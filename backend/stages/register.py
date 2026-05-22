"""register stage.

Runs the access-token-only ChatGPT registration engine and writes the resulting
account/session metadata into the new `chatgpt_accounts` table.

Email module integration for now is the only-microsoft variant: when the
caller wants the registrar to source a fresh email, we expect the Microsoft
import to have populated `email_accounts` and we let the engine pull a free
mailbox via the bundled adapter.
"""
from __future__ import annotations

import secrets
import string
import threading
from datetime import datetime, timezone
from typing import Any

from backend.core.constants import (
    ACCOUNT_STATUS_FAILED,
    ACCOUNT_STATUS_REGISTERED,
)
from backend.core.db import session_scope
from backend.core.email_domain_policy import validate_email_domain_policy
from backend.core.errors import JobCancelled
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps, json_loads
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.proxy import Proxy
from backend.schemas.stage_io import RegisterInput, RegisterOutput


@stage(
    name="register",
    requires_resources=["email_pool"],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=RegisterInput,
    output_schema=RegisterOutput,
    description="Register a ChatGPT account; binds identity (proxy/UA/cookies/fp).",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    requested_email = str(payload.get("email") or "").strip()
    password_from_input = bool(str(payload.get("password") or "").strip())
    requested_password = str(payload.get("password") or "").strip() or _generate_register_password()
    proxy_url = ctx.proxy_url or str(payload.get("proxy_url") or "").strip()
    proxy_id = ctx.proxy_id or int(payload.get("proxy_id") or 0) or None
    proxy_region = str(
        payload.get("proxy_region")
        or payload.get("region")
        or settings.get("workpool.register.proxy_region", "")
        or ""
    ).strip()
    if not proxy_url:
        proxy_resource = ctx.acquire(
            "proxy_pool",
            hint={"stage": "register", "proxy_id": proxy_id, "region": proxy_region},
        )
        proxy_payload = proxy_resource.payload or {}
        proxy_url = str(proxy_payload.get("url") or proxy_resource.id or "").strip()
        proxy_id = int(proxy_payload.get("proxy_id") or 0) or proxy_id
        proxy_region = str(proxy_payload.get("region") or proxy_region or "")
    elif not proxy_id:
        proxy_id, proxy_region = _resolve_proxy_identity(proxy_url)

    if not proxy_id or not proxy_url:
        raise RuntimeError("register stage requires a bound proxy_id and proxy_url")
    ctx.attach_proxy(proxy_id=proxy_id, proxy_url=proxy_url)
    extra_config = dict(payload.get("extra_config") or {})
    also_record_to_at_pool = bool(
        ctx.input.get(
            "also_record_to_at_pool",
            settings.get_bool("workpool.register.also_record_to_at_pool", False),
        )
    )

    # Soft validation up front — the engine repeats it deeper, but failing
    # fast keeps the queue clean.
    if requested_email:
        try:
            validate_email_domain_policy(requested_email, settings.get_all())
        except ValueError as exc:
            ctx.log(f"email_domain_policy 拒绝: {exc}", level="error")
            raise

    ctx.log(
        "starting ChatGPT register stage",
        payload={
            "email_provided": bool(requested_email),
            "password_provided": password_from_input,
            "password_generated": not password_from_input,
            "proxy_provided": bool(proxy_url),
            "proxy_id": proxy_id,
            "proxy_region": proxy_region,
            "also_record_to_at_pool": also_record_to_at_pool,
        },
    )

    # Lazy import: the engine pulls in heavy modules (curl_cffi, playwright).
    from backend.integrations.chatgpt.access_token_only_registration_engine import (
        AccessTokenOnlyRegistrationEngine,
    )
    from backend.integrations.chatgpt.registration_result import RegistrationResult

    cancel_event = threading.Event()

    def _checkpoint() -> None:
        try:
            ctx.check_cancelled()
        except JobCancelled:
            cancel_event.set()
            raise

    def _emit_log(message: str, level: str = "info") -> None:
        if cancel_event.is_set():
            return
        ctx.log(str(message or ""), level=level)
        _checkpoint()

    merged_extra = {**settings.get_all(), **extra_config}
    if requested_email:
        merged_extra["chatgpt_register_fixed_email"] = requested_email
    if requested_password:
        merged_extra["chatgpt_register_fixed_password"] = requested_password

    email_service = _resolve_email_service({
        "fixed_email": requested_email or None,
        "fixed_password": requested_password or None,
    })
    register_max_retries = _read_int_config(
        merged_extra,
        "register_max_retries",
        default=3,
        minimum=1,
        maximum=10,
    )

    engine_obj = AccessTokenOnlyRegistrationEngine(
        email_service=email_service,
        proxy_url=proxy_url or None,
        browser_mode=str(merged_extra.get("chatgpt_browser_mode") or "protocol"),
        callback_logger=_emit_log,
        max_retries=register_max_retries,
        extra_config=merged_extra,
    )
    if requested_email:
        engine_obj.email = requested_email
    if requested_password:
        engine_obj.password = requested_password
    ctx.log("ChatGPT registration mode: access_token_only")

    try:
        result: RegistrationResult = engine_obj.run()
    except Exception:
        _release_email_back_to_pool(email_service, ctx, reason="register exception")
        raise
    _checkpoint()

    account_id = _persist_account(result, proxy_id=proxy_id, proxy_url=proxy_url)
    if account_id:
        ctx.attach_account(account_id)
        ctx.update_result({
            "account_id": account_id,
            "email": result.email,
            "email_address": result.email,
            "proxy_id": proxy_id,
            "proxy_url": proxy_url or "",
            "registered_account_id": result.account_id,
            "workspace_id": result.workspace_id,
            "source": result.source,
        })

    if not result.success:
        if _result_consumed_email(result):
            _consume_email(email_service, ctx, note="registered_before_failure")
        else:
            _release_email_back_to_pool(email_service, ctx, reason=result.error_message or "register failed")
        ctx.log(f"register failed: {result.error_message}", level="error")
        raise RuntimeError(result.error_message or "register failed")

    _consume_email(email_service, ctx, note="registered")

    # If the caller asked us to also stash a row in the AT pool (e.g. the
    # pipeline only runs `register` and wants the AT retrievable independently),
    # do so now. The legacy `_is_register_only_pipeline` gating is gone:
    # callers express intent via `also_record_to_at_pool` in stage_input.
    if account_id and also_record_to_at_pool:
        at_id = _persist_access_token_account(
            result,
            proxy_id=proxy_id,
            proxy_url=proxy_url,
            chatgpt_account_id=account_id,
            pipeline_id=ctx.pipeline_id,
        )
        if at_id:
            ctx.update_result({"access_token_account_id": at_id})
            ctx.log(f"access_token_accounts row created id={at_id}")

    ctx.log("register succeeded", payload={"account_id": account_id})


# ---- helpers ---------------------------------------------------------------


def _generate_register_password() -> str:
    length = secrets.randbelow(7) + 12
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(ch.islower() for ch in password)
            and any(ch.isupper() for ch in password)
            and any(ch.isdigit() for ch in password)
            and any(ch in "!@#$%^&*_-+=" for ch in password)
        ):
            return password


