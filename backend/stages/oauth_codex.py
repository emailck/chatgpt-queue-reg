"""oauth_codex stage.

Runs the OpenAI OAuth PKCE flow against an *already registered* ChatGPT
account to obtain a Codex `refresh_token` (+ `access_token` / `id_token`),
then upserts a local mirror row and uploads the RT to sub2api. sub2api owns
RT rotation.

Identity (proxy / UA / cookies / fingerprint) comes from the bound
`chatgpt_accounts` row — see ARCHITECTURE.md §4.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Optional

from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.errors import JobCancelled
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps, json_loads
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.codex_token import CodexToken
from backend.schemas.stage_io import OAuthCodexInput, OAuthCodexOutput


PROBE_INTERVAL_HOURS = 24


@stage(
    name="oauth_codex",
    requires_resources=[],
    optional_resources=["sms_pool", "proxy_pool"],
    default_concurrency=3,
    input_schema=OAuthCodexInput,
    output_schema=OAuthCodexOutput,
    description="Run OpenAI OAuth PKCE on a registered account, obtain Codex RT/AT.",
)
def run(ctx: JobContext) -> None:
    account_id = ctx.account_id or int(ctx.input.get("account_id") or 0) or None
    if not account_id:
        raise RuntimeError("oauth_codex stage requires account_id")
    ctx.attach_account(account_id)

    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    merged_extra = {**settings.get_all(), **extra_config}

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
        "starting oauth_codex stage",
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

    # Lazy import — pulls heavy modules.
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
    upload_result = _upload_refresh_token_to_sub2api(token_id, account_id, token_data, proxy_url=proxy_url)

    expires_in = int(token_data.get("expires_in") or 3600)
    ctx.update_result({
        "account_id": account_id,
        "codex_token_id": token_id,
        "codex_rt": str(token_data.get("refresh_token") or ""),
        "codex_at": str(token_data.get("access_token") or ""),
        "expires_in": expires_in,
        "sub2api_status": upload_result.get("status", ""),
        "sub2api_external_id": upload_result.get("external_id", ""),
    })
    ctx.log(
        "oauth_codex succeeded",
        payload={"account_id": account_id, "expires_in": expires_in, "sub2api_status": upload_result.get("status", "")},
    )


# ---- helpers ---------------------------------------------------------------


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
    """Upsert a `codex_tokens` row for `account_id` from a fresh token response.

    Sets RT/AT/id_token, expires_at = now + expires_in, schedules immediate
    sub2api upload/sync, and resets failure counters.
    """
    rt = str(token_data.get("refresh_token") or "").strip()
    at = str(token_data.get("access_token") or "").strip()
    id_token = str(token_data.get("id_token") or "").strip()
    expires_in = int(token_data.get("expires_in") or 3600)
    now = utcnow()

    with session_scope() as s:
        from sqlalchemy import select as sa_select

        existing = s.exec(
            sa_select(CodexToken).where(CodexToken.account_id == int(account_id))
        ).scalars().first()

        if existing is None:
            row = CodexToken(
                account_id=int(account_id),
                refresh_token=rt,
                access_token=at,
                id_token=id_token,
                expires_at=now + timedelta(seconds=expires_in),
                next_refresh_at=now,
                last_refreshed_at=None,
                consecutive_failures=0,
                alive=True,
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
        if at:
            existing.access_token = at
        if id_token:
            existing.id_token = id_token
        existing.expires_at = now + timedelta(seconds=expires_in)
        existing.next_refresh_at = now
        existing.last_refreshed_at = None
        existing.consecutive_failures = 0
        existing.alive = True
        existing.last_error = ""
        existing.sub2api_status = "pending_upload"
        existing.updated_at = now
        s.add(existing)
        s.commit()
        s.refresh(existing)
        return int(existing.id or 0)


def _upload_refresh_token_to_sub2api(token_id: int, account_id: int, token_data: dict[str, Any], *, proxy_url: str) -> dict[str, str]:
    from backend.integrations.sub2api import Sub2ApiNotConfigured, get_sub2api_client

    rt = str(token_data.get("refresh_token") or "").strip()
    if not rt:
        return {"status": "missing_refresh_token", "external_id": ""}
    now = utcnow()
    try:
        resp = get_sub2api_client().upload_codex_token(
            account_id=int(account_id),
            refresh_token=rt,
            access_token=str(token_data.get("access_token") or ""),
            id_token=str(token_data.get("id_token") or ""),
            expires_at=(now + timedelta(seconds=int(token_data.get("expires_in") or 3600))).isoformat(),
            proxy_url=proxy_url or "",
            metadata={"local_codex_token_id": token_id},
        )
        external_id = _extract_sub2api_external_id(resp) or str(resp.get("id") or resp.get("token_id") or "")
        status = str(resp.get("status") or resp.get("state") or "uploaded")
        with session_scope() as s:
            row = s.get(CodexToken, token_id)
            if row is not None:
                row.sub2api_external_id = external_id
                row.sub2api_status = status
                row.sub2api_payload_json = json_dumps(resp)
                row.uploaded_at = now
                row.status_checked_at = now
                row.next_refresh_at = now + timedelta(hours=PROBE_INTERVAL_HOURS)
                row.last_error = ""
                row.updated_at = now
                s.add(row)
        return {"status": status, "external_id": external_id}
    except Sub2ApiNotConfigured as exc:
        _mark_sub2api_upload_failed(token_id, str(exc), status="pending_upload")
        return {"status": "pending_upload", "external_id": ""}
    except Exception as exc:
        _mark_sub2api_upload_failed(token_id, str(exc), status="upload_failed")
        return {"status": "upload_failed", "external_id": ""}


def _extract_sub2api_external_id(resp: dict[str, Any]) -> str:
    for key in ("external_id", "sub2api_id", "id", "token_id"):
        value = resp.get(key)
        if value not in (None, ""):
            return str(value)
    data = resp.get("data")
    if isinstance(data, dict):
        return _extract_sub2api_external_id(data)
    return ""


def _mark_sub2api_upload_failed(token_id: int, error: str, *, status: str) -> None:
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            return
        row.sub2api_status = status
        row.last_error = error
        row.status_checked_at = utcnow()
        row.updated_at = utcnow()
        s.add(row)


def _persist_refresh_token_error(account_id: int, error: str) -> None:
    """Record an OAuth-codex failure on the existing `codex_tokens` row, if any.

    We only update an existing row here; we don't create one with empty RT
    because the row's `refresh_token` is mandatory for `rt_keepalive` to do
    anything useful.
    """
    if not account_id:
        return
    now = utcnow()
    with session_scope() as s:
        from sqlalchemy import select as sa_select

        existing = s.exec(
            sa_select(CodexToken).where(CodexToken.account_id == int(account_id))
        ).scalars().first()
        if existing is None:
            return
        existing.last_error = str(error or "")
        existing.consecutive_failures = int(existing.consecutive_failures or 0) + 1
        existing.updated_at = now
        s.add(existing)
