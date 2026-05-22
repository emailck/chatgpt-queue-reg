"""OpenAI refresh-token APIs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken

router = APIRouter()


@router.get("/api/refresh-tokens", tags=["refresh-tokens"])
def list_refresh_tokens(account_id: Optional[int] = None, limit: int = Query(500, ge=1, le=1000)):
    with Session(engine) as s:
        stmt = sa_select(OpenAIRefreshToken)
        if account_id is not None:
            stmt = stmt.where(OpenAIRefreshToken.account_id == account_id)
        rows = list(s.exec(stmt.order_by(OpenAIRefreshToken.id.desc()).limit(limit)).scalars())
    return [_refresh_token_to_dict(row) for row in rows]


@router.post("/api/refresh-tokens/{token_id}/sync", tags=["refresh-tokens"])
def enqueue_sub2api_sync(token_id: int):
    with Session(engine) as s:
        row = s.get(OpenAIRefreshToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="refresh token not found")
        account_id = int(row.account_id or 0)
        account = s.get(ChatGPTAccount, account_id) if account_id else None
    job_id = enqueue_job(
        type="sub2api_sync",
        input={"account_id": account_id, "refresh_token_id": token_id},
        account_id=account_id,
        proxy_id=account.proxy_id if account else None,
        proxy_url=account.proxy_url if account else "",
    )
    return {"job_id": job_id}


@router.patch("/api/refresh-tokens/{token_id}/toggle", tags=["refresh-tokens"])
def toggle_refresh_token(token_id: int):
    with session_scope() as s:
        row = s.get(OpenAIRefreshToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="refresh token not found")
        row.enabled = not row.enabled
        if row.enabled:
            row.next_sync_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return _refresh_token_to_dict(row)


@router.delete("/api/refresh-tokens/{token_id}", tags=["refresh-tokens"])
def delete_refresh_token(token_id: int):
    with session_scope() as s:
        row = s.get(OpenAIRefreshToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="refresh token not found")
        s.delete(row)
    return {"ok": True}


def _mask(value: str) -> str:
    text = str(value or "")
    if len(text) <= 16:
        return text
    return f"{text[:8]}...{text[-6:]}"


def _refresh_token_to_dict(row: OpenAIRefreshToken) -> dict:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "refresh_token": _mask(row.refresh_token),
        "oauth_access_token": _mask(row.oauth_access_token),
        "oauth_id_token": _mask(row.oauth_id_token),
        "has_refresh_token": bool(row.refresh_token),
        "has_oauth_access_token": bool(row.oauth_access_token),
        "oauth_access_expires_at": row.oauth_access_expires_at.isoformat() if row.oauth_access_expires_at else None,
        "next_sync_at": row.next_sync_at.isoformat() if row.next_sync_at else None,
        "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
        "consecutive_failures": row.consecutive_failures,
        "enabled": row.enabled,
        "last_error": row.last_error,
        "sub2api_account_id": row.sub2api_account_id,
        "sub2api_status": row.sub2api_status,
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "status_checked_at": row.status_checked_at.isoformat() if row.status_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
