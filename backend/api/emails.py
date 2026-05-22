"""Email APIs: import, list, ad-hoc read, message history, pool ops."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import email_account_to_dict, email_message_to_dict
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.integrations.mail.imports import (
    MailImportBatchDeleteRequest,
    MailImportDeleteItem,
    MailImportExecuteRequest,
    MailImportSnapshotRequest,
    MicrosoftMailImportStrategy,
)
from backend.integrations.mail import pool as email_pool
from backend.integrations.mail.microsoft import MicrosoftMailbox, wait_for_otp
from backend.models.email import EmailAccount, EmailMessage

router = APIRouter()
_strategy = MicrosoftMailImportStrategy()
OTP_REQUEST_GRACE_SECONDS = 30


class ImportRequest(BaseModel):
    content: str
    enabled: bool = True
    alias_split_enabled: bool = False
    alias_split_count: int = Field(default=5, ge=1, le=5)
    alias_include_original: bool = False
    preview_limit: int = Field(default=100, ge=1, le=500)


class DeleteItemModel(BaseModel):
    email: str


class BatchDeleteRequest(BaseModel):
    items: list[DeleteItemModel]


class ReadRequest(BaseModel):
    email: str
    timeout_seconds: int = 120
    keyword: str = ""
    code_regex: str | None = None


@router.get("/api/email/accounts", tags=["email"])
def list_email_accounts(limit: int = Query(500, ge=1, le=1000)):
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .order_by(EmailAccount.id.desc())
                .limit(limit)
            ).scalars()
        )
    return [email_account_to_dict(r) for r in rows]


@router.post("/api/email/import", tags=["email"])
def import_emails(body: ImportRequest):
    response = _strategy.execute(MailImportExecuteRequest(
        type="microsoft",
        content=body.content,
        enabled=body.enabled,
        alias_split_enabled=body.alias_split_enabled,
        alias_split_count=body.alias_split_count,
        alias_include_original=body.alias_include_original,
        preview_limit=body.preview_limit,
    ))
    return response.model_dump()


@router.get("/api/email/snapshot", tags=["email"])
def email_snapshot(preview_limit: int = Query(100, ge=1, le=500)):
    snapshot = _strategy.get_snapshot(MailImportSnapshotRequest(
        type="microsoft", preview_limit=preview_limit,
    ))
    return snapshot.model_dump()


@router.post("/api/email/batch-delete", tags=["email"])
def batch_delete_emails(body: BatchDeleteRequest):
    response = _strategy.batch_delete(MailImportBatchDeleteRequest(
        type="microsoft",
        items=[MailImportDeleteItem(email=item.email) for item in body.items],
    ))
    return response.model_dump()


@router.post("/api/email/read", tags=["email"])
def read_email(body: ReadRequest):
    email = body.email.strip()
    if not email:
        raise HTTPException(status_code=400, detail="email 不能为空")

    with Session(engine) as s:
        row = (
            s.exec(
                sa_select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.email == email)
            ).scalars().first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"邮箱不在 Microsoft 池中: {email}")

    meta = json_loads(row.metadata_json, fallback={}) or {}
    client_id = str(meta.get("client_id") or "")
    refresh_token = row.refresh_token
    if not client_id or not refresh_token:
        raise HTTPException(status_code=409, detail=f"邮箱缺少 OAuth client_id/refresh_token: {email}")

    since_dt = datetime.now(timezone.utc) - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS)
    data = wait_for_otp(
        mailbox=MicrosoftMailbox(),
        client_id=client_id,
        refresh_token=refresh_token,
        keyword=body.keyword,
        code_pattern=body.code_regex,
        timeout=int(body.timeout_seconds or 120),
        poll_interval=5,
        since_iso=since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    with session_scope() as s:
        s.add(EmailMessage(
            account_id=row.id,
            job_id=None,
            email=row.email,
            provider="microsoft",
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=json_dumps(data.get("raw") or {}),
        ))

    return {
        "email": email,
        "code": data.get("code"),
        "subject": data.get("subject"),
        "sender": data.get("sender"),
        "received_at": data.get("received_at"),
    }


@router.get("/api/email/messages", tags=["email"])
def list_messages(
    email: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(EmailMessage)
        if email:
            stmt = stmt.where(EmailMessage.email == email)
        stmt = stmt.order_by(EmailMessage.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
    return [email_message_to_dict(r) for r in rows]


@router.get("/api/email/accounts/{email}/history", tags=["email"])
def email_account_history(email: str, limit: int = Query(10, ge=1, le=50)):
    row = _get_microsoft_email_account(email)
    history = _fetch_live_email_history(row, limit=limit)
    if history is not None:
        return history
    return _local_email_history(row.email, limit=limit)


def email_history_for_address(email: str, *, limit: int = 10) -> list[dict[str, Any]]:
    row = _get_microsoft_email_account(email)
    history = _fetch_live_email_history(row, limit=limit)
    if history is not None:
        return history
    return _local_email_history(row.email, limit=limit)


def _get_microsoft_email_account(email: str) -> EmailAccount:
    value = str(email or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="email 不能为空")
    with Session(engine) as s:
        row = (
            s.exec(
                sa_select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.email == value)
            ).scalars().first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"邮箱不在 Microsoft 池中: {value}")
    return row


def _fetch_live_email_history(row: EmailAccount, *, limit: int) -> list[dict[str, Any]] | None:
    meta = json_loads(row.metadata_json, fallback={}) or {}
    client_id = str(meta.get("client_id") or "")
    refresh_token = row.refresh_token
    if not client_id or not refresh_token:
        return None
    target_email = str(row.email or "").strip().lower()
    messages = MicrosoftMailbox().list_messages(client_id=client_id, refresh_token=refresh_token, top=max(limit * 5, 50))
    messages = [message for message in messages if target_email in {recipient.lower() for recipient in message.recipients}]
    messages.sort(key=lambda item: item.received_at or "", reverse=True)
    return [
        {
            "id": message.id,
            "account_id": row.id,
            "job_id": None,
            "email": row.email,
            "provider": row.provider,
            "subject": message.subject,
            "sender": message.sender,
            "recipients": message.recipients,
            "body_text": message.body_text,
            "code": "",
            "received_at": message.received_at or None,
            "created_at": None,
            "folder": message.folder,
        }
        for message in messages[:limit]
    ]


def _local_email_history(email: str, *, limit: int) -> list[dict[str, Any]]:
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(EmailMessage)
                .where(EmailMessage.email == email)
                .order_by(EmailMessage.id.desc())
                .limit(limit)
            ).scalars()
        )
    return [email_message_to_dict(row) for row in rows]


class MessageIdsRequest(BaseModel):
    ids: list[int]


@router.post("/api/email/messages/batch-delete", tags=["email"])
def batch_delete_messages(body: MessageIdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for mid in ids:
            row = s.get(EmailMessage, mid)
            if row is None:
                not_found.append(mid)
                continue
            s.delete(row)
            deleted.append(mid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "not_found": not_found,
        "total_requested": len(ids),
    }


# ---- pool ops --------------------------------------------------------------


class PoolEmailRequest(BaseModel):
    email: str
    note: str = ""


@router.get("/api/email/pool-stats", tags=["email"])
def pool_stats():
    return email_pool.stats()


@router.post("/api/email/requeue", tags=["email"])
def requeue_email(body: PoolEmailRequest):
    if not email_pool.requeue(email=body.email):
        raise HTTPException(status_code=404, detail=f"未找到邮箱: {body.email}")
    return {"ok": True}


@router.post("/api/email/mark-consumed", tags=["email"])
def mark_email_consumed(body: PoolEmailRequest):
    if not email_pool.mark_consumed(email=body.email, note=body.note or "manual"):
        raise HTTPException(status_code=404, detail=f"未找到邮箱: {body.email}")
    return {"ok": True}


@router.post("/api/email/blacklist", tags=["email"])
def blacklist_email(body: PoolEmailRequest):
    if not email_pool.blacklist(email=body.email, note=body.note or "manual"):
        raise HTTPException(status_code=404, detail=f"未找到邮箱: {body.email}")
    return {"ok": True}
