"""ChatGPT password setup stage."""
from __future__ import annotations

from typing import Any

from backend.core.db import session_scope
from backend.core.proxy import resolve_workpool_proxy_template
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.core.json_utils import json_dumps, json_loads
from backend.integrations.chatgpt.mfa_client import build_totp_adapter_from_metadata
from backend.models.account import ChatGPTAccount
from backend.schemas.stage_io import ChatGPTPasswordSetupInput, ChatGPTPasswordSetupOutput


@stage(
    name="chatgpt_password_setup",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=ChatGPTPasswordSetupInput,
    output_schema=ChatGPTPasswordSetupOutput,
    description="Set or update the password for an existing ChatGPT account after email OTP verification.",
)
def run(ctx) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.chatgpt_password_setup."), **extra_config}

    account_id = _to_int(payload.get("account_id") or ctx.account_id)
    if not account_id:
        raise RuntimeError("chatgpt_password_setup requires account_id")
    ctx.attach_account(account_id)

    with session_scope() as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        email = str(account.email or "").strip()
        password = str(payload.get("password") or account.password or "").strip()
        access_token = str(account.access_token or payload.get("access_token") or "").strip()
        account_proxy_url = str(account.proxy_url or "").strip()
        account_proxy_id = int(account.proxy_id or 0) or None
        account_password = str(account.password or "").strip()
        account_metadata = json_loads(account.metadata_json, fallback={}) or {}

    if not email:
        raise RuntimeError(f"account {account_id} has no email")
    if not password:
        raise RuntimeError(
            f"account {account_id} has no password; pass one in stage input or set ChatGPTAccount.password first"
        )

    otp_wait_timeout = _coerce_int(
        payload.get("otp_wait_timeout") or config.get("otp_wait_timeout_seconds"),
        default=300,
        minimum=30,
        maximum=3600,
    )
    otp_resend_wait_timeout = _coerce_int(
        payload.get("otp_resend_wait_timeout") or config.get("otp_resend_wait_timeout_seconds"),
        default=300,
        minimum=30,
        maximum=3600,
    )
    max_steps = _coerce_int(
        payload.get("max_steps") or config.get("max_steps"),
        default=16,
        minimum=3,
        maximum=50,
    )

    proxy_url = (
        ctx.effective_proxy_url()
        or str(payload.get("proxy_url") or "").strip()
        or str(config.get("proxy_url") or "").strip()
        or account_proxy_url
    )
    if not proxy_url:
        rendered = resolve_workpool_proxy_template("chatgpt_password_setup", payload=payload, extra=config)
        if rendered is not None and rendered.url:
            proxy_url = rendered.url
            ctx.attach_proxy(proxy_id=None, proxy_url=proxy_url)
            ctx.log(
                "chatgpt_password_setup dynamic proxy rendered",
                payload={"provider": rendered.provider, "region": rendered.region, "ttl": rendered.ttl, "sid": rendered.sid},
            )
    elif account_proxy_id:
        ctx.attach_proxy(proxy_id=account_proxy_id, proxy_url=proxy_url)
    elif proxy_url and not ctx.proxy_url:
        ctx.attach_proxy(proxy_id=None, proxy_url=proxy_url)

    ctx.log(
        "starting chatgpt_password_setup",
        payload={
            "account_id": account_id,
            "email": email,
            "proxy_provided": bool(proxy_url),
            "password_present": bool(password),
            "otp_wait_timeout": otp_wait_timeout,
            "otp_resend_wait_timeout": otp_resend_wait_timeout,
            "max_steps": max_steps,
        },
    )

    from backend.integrations.mail.email_service import MicrosoftEmailService
    from backend.stages.chatgpt_session import _build_client_from_account, refresh_or_relogin_account_session

    with session_scope() as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        client = _build_client_from_account(account, ctx, proxy_url_override=proxy_url)
    email_service = MicrosoftEmailService(extra_config={"fixed_email": email})
    mfa_provider = build_totp_adapter_from_metadata(
        account_metadata,
        config=config,
        log_fn=lambda msg: ctx.log(str(msg or "")),
        timeout_seconds=otp_wait_timeout,
    )

    ok, result = client.setup_password_existing_user(
        email=email,
        password=password,
        otp_provider=email_service,
        mfa_provider=mfa_provider,
        access_token=access_token,
        max_steps=max_steps,
        otp_wait_timeout=otp_wait_timeout,
        otp_resend_wait_timeout=otp_resend_wait_timeout,
    )
    if not ok:
        raise RuntimeError(f"chatgpt_password_setup failed: {result}")

    session_snapshot = refresh_or_relogin_account_session(
        account_id,
        ctx,
        password=password,
        otp_provider=email_service,
        mfa_provider=mfa_provider,
        proxy_url_override=proxy_url,
        max_attempts=5,
    )
    _persist_password_setup_metadata(account_id, password)
    ctx.update_result(
        {
            "account_id": account_id,
            "email": email,
            "password_set": True,
            "email_otp_verified": True,
            "session_refresh_status": "password_setup_refreshed",
            "access_token_refreshed": True,
            "chatgpt_account_id": session_snapshot.get("chatgpt_account_id", ""),
            "plan_type": session_snapshot.get("plan_type", ""),
            "password_page_url": str(getattr(client.last_registration_state, "current_url", "") or ""),
            "proxy_id": ctx.proxy_id,
            "proxy_url": ctx.proxy_url or proxy_url,
        }
    )
    ctx.log(
        "chatgpt_password_setup completed",
        payload={"account_id": account_id, "email": email, "password_changed": password != account_password},
    )


def _persist_password_setup_metadata(account_id: int, password: str) -> None:
    now = utcnow()
    with session_scope() as s:
        row = s.get(ChatGPTAccount, int(account_id))
        if row is None:
            return
        row.password = str(password or row.password or "")
        metadata = json_loads(row.metadata_json, fallback={}) or {}
        metadata["password_setup_at"] = now.isoformat()
        row.metadata_json = json_dumps(metadata)
        row.updated_at = now
        s.add(row)
        s.commit()


def _workpool_config(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in settings.get_all().items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
    return out


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
