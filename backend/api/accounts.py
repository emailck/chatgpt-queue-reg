"""ChatGPT account list + per-row helper actions."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import account_to_dict, payment_link_to_dict
from backend.core.constants import (
    JOB_TYPE_BROWSER_DEBUG,
    JOB_TYPE_CHATGPT_PAYMENT_LINK,
    JOB_TYPE_EMAIL_READ,
)
from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.models.account import ChatGPTAccount
from backend.models.payment import PaymentLink

router = APIRouter()


class RetryPaymentLinkRequest(BaseModel):
    plan: str | None = None  # "team" | "plus"
    workspace_name: str | None = None
    price_interval: str | None = None
    seat_quantity: int | None = None
    country: str | None = None
    currency: str | None = None


class ReadEmailRequest(BaseModel):
    timeout_seconds: int = 120
    keyword: str = ""
    code_regex: str | None = None


class DebugBrowserRequest(BaseModel):
    target_url: str | None = None
    browser_type: str = "camoufox"
    inject_cookies: bool = True
    inject_local_storage: bool = True
    inject_fingerprint: bool = True
    record_har: bool = True
    omit_har_content: bool = False


@router.get("/api/accounts", tags=["accounts"])
def list_accounts(
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(ChatGPTAccount)
        if status:
            stmt = stmt.where(ChatGPTAccount.status == status)
        stmt = stmt.order_by(ChatGPTAccount.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
        link_ids = {r.last_payment_link_id for r in rows if r.last_payment_link_id}
        urls: dict[int, str] = {}
        if link_ids:
            for row in s.exec(
                sa_select(PaymentLink).where(PaymentLink.id.in_(list(link_ids)))
            ).scalars():
                urls[int(row.id or 0)] = row.checkout_url
    return [
        account_to_dict(
            row,
            last_payment_link_url=urls.get(row.last_payment_link_id or 0, ""),
        )
        for row in rows
    ]


@router.get("/api/accounts/{account_id}", tags=["accounts"])
def get_account(account_id: int):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        link_url = ""
        if row.last_payment_link_id:
            link = s.get(PaymentLink, row.last_payment_link_id)
            if link:
                link_url = link.checkout_url
    return account_to_dict(row, last_payment_link_url=link_url)


@router.post("/api/accounts/{account_id}/payment-link/retry", tags=["accounts"])
def retry_payment_link(account_id: int, body: RetryPaymentLinkRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    plan = (body.plan or "team").lower()
    default_country = "ID" if plan == "plus" else "US"
    job_id = enqueue_job(
        type=JOB_TYPE_CHATGPT_PAYMENT_LINK,
        input={
            "account_id": account_id,
            "plan": plan,
            "workspace_name": body.workspace_name or "MyWorkspace",
            "price_interval": body.price_interval or "month",
            "seat_quantity": int(body.seat_quantity or 2),
            "country": body.country or default_country,
            "currency": body.currency,
        },
        account_id=account_id,
        proxy_url=row.proxy_url or "",
    )
    return {"job_id": job_id}


@router.post("/api/accounts/{account_id}/read-email", tags=["accounts"])
def read_email(account_id: int, body: ReadEmailRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    job_id = enqueue_job(
        type=JOB_TYPE_EMAIL_READ,
        input={
            "email": row.email,
            "timeout_seconds": body.timeout_seconds,
            "keyword": body.keyword,
            "code_regex": body.code_regex,
        },
        account_id=account_id,
        email_address=row.email,
    )
    return {"job_id": job_id}


@router.post("/api/accounts/{account_id}/debug-browser", tags=["accounts"])
def debug_browser(account_id: int, body: DebugBrowserRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    job_id = enqueue_job(
        type=JOB_TYPE_BROWSER_DEBUG,
        input={
            "target_url": body.target_url or "https://chatgpt.com/",
            "account_id": account_id,
            "browser_type": body.browser_type,
            "inject_cookies": body.inject_cookies,
            "inject_local_storage": body.inject_local_storage,
            "inject_fingerprint": body.inject_fingerprint,
            "record_har": body.record_har,
            "omit_har_content": body.omit_har_content,
        },
        account_id=account_id,
        proxy_url=row.proxy_url or "",
    )
    return {"job_id": job_id}


class IdsRequest(BaseModel):
    ids: list[int]


@router.delete("/api/accounts/{account_id}", tags=["accounts"])
def delete_account(account_id: int):
    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/accounts/batch-delete", tags=["accounts"])
def batch_delete_accounts(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for aid in ids:
            row = s.get(ChatGPTAccount, aid)
            if row is None:
                not_found.append(aid)
                continue
            s.delete(row)
            deleted.append(aid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "not_found": not_found,
        "total_requested": len(ids),
    }
