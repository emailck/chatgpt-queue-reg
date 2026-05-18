"""rt_keepalive stage backed by sub2api.

sub2api owns Codex RT rotation. This stage only synchronizes the local
`codex_tokens` mirror with sub2api: upload pending RTs, then pull remote status.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.integrations.sub2api import Sub2ApiNotConfigured, get_sub2api_client
from backend.models.codex_token import CodexToken
from backend.schemas.stage_io import RtKeepaliveInput, RtKeepaliveOutput

STATUS_SYNC_INTERVAL_HOURS = 24
FAILURE_BACKOFF_HOURS = 1


@stage(
    name="rt_keepalive",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=5,
    input_schema=RtKeepaliveInput,
    output_schema=RtKeepaliveOutput,
    description="Sync Codex RT pool status with sub2api; sub2api owns RT rotation.",
)
def run(ctx: JobContext) -> None:
    account_id = int(ctx.account_id or ctx.input.get("account_id") or 0)
    token_id = int(ctx.input.get("codex_token_id") or 0)
    if not account_id and not token_id:
        raise RuntimeError("rt_keepalive requires account_id or codex_token_id")
    if account_id:
        ctx.attach_account(account_id)

    row = _load_token_row(token_id=token_id, account_id=account_id)
    if row is None:
        raise RuntimeError("codex_tokens row not found")
    token_id = int(row.id or 0)
    account_id = int(row.account_id or account_id or 0)
    if account_id:
        ctx.attach_account(account_id)

    if not row.refresh_token:
        raise RuntimeError(f"codex_tokens row {token_id} missing refresh_token")

    ctx.log(
        "syncing Codex RT with sub2api",
        payload={
            "account_id": account_id,
            "codex_token_id": token_id,
            "sub2api_external_id": row.sub2api_external_id,
            "sub2api_status": row.sub2api_status,
        },
    )

    try:
        sync_result = _sync_with_sub2api(row, proxy_url=ctx.effective_proxy_url() or "")
    except Sub2ApiNotConfigured as exc:
        _record_sync_pending(token_id, str(exc), status="pending_upload")
        ctx.log(f"sub2api not configured: {exc}", level="warning")
        ctx.update_result({"account_id": account_id, "codex_token_id": token_id, "sub2api_status": "pending_upload"})
        return
    except Exception as exc:
        _record_sync_failure(token_id, str(exc), status="sync_failed")
        ctx.log(f"sub2api sync failed: {exc}", level="error")
        raise

    ctx.update_result({
        "account_id": account_id,
        "codex_token_id": token_id,
        "sub2api_external_id": sync_result.get("external_id", ""),
        "sub2api_status": sync_result.get("status", ""),
    })
    ctx.log("sub2api RT sync ok", payload=sync_result)


def _load_token_row(*, token_id: int, account_id: int) -> CodexToken | None:
    with Session(engine) as s:
        if token_id:
            return s.get(CodexToken, token_id)
        from sqlalchemy import select as sa_select

        return s.exec(sa_select(CodexToken).where(CodexToken.account_id == account_id)).scalars().first()


def _sync_with_sub2api(row: CodexToken, *, proxy_url: str) -> dict[str, str]:
    client = get_sub2api_client()
    external_id = str(row.sub2api_external_id or "")
    now = utcnow()

    if not external_id or row.sub2api_status in {"pending_upload", "upload_failed", "sync_failed"}:
        uploaded = client.upload_codex_token(
            account_id=int(row.account_id),
            refresh_token=row.refresh_token,
            access_token=row.access_token,
            id_token=row.id_token,
            expires_at=row.expires_at.isoformat() if row.expires_at else "",
            proxy_url=proxy_url or "",
            metadata={"local_codex_token_id": row.id},
        )
        external_id = _extract_external_id(uploaded) or external_id
        status = str(uploaded.get("status") or uploaded.get("state") or "uploaded")
        _record_sync_success(row.id or 0, external_id=external_id, status=status, payload=uploaded, checked_at=now)
        return {"external_id": external_id, "status": status}

    status_payload = client.get_codex_token_status(external_id=external_id)
    status = str(status_payload.get("status") or status_payload.get("state") or "unknown")
    alive = status.lower() not in {"dead", "disabled", "banned", "invalid", "expired"}
    _record_sync_success(
        row.id or 0,
        external_id=external_id,
        status=status,
        payload=status_payload,
        checked_at=now,
        alive=alive,
    )
    return {"external_id": external_id, "status": status}


def _extract_external_id(resp: dict[str, Any]) -> str:
    for key in ("external_id", "sub2api_id", "id", "token_id"):
        value = resp.get(key)
        if value not in (None, ""):
            return str(value)
    data = resp.get("data")
    if isinstance(data, dict):
        return _extract_external_id(data)
    return ""


def _record_sync_success(
    token_id: int,
    *,
    external_id: str,
    status: str,
    payload: dict[str, Any],
    checked_at,
    alive: bool = True,
) -> None:
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            return
        row.sub2api_external_id = external_id
        row.sub2api_status = status
        row.sub2api_payload_json = json_dumps(payload)
        row.status_checked_at = checked_at
        if row.uploaded_at is None and external_id:
            row.uploaded_at = checked_at
        row.next_refresh_at = checked_at + timedelta(hours=STATUS_SYNC_INTERVAL_HOURS)
        row.last_refreshed_at = checked_at
        row.consecutive_failures = 0
        row.alive = alive
        row.last_error = ""
        row.updated_at = checked_at
        s.add(row)


def _record_sync_pending(token_id: int, error: str, *, status: str) -> None:
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            return
        now = utcnow()
        row.sub2api_status = status
        row.last_error = error
        row.status_checked_at = now
        row.next_refresh_at = now + timedelta(hours=STATUS_SYNC_INTERVAL_HOURS)
        row.updated_at = now
        s.add(row)


def _record_sync_failure(token_id: int, error: str, *, status: str) -> None:
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            return
        row.sub2api_status = status
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
        row.last_error = error
        row.status_checked_at = utcnow()
        row.next_refresh_at = utcnow() + timedelta(hours=FAILURE_BACKOFF_HOURS)
        row.updated_at = utcnow()
        s.add(row)
