"""Email APIs: import, list, ad-hoc read, message history, pool ops."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import email_account_to_dict, email_message_to_dict
from backend.core.constants import JOB_TYPE_EMAIL_READ
from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.integrations.mail.imports import (
    MailImportBatchDeleteRequest,
    MailImportDeleteItem,
    MailImportExecuteRequest,
    MailImportSnapshotRequest,
    MicrosoftMailImportStrategy,
)
from backend.integrations.mail import pool as email_pool
from backend.models.email import EmailAccount, EmailMessage

router = APIRouter()
_strategy = MicrosoftMailImportStrategy()


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
    job_id = enqueue_job(
        type=JOB_TYPE_EMAIL_READ,
        input={
            "email": body.email,
            "timeout_seconds": body.timeout_seconds,
            "keyword": body.keyword,
            "code_regex": body.code_regex,
        },
        email_address=body.email,
    )
    return {"job_id": job_id}


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
