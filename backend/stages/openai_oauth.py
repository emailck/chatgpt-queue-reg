"""OpenAI OAuth refresh-token stage."""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.errors import JobCancelled
from backend.core.job_context import JobContext
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.schemas.stage_io import OpenAIOAuthInput, OpenAIOAuthOutput


@stage(
    name="openai_oauth",
    requires_resources=[],
    optional_resources=["sms_pool", "proxy_pool"],
    default_concurrency=3,
    input_schema=OpenAIOAuthInput,
    output_schema=OpenAIOAuthOutput,
    description="Run OpenAI OAuth PKCE on a registered account and obtain RT.",
)
def run(ctx: JobContext) -> None:
    account_id = ctx.account_id or int(ctx.input.get("account_id") or 0) or None
    if not account_id:
        raise RuntimeError("openai_oauth stage requires account_id")
    ctx.attach_account(account_id)

    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    pool_config = _workpool_config("workpool.openai_oauth.")
    merged_extra = {**settings.get_all(), **pool_config, **extra_config}

    with Session(engine) as s:
        account_row = s.get(ChatGPTAccount, account_id)
        if account_row is None:
            raise RuntimeError(f"account {account_id} not found")
        email = str(account_row.email or "")
        password = str(account_row.password or "")
        account_user_agent = str(account_row.user_agent or "")

    if not email:
        raise RuntimeError(f"account {account_id} has no email")

    proxy_url = ctx.effective_proxy_url() or ""

    ctx.log(
        "starting openai_oauth stage",
        payload={
            "account_id": account_id,
            "email": email,
            "proxy_provided": bool(proxy_url),
        },
    )

    def _emit_log(message: str, level: str = "info") -> None:
        ctx.log(str(message or ""), level=level)
        try:
            ctx.check_cancelled()
        except JobCancelled:
            raise

    oauth_timeout = _read_int_config(
        merged_extra,
        "chatgpt_oauth_otp_wait_seconds",
        fallback_keys=("chatgpt_register_otp_wait_seconds", "chatgpt_otp_wait_seconds"),
        default=300,
        minimum=30,
        maximum=3600,
    )
    oauth_max_retries = _read_int_config(
        merged_extra,
        "chatgpt_oauth_max_retries",
        default=2,
        minimum=1,
        maximum=5,
    )

    from backend.integrations.chatgpt.oauth import create_oauth_session
    from backend.integrations.chatgpt.oauth_protocol import (
        OAuthOtpAdapter,
        run_protocol_oauth,
    )

    email_service = _resolve_email_service({
        "fixed_email": email or None,
        "fixed_password": password or None,
    })

    last_error = ""
    token_data: dict[str, Any] | None = None
    for attempt in range(oauth_max_retries):
        if attempt:
            _emit_log(f"OAuth RT 获取重试 {attempt + 1}/{oauth_max_retries} ...")
            time.sleep(1)
        try:
            _emit_log("OAuth RT: 创建 PKCE authorize session")
            oauth = create_oauth_session(merged_extra)
            otp_provider = OAuthOtpAdapter(
                email_service,
                log_fn=_emit_log,
                timeout_seconds=oauth_timeout,
            )
            identity = ctx.require_identity()
            token_data = run_protocol_oauth(
                oauth,
                email=email,
                password=password,
                otp_provider=otp_provider,
                phone_provider=_build_phone_provider(merged_extra, _emit_log, proxy_url),
                config={
                    **merged_extra,
                    "user_agent": account_user_agent,
                    "browser_fingerprint": identity.fingerprint,
                    "cookies": identity.cookies,
                },
                proxy=proxy_url or "",
                log_fn=_emit_log,
            )
            refresh_token = str((token_data or {}).get("refresh_token") or "").strip()
            if not refresh_token:
                raise RuntimeError("OAuth token response missing refresh_token")
            _emit_log("OAuth refresh_token 获取完成")
            break
        except Exception as exc:
            last_error = str(exc)
            token_data = None
            if attempt < oauth_max_retries - 1:
                _emit_log(f"OAuth RT 获取失败，准备重试: {last_error}", level="warning")
                continue

    if not token_data:
        _persist_refresh_token_error(account_id, last_error)
        raise RuntimeError(f"OAuth 获取 refresh_token 失败: {last_error}")

    token_id = _persist_refresh_token(account_id, token_data)
    expires_in = int(token_data.get("expires_in") or 3600)
    ctx.update_result({
        "account_id": account_id,
        "refresh_token_id": token_id,
        "has_refresh_token": True,
        "expires_in": expires_in,
        "sub2api_status": "pending_sync",
    })
    ctx.log(
        "openai_oauth succeeded",
        payload={"account_id": account_id, "refresh_token_id": token_id, "expires_in": expires_in},
    )


