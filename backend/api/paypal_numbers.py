from __future__ import annotations

import re
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


class BatchStatusRequest(IdsRequest):
    status: str


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
    phone = str(body.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    with session_scope() as s:
        if _phone_exists(s, phone):
            raise HTTPException(status_code=409, detail="phone already exists")
        row = PayPalNumber(phone=phone, smsurl=str(body.smsurl or "").strip(), note=body.note, status=PAYPAL_NUMBER_STATUS_AVAILABLE)
        s.add(row)
        s.commit()
        s.refresh(row)
        return _paypal_number_to_dict(row)


@router.post("/api/paypal-numbers/bulk", tags=["paypal_numbers"])
def bulk_create_paypal_numbers(body: PayPalNumberBulkCreate):
    created = 0
    skipped_duplicates = 0
    skipped_invalid = 0
    with session_scope() as s:
        existing = _existing_phone_keys(s)
        seen: set[str] = set()
        for item in body.numbers:
            phone = str(item.phone or "").strip()
            key = _phone_key(phone)
            if not key:
                skipped_invalid += 1
                continue
            if key in existing or key in seen:
                skipped_duplicates += 1
                continue
            seen.add(key)
            s.add(PayPalNumber(phone=phone, smsurl=str(item.smsurl or "").strip(), note=item.note, status=PAYPAL_NUMBER_STATUS_AVAILABLE))
            created += 1
    return {"created": created, "skipped_duplicates": skipped_duplicates, "skipped_invalid": skipped_invalid}


@router.patch("/api/paypal-numbers/{number_id}", tags=["paypal_numbers"])
def update_paypal_number(number_id: int, body: PayPalNumberUpdate):
    with session_scope() as s:
        row = s.get(PayPalNumber, number_id)
        if row is None:
            raise HTTPException(status_code=404, detail="paypal number not found")
        if body.phone is not None:
            phone = body.phone.strip()
            if _phone_exists(s, phone, exclude_id=number_id):
                raise HTTPException(status_code=409, detail="phone already exists")
            row.phone = phone
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


@router.post("/api/paypal-numbers/batch-status", tags=["paypal_numbers"])
def batch_update_paypal_number_status(body: BatchStatusRequest):
    status = str(body.status or "").strip()
    if status not in {"available", "in_use", "cooling", "banned"}:
        raise HTTPException(status_code=400, detail="invalid status")
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    updated: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for number_id in ids:
            row = s.get(PayPalNumber, number_id)
            if row is None:
                not_found.append(number_id)
                continue
            row.status = status
            if status == PAYPAL_NUMBER_STATUS_AVAILABLE:
                row.bound_job_id = None
                row.last_error = ""
            row.updated_at = utcnow()
            s.add(row)
            updated.append(number_id)
    return {"updated": len(updated), "updated_ids": updated, "not_found": not_found, "total_requested": len(ids)}


@router.post("/api/paypal-numbers/dedupe", tags=["paypal_numbers"])
def dedupe_paypal_numbers():
    deleted: list[int] = []
    skipped_bound: list[int] = []
    with session_scope() as s:
        groups: dict[str, list[PayPalNumber]] = {}
        for row in s.exec(sa_select(PayPalNumber).order_by(PayPalNumber.id.asc())).scalars():
            key = _phone_key(row.phone)
            if not key:
                continue
            groups.setdefault(key, []).append(row)
        for rows in groups.values():
            if len(rows) < 2:
                continue
            keep = _dedupe_keep_row(rows)
            for row in rows:
                if row.id == keep.id:
                    continue
                if row.bound_job_id:
                    skipped_bound.append(int(row.id or 0))
                    continue
                deleted.append(int(row.id or 0))
                s.delete(row)
    return {"deleted": len(deleted), "deleted_ids": deleted, "skipped_bound_ids": skipped_bound}


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


def _phone_key(phone: str) -> str:
    return re.sub(r"\D+", "", str(phone or "")) or str(phone or "").strip().lower()


def _phone_exists(s: Session, phone: str, *, exclude_id: int | None = None) -> bool:
    key = _phone_key(phone)
    if not key:
        return False
    for row in s.exec(sa_select(PayPalNumber.id, PayPalNumber.phone)).all():
        row_id, row_phone = row
        if exclude_id is not None and int(row_id or 0) == int(exclude_id):
            continue
        if _phone_key(str(row_phone or "")) == key:
            return True
    return False


def _existing_phone_keys(s: Session) -> set[str]:
    return {
        key for key in (_phone_key(str(row.phone or "")) for row in s.exec(sa_select(PayPalNumber)).scalars())
        if key
    }


def _dedupe_keep_row(rows: list[PayPalNumber]) -> PayPalNumber:
    return sorted(
        rows,
        key=lambda row: (
            0 if row.bound_job_id else 1,
            0 if row.status == "in_use" else 1,
            int(row.id or 0),
        ),
    )[0]


def _paypal_number_to_dict(row: PayPalNumber) -> dict:
    return {
        "id": row.id,
        "phone": row.phone,
        "smsurl": row.smsurl,
        "status": row.status,
        "use_count": row.use_count,
        "otp_failure_count": int(getattr(row, "otp_failure_count", 0) or 0),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_error": row.last_error,
        "bound_job_id": row.bound_job_id,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
