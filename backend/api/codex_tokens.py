"""Codex refresh-token APIs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.codex_token import CodexToken

router = APIRouter()


@router.get("/api/codex-tokens", tags=["codex-tokens"])
def list_codex_tokens(account_id: Optional[int] = None, limit: int = Query(500, ge=1, le=1000)):
    with Session(engine) as s:
        stmt = sa_select(CodexToken)
        if account_id is not None:
            stmt = stmt.where(CodexToken.account_id == account_id)
        rows = list(s.exec(stmt.order_by(CodexToken.id.desc()).limit(limit)).scalars())
    return [_codex_token_to_dict(row) for row in rows]


@router.post("/api/codex-tokens/{token_id}/sync", tags=["codex-tokens"])
def enqueue_sub2api_sync(token_id: int):
    with Session(engine) as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="codex token not found")
        account_id = int(row.account_id or 0)
        account = s.get(ChatGPTAccount, account_id) if account_id else None
    job_id = enqueue_job(
        type="rt_keepalive",
        input={"account_id": account_id, "codex_token_id": token_id},
        account_id=account_id,
        proxy_id=account.proxy_id if account else None,
        proxy_url=account.proxy_url if account else "",
    )
    return {"job_id": job_id}


@router.patch("/api/codex-tokens/{token_id}/toggle", tags=["codex-tokens"])
def toggle_codex_token(token_id: int):
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="codex token not found")
        row.alive = not row.alive
        if row.alive:
            row.next_refresh_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return _codex_token_to_dict(row)


@router.delete("/api/codex-tokens/{token_id}", tags=["codex-tokens"])
def delete_codex_token(token_id: int):
    with session_scope() as s:
        row = s.get(CodexToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="codex token not found")
        s.delete(row)
    return {"ok": True}


def _mask(value: str) -> str:
    text = str(value or "")
    if len(text) <= 16:
        return text
    return f"{text[:8]}...{text[-6:]}"


def _codex_token_to_dict(row: CodexToken) -> dict:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "refresh_token": _mask(row.refresh_token),
        "access_token": _mask(row.access_token),
        "id_token": _mask(row.id_token),
        "has_refresh_token": bool(row.refresh_token),
        "has_access_token": bool(row.access_token),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "next_refresh_at": row.next_refresh_at.isoformat() if row.next_refresh_at else None,
        "last_refreshed_at": row.last_refreshed_at.isoformat() if row.last_refreshed_at else None,
        "consecutive_failures": row.consecutive_failures,
        "alive": row.alive,
        "last_error": row.last_error,
        "sub2api_external_id": row.sub2api_external_id,
        "sub2api_status": row.sub2api_status,
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "status_checked_at": row.status_checked_at.isoformat() if row.status_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
