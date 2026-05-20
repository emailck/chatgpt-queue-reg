from __future__ import annotations

import threading
from typing import Any, Optional

from sqlmodel import Session, select

from backend.core.db import engine, session_scope
from backend.core.pools.base import AcquireOutcome, Resource
from backend.core.time_utils import utcnow
from backend.models.paypal_number import (
    PAYPAL_NUMBER_STATUS_AVAILABLE,
    PAYPAL_NUMBER_STATUS_BANNED,
    PAYPAL_NUMBER_STATUS_FAILED,
    PAYPAL_NUMBER_STATUS_IN_USE,
    PAYPAL_NUMBER_STATUS_USED,
    PayPalNumber,
)


class PayPalNumberPool:
    name = "paypal_number_pool"
    _lock = threading.Lock()

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        with self._lock, session_scope() as s:
            row = s.exec(
                select(PayPalNumber)
                .where(PayPalNumber.status == PAYPAL_NUMBER_STATUS_AVAILABLE)
                .order_by(PayPalNumber.id)
                .limit(1)
            ).first()
            if row is None:
                return None
            row.status = PAYPAL_NUMBER_STATUS_IN_USE
            row.bound_job_id = int(job_id) if job_id else None
            row.updated_at = utcnow()
            s.add(row)
            s.flush()
            number_id = int(row.id or 0)
            payload = {
                "id": number_id,
                "phone": row.phone,
                "smsurl": row.smsurl,
            }
        return Resource(pool=self.name, id=str(number_id), payload=payload)

    def release(self, resource, *, outcome, reason: str = "") -> None:
        try:
            number_id = int(resource.id)
        except Exception:
            return
        with session_scope() as s:
            row = s.get(PayPalNumber, number_id)
            if row is None:
                return
            if outcome == AcquireOutcome.CONSUMED:
                row.status = PAYPAL_NUMBER_STATUS_USED
                row.use_count = int(row.use_count or 0) + 1
                row.last_used_at = utcnow()
            elif outcome == AcquireOutcome.FAILED:
                row.status = PAYPAL_NUMBER_STATUS_FAILED
                row.use_count = int(row.use_count or 0) + 1
                row.last_error = reason or "failed"
            elif outcome == AcquireOutcome.BANNED:
                row.status = PAYPAL_NUMBER_STATUS_BANNED
                row.last_error = reason or "banned"
            else:
                row.status = PAYPAL_NUMBER_STATUS_AVAILABLE
            row.bound_job_id = None
            row.updated_at = utcnow()
            s.add(row)

    def stats(self) -> dict[str, Any]:
        with Session(engine) as s:
            rows = list(s.exec(select(PayPalNumber)).all())
        out: dict[str, Any] = {"total": len(rows)}
        for status in (
            PAYPAL_NUMBER_STATUS_AVAILABLE,
            PAYPAL_NUMBER_STATUS_IN_USE,
            PAYPAL_NUMBER_STATUS_USED,
            PAYPAL_NUMBER_STATUS_FAILED,
            PAYPAL_NUMBER_STATUS_BANNED,
        ):
            out[status] = sum(1 for row in rows if row.status == status)
        return out


paypal_number_pool = PayPalNumberPool()
