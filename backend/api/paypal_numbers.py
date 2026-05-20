from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.pools.paypal_number_pool import sweep_expired_cooling
from backend.core.time_utils import utcnow
from backend.models.paypal_number import PAYPAL_NUMBER_STATUS_AVAILABLE, PayPalNumber

router = APIRouter()


class PayPalNumberCreate(BaseModel):
    phone: str
    smsurl: str
    note: str = ""


class PayPalNumberUpdate(BaseModel):
    phone: Optional[str] = None
    smsurl: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None
    last_error: Optional[str] = None


class PayPalNumberBulkCreate(BaseModel):
    numbers: list[PayPalNumberCreate]


class IdsRequest(BaseModel):
    ids: list[int]


@router.get("/api/paypal-numbers", tags=["paypal_numbers"])
def list_paypal_numbers(status: Optional[str] = None, limit: int = Query(500, ge=1, le=2000)):
    sweep_expired_cooling()
    with Session(engine) as s:
        stmt = sa_select(PayPalNumber)
        if status:
            stmt = stmt.where(PayPalNumber.status == status)
        rows = list(s.exec(stmt.order_by(PayPalNumber.id.desc()).limit(limit)).scalars())
    return [_paypal_number_to_dict(row) for row in rows]


@router.post("/api/paypal-numbers", tags=["paypal_numbers"])
def create_paypal_number(body: PayPalNumberCreate):
    if not str(body.phone or "").strip():
        raise HTTPException(status_code=400, detail="phone is required")
    with session_scope() as s:
        row = PayPalNumber(phone=body.phone.strip(), smsurl=str(body.smsurl or "").strip(), note=body.note, status=PAYPAL_NUMBER_STATUS_AVAILABLE)
        s.add(row)
        s.commit()
        s.refresh(row)
        return _paypal_number_to_dict(row)


@router.post("/api/paypal-numbers/bulk", tags=["paypal_numbers"])
def bulk_create_paypal_numbers(body: PayPalNumberBulkCreate):
    created = 0
    with session_scope() as s:
        for item in body.numbers:
            phone = str(item.phone or "").strip()
            if not phone:
                continue
            s.add(PayPalNumber(phone=phone, smsurl=str(item.smsurl or "").strip(), note=item.note, status=PAYPAL_NUMBER_STATUS_AVAILABLE))
            created += 1
    return {"created": created}


@router.patch("/api/paypal-numbers/{number_id}", tags=["paypal_numbers"])
def update_paypal_number(number_id: int, body: PayPalNumberUpdate):
    with session_scope() as s:
        row = s.get(PayPalNumber, number_id)
        if row is None:
            raise HTTPException(status_code=404, detail="paypal number not found")
        if body.phone is not None:
            row.phone = body.phone.strip()
        if body.smsurl is not None:
            row.smsurl = body.smsurl.strip()
        if body.status is not None:
            row.status = body.status
        if body.note is not None:
            row.note = body.note
        if body.last_error is not None:
            row.last_error = body.last_error
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return _paypal_number_to_dict(row)


@router.delete("/api/paypal-numbers/{number_id}", tags=["paypal_numbers"])
def delete_paypal_number(number_id: int):
    with session_scope() as s:
        row = s.get(PayPalNumber, number_id)
        if row is None:
            raise HTTPException(status_code=404, detail="paypal number not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/paypal-numbers/batch-delete", tags=["paypal_numbers"])
def batch_delete_paypal_numbers(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    deleted: list[int] = []
    with session_scope() as s:
        for number_id in ids:
            row = s.get(PayPalNumber, number_id)
            if row is None:
                continue
            s.delete(row)
            deleted.append(number_id)
    return {"deleted": len(deleted), "deleted_ids": deleted, "total_requested": len(ids)}


def _paypal_number_to_dict(row: PayPalNumber) -> dict:
    return {
        "id": row.id,
        "phone": row.phone,
        "smsurl": row.smsurl,
        "status": row.status,
        "use_count": row.use_count,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_error": row.last_error,
        "bound_job_id": row.bound_job_id,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