# ---- helpers ---------------------------------------------------------------


def _workpool_config(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in settings.get_all().items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
    return out


def _resolve_email_service(extra_config: dict[str, Any]):
    from backend.integrations.mail.email_service import MicrosoftEmailService

    return MicrosoftEmailService(extra_config=extra_config)


def _build_phone_provider(extra_config: dict[str, Any], log_fn, proxy_url: str):
    try:
        from backend.integrations.chatgpt.phone_service import build_phone_provider

        provider = build_phone_provider(
            extra_config,
            log_fn=log_fn,
            proxy_url=proxy_url or "",
        )
    except Exception as exc:
        raise RuntimeError(f"初始化 OAuth 手机接码失败: {exc}") from exc
    if provider:
        log_fn(f"OAuth 手机接码已开启: provider={provider.provider_name}")
    return provider


def _read_int_config(
    values: dict[str, Any],
    primary_key: str,
    *,
    fallback_keys: tuple[str, ...] = (),
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    keys = (primary_key, *tuple(fallback_keys or ()))
    for key in keys:
        if key not in values:
            continue
        try:
            parsed = int(values.get(key))
        except Exception:
            continue
        return max(minimum, min(parsed, maximum))
    return max(minimum, min(int(default), maximum))


def _persist_refresh_token(account_id: int, token_data: dict[str, Any]) -> int:
    rt = str(token_data.get("refresh_token") or "").strip()
    oauth_at = str(token_data.get("access_token") or "").strip()
    oauth_id_token = str(token_data.get("id_token") or "").strip()
    expires_in = int(token_data.get("expires_in") or 3600)
    now = utcnow()

    with session_scope() as s:
        from sqlalchemy import select as sa_select

        existing = s.exec(
            sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == int(account_id))
        ).scalars().first()

        if existing is None:
            row = OpenAIRefreshToken(
                account_id=int(account_id),
                refresh_token=rt,
                oauth_access_token=oauth_at,
                oauth_id_token=oauth_id_token,
                oauth_access_expires_at=now + timedelta(seconds=expires_in),
                next_sync_at=now,
                last_sync_at=None,
                consecutive_failures=0,
                enabled=True,
                last_error="",
                sub2api_status="pending_upload",
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return int(row.id or 0)

        existing.refresh_token = rt or existing.refresh_token
        if oauth_at:
            existing.oauth_access_token = oauth_at
        if oauth_id_token:
            existing.oauth_id_token = oauth_id_token
        existing.oauth_access_expires_at = now + timedelta(seconds=expires_in)
        existing.next_sync_at = now
        existing.last_sync_at = None
        existing.consecutive_failures = 0
        existing.enabled = True
        existing.last_error = ""
        existing.sub2api_status = "pending_upload"
        existing.updated_at = now
        s.add(existing)
        s.commit()
        s.refresh(existing)
        return int(existing.id or 0)


def _persist_refresh_token_error(account_id: int, error: str) -> None:
    if not account_id:
        return
    now = utcnow()
    with session_scope() as s:
        from sqlalchemy import select as sa_select

        existing = s.exec(
            sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == int(account_id))
        ).scalars().first()
        if existing is None:
            return
        existing.last_error = str(error or "")
        existing.consecutive_failures = int(existing.consecutive_failures or 0) + 1
        existing.updated_at = now
        s.add(existing)
