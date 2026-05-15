"""Payment-link list + actions."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import payment_link_to_dict
from backend.core.constants import JOB_TYPE_BROWSER_DEBUG, JOB_TYPE_PAYMENT_EMPTY
from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.models.payment import PaymentLink

router = APIRouter()


class DebugBrowserRequest(BaseModel):
    browser_type: str = "camoufox"
    inject_cookies: bool = True
    inject_local_storage: bool = True
    inject_fingerprint: bool = True
    record_har: bool = True
    omit_har_content: bool = False


class IdsRequest(BaseModel):
    ids: list[int]


@router.get("/api/payment-links", tags=["payment-links"])
def list_payment_links(
    account_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(PaymentLink)
        if account_id is not None:
            stmt = stmt.where(PaymentLink.account_id == account_id)
        if status:
            stmt = stmt.where(PaymentLink.status == status)
        stmt = stmt.order_by(PaymentLink.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
    return [payment_link_to_dict(r) for r in rows]


@router.get("/api/payment-links/{payment_link_id}", tags=["payment-links"])
def get_payment_link(payment_link_id: int):
    with Session(engine) as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is None:
            raise HTTPException(status_code=404, detail="payment_link not found")
    return payment_link_to_dict(row)


@router.post("/api/payment-links/{payment_link_id}/payment", tags=["payment-links"])
def trigger_empty_payment(payment_link_id: int):
    with Session(engine) as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is None:
            raise HTTPException(status_code=404, detail="payment_link not found")
    job_id = enqueue_job(
        type=JOB_TYPE_PAYMENT_EMPTY,
        input={"payment_link_id": payment_link_id},
        payment_link_id=payment_link_id,
        account_id=row.account_id,
    )
    return {"job_id": job_id}


@router.post("/api/payment-links/{payment_link_id}/debug-browser", tags=["payment-links"])
def debug_browser(payment_link_id: int, body: DebugBrowserRequest):
    with Session(engine) as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is None:
            raise HTTPException(status_code=404, detail="payment_link not found")
        target = row.checkout_url
        account_id = row.account_id
    if not target:
        raise HTTPException(status_code=409, detail="payment_link has no checkout_url")
    job_id = enqueue_job(
        type=JOB_TYPE_BROWSER_DEBUG,
        input={
            "target_url": target,
            "account_id": account_id,
            "payment_link_id": payment_link_id,
            "browser_type": body.browser_type,
            "inject_cookies": body.inject_cookies,
            "inject_local_storage": body.inject_local_storage,
            "inject_fingerprint": body.inject_fingerprint,
            "record_har": body.record_har,
            "omit_har_content": body.omit_har_content,
        },
        account_id=account_id,
        payment_link_id=payment_link_id,
    )
    return {"job_id": job_id}


@router.delete("/api/payment-links/{payment_link_id}", tags=["payment-links"])
def delete_payment_link(payment_link_id: int):
    with session_scope() as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is None:
            raise HTTPException(status_code=404, detail="payment_link not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/payment-links/batch-delete", tags=["payment-links"])
def batch_delete_payment_links(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for pid in ids:
            row = s.get(PaymentLink, pid)
            if row is None:
                not_found.append(pid)
                continue
            s.delete(row)
            deleted.append(pid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "not_found": not_found,
        "total_requested": len(ids),
    }
