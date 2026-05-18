"""Payment card pool APIs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.time_utils import utcnow
from backend.models.payment_card import CARD_STATUS_AVAILABLE, PaymentCard

router = APIRouter()


class CardCreate(BaseModel):
    number: str
    exp_month: int
    exp_year: int
    cvv: str = ""
    holder_name: str = ""
    billing_country: str = ""
    billing_postal: str = ""
    note: str = ""


class CardUpdate(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None
    last_error: Optional[str] = None


class CardBulkCreate(BaseModel):
    cards: list[CardCreate]


class IdsRequest(BaseModel):
    ids: list[int]


@router.get("/api/cards", tags=["cards"])
def list_cards(status: Optional[str] = None, limit: int = Query(500, ge=1, le=2000)):
    with Session(engine) as s:
        stmt = sa_select(PaymentCard)
        if status:
            stmt = stmt.where(PaymentCard.status == status)
        rows = list(s.exec(stmt.order_by(PaymentCard.id.desc()).limit(limit)).scalars())
    return [_card_to_dict(row) for row in rows]


@router.post("/api/cards", tags=["cards"])
def create_card(body: CardCreate):
    if not str(body.number or "").strip():
        raise HTTPException(status_code=400, detail="card number is required")
    with session_scope() as s:
        row = PaymentCard(**body.model_dump(), status=CARD_STATUS_AVAILABLE)
        s.add(row)
        s.commit()
        s.refresh(row)
        return _card_to_dict(row)


@router.post("/api/cards/bulk", tags=["cards"])
def bulk_create_cards(body: CardBulkCreate):
    created = 0
    with session_scope() as s:
        for item in body.cards:
            if not str(item.number or "").strip():
                continue
            s.add(PaymentCard(**item.model_dump(), status=CARD_STATUS_AVAILABLE))
            created += 1
    return {"created": created}


@router.patch("/api/cards/{card_id}", tags=["cards"])
def update_card(card_id: int, body: CardUpdate):
    with session_scope() as s:
        row = s.get(PaymentCard, card_id)
        if row is None:
            raise HTTPException(status_code=404, detail="card not found")
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
        return _card_to_dict(row)


@router.delete("/api/cards/{card_id}", tags=["cards"])
def delete_card(card_id: int):
    with session_scope() as s:
        row = s.get(PaymentCard, card_id)
        if row is None:
            raise HTTPException(status_code=404, detail="card not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/cards/batch-delete", tags=["cards"])
def batch_delete_cards(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    deleted: list[int] = []
    with session_scope() as s:
        for card_id in ids:
            row = s.get(PaymentCard, card_id)
            if row is None:
                continue
            s.delete(row)
            deleted.append(card_id)
    return {"deleted": len(deleted), "deleted_ids": deleted, "total_requested": len(ids)}


def _card_to_dict(row: PaymentCard) -> dict:
    return {
        "id": row.id,
        "number": row.number,
        "exp_month": row.exp_month,
        "exp_year": row.exp_year,
        "cvv": row.cvv,
        "holder_name": row.holder_name,
        "billing_country": row.billing_country,
        "billing_postal": row.billing_postal,
        "status": row.status,
        "bind_count": row.bind_count,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_error": row.last_error,
        "bound_job_id": row.bound_job_id,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