def _read_int_config(
    values: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(values.get(key))
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _result_consumed_email(result) -> bool:
    metadata = dict(getattr(result, "metadata", None) or {})
    if metadata.get("mailbox_account_consumed"):
        return True
    error = str(getattr(result, "error_message", None) or "").lower()
    return "user_already_exists" in error


def _free_pool_note(reason: str) -> str:
    text = str(reason or "").strip()
    return text or "released"


def _release_email_back_to_pool(email_service, ctx, *, reason: str) -> None:
    target = getattr(email_service, "claimed_email", None) or ""
    if not target:
        return
    try:
        from backend.integrations.mail.pool import requeue

        ok = requeue(email=str(target))
        if ok:
            ctx.log(f"邮箱已退回池: {target} ({_free_pool_note(reason)})", level="warning")
    except Exception as exc:
        ctx.log(f"邮箱退回池失败: {target} -> {exc}", level="error")


def _consume_email(email_service, ctx, *, note: str) -> None:
    target = getattr(email_service, "claimed_email", None) or ""
    if not target:
        return
    try:
        from backend.integrations.mail.pool import mark_consumed

        if mark_consumed(email=str(target), note=note):
            ctx.log(f"邮箱已标记消费: {target} ({note})")
    except Exception as exc:
        ctx.log(f"邮箱标记消费失败: {target} -> {exc}", level="warning")


def _resolve_proxy_identity(proxy_url: str) -> tuple[int | None, str]:
    if not proxy_url:
        return None, ""
    with session_scope() as s:
        from sqlalchemy import select as sa_select

        row = s.exec(sa_select(Proxy).where(Proxy.url == proxy_url)).scalars().first()
        if row is None:
            return None, ""
        return int(row.id or 0) or None, str(row.region or "")


def _resolve_email_service(extra_config: dict[str, Any]):
    """Return the email service the legacy engine expects.

    For now we only support Microsoft.  We adapt our `EmailAccount` table
    behind the legacy `EmailService`-shaped duck-type the engine consumes.
    """
    from backend.integrations.mail.email_service import MicrosoftEmailService

    return MicrosoftEmailService(extra_config=extra_config)


def _persist_account(result, *, proxy_id: int | None, proxy_url: str) -> int | None:
    metadata = dict(getattr(result, "metadata", None) or {})
    user_agent = str(metadata.get("user_agent") or "")
    cookies = metadata.get("cookies") or metadata.get("cookies_json") or []
    if isinstance(cookies, str):
        cookies = json_loads(cookies, fallback=[])
    local_storage = metadata.get("local_storage") or {}
    if isinstance(local_storage, str):
        local_storage = json_loads(local_storage, fallback={})
    fingerprint = metadata.get("browser_fingerprint") or {}
    if isinstance(fingerprint, str):
        fingerprint = json_loads(fingerprint, fallback={})
    if not user_agent and isinstance(metadata.get("oai_client_version"), str):
        user_agent = ""

    status = ACCOUNT_STATUS_REGISTERED if result.success else ACCOUNT_STATUS_FAILED
    raw_account = metadata.get("account") if isinstance(metadata.get("account"), dict) else {}
    session_expires_at = _parse_datetime(metadata.get("expires"))
    plan_type = str(raw_account.get("planType") or raw_account.get("plan_type") or "")

    with session_scope() as s:
        account = ChatGPTAccount(
            email=result.email or "",
            password=result.password or "",
            status=status,
            account_id=result.account_id or "",
            workspace_id=result.workspace_id or "",
            access_token=result.access_token or "",
            refresh_token="",
            id_token=result.id_token or "",
            session_token=result.session_token or "",
            session_expires_at=session_expires_at,
            session_refresh_status="current" if result.success and result.access_token else "",
            last_session_refresh_at=utcnow() if result.success and result.access_token else None,
            plan_type=plan_type,
            cookies_json=json_dumps(cookies or []),
            local_storage_json=json_dumps(local_storage or {}),
            browser_fingerprint_json=json_dumps(fingerprint or {}),
            user_agent=user_agent,
            proxy_id=proxy_id,
            proxy_url=proxy_url or "",
            last_error=result.error_message or "",
            registered_at=utcnow() if result.success else None,
            metadata_json=json_dumps(metadata),
        )
        s.add(account)
        s.commit()
        s.refresh(account)
        return int(account.id or 0)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _persist_access_token_account(
    result,
    *,
    proxy_id: int | None,
    proxy_url: str,
    chatgpt_account_id: int | None,
    pipeline_id: int | None,
) -> int | None:
    if not result.success or not (result.access_token or "").strip():
        return None
    metadata = dict(getattr(result, "metadata", None) or {})
    cookies = metadata.get("cookies") or metadata.get("cookies_json") or []
    if isinstance(cookies, str):
        cookies = json_loads(cookies, fallback=[])
    local_storage = metadata.get("local_storage") or {}
    if isinstance(local_storage, str):
        local_storage = json_loads(local_storage, fallback={})
    fingerprint = metadata.get("browser_fingerprint") or {}
    if isinstance(fingerprint, str):
        fingerprint = json_loads(fingerprint, fallback={})
    user_agent = str(metadata.get("user_agent") or "")

    from backend.models.access_token import AccessTokenAccount

    with session_scope() as s:
        row = AccessTokenAccount(
            pipeline_id=pipeline_id,
            chatgpt_account_id=chatgpt_account_id,
            email=result.email or "",
            password=result.password or "",
            account_id=result.account_id or "",
            workspace_id=result.workspace_id or "",
            access_token=result.access_token or "",
            refresh_token="",
            id_token=result.id_token or "",
            session_token=result.session_token or "",
            cookies_json=json_dumps(cookies or []),
            local_storage_json=json_dumps(local_storage or {}),
            browser_fingerprint_json=json_dumps(fingerprint or {}),
            user_agent=user_agent,
            proxy_id=proxy_id,
            proxy_url=proxy_url or "",
            metadata_json=json_dumps(metadata),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return int(row.id or 0)
