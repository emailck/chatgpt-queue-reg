"""Payment card resource pool.

DB-backed against `payment_cards`. Acquire picks the lowest-id row whose
status is `available`, flips it to `in_use` inside a lock+session so two
workers cannot grab the same card.  Release maps `AcquireOutcome` to the
appropriate status transition.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from sqlmodel import Session, select

from backend.core.db import engine, session_scope
from backend.core.pools.base import AcquireOutcome, Resource
from backend.core.time_utils import utcnow
from backend.models.payment_card import (
    CARD_STATUS_AVAILABLE,
    CARD_STATUS_BANNED,
    CARD_STATUS_FAILED,
    CARD_STATUS_IN_USE,
    CARD_STATUS_USED,
    PaymentCard,
)

BAN_THRESHOLD = 3  # bind_count at/after which a failed card is banned


class CardPool:
    name = "card_pool"
    _lock = threading.Lock()

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        with self._lock, session_scope() as s:
            stmt = (
                select(PaymentCard)
                .where(PaymentCard.status == CARD_STATUS_AVAILABLE)
                .order_by(PaymentCard.id)
                .limit(1)
            )
            row = s.exec(stmt).first()
            if row is None:
                return None
            row.status = CARD_STATUS_IN_USE
            row.bound_job_id = int(job_id) if job_id else None
            row.updated_at = utcnow()
            s.add(row)
            s.flush()
            card_id = int(row.id or 0)
            payload = {
                "id": card_id,
                "number": row.number,
                "exp_month": row.exp_month,
                "exp_year": row.exp_year,
                "cvv": row.cvv,
                "holder_name": row.holder_name,
                "billing_country": row.billing_country,
                "billing_postal": row.billing_postal,
            }
        return Resource(pool=self.name, id=str(card_id), payload=payload)

    def release(self, resource, *, outcome, reason: str = "") -> None:
        try:
            card_id = int(resource.id)
        except Exception:
            return
        with session_scope() as s:
            row = s.get(PaymentCard, card_id)
            if row is None:
                return
            if outcome == AcquireOutcome.CONSUMED:
                row.status = CARD_STATUS_USED
                row.bind_count = int(row.bind_count or 0) + 1
                row.last_used_at = utcnow()
            elif outcome == AcquireOutcome.FAILED:
                row.bind_count = int(row.bind_count or 0) + 1
                row.last_error = reason or "failed"
                row.status = (
                    CARD_STATUS_BANNED
                    if row.bind_count >= BAN_THRESHOLD
                    else CARD_STATUS_FAILED
                )
            elif outcome == AcquireOutcome.BANNED:
                row.status = CARD_STATUS_BANNED
                row.last_error = reason or "banned"
            else:  # REUSABLE
                row.status = CARD_STATUS_AVAILABLE
            row.bound_job_id = None
            row.updated_at = utcnow()
            s.add(row)

    def stats(self) -> dict[str, Any]:
        with Session(engine) as s:
            rows = list(s.exec(select(PaymentCard)).all())
        out: dict[str, Any] = {"total": len(rows)}
        for status in (
            CARD_STATUS_AVAILABLE,
            CARD_STATUS_IN_USE,
            CARD_STATUS_USED,
            CARD_STATUS_FAILED,
            CARD_STATUS_BANNED,
        ):
            out[status] = sum(1 for r in rows if r.status == status)
        return out


card_pool = CardPool()
