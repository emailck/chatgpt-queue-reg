from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from backend.core.db import engine, session_scope
from backend.core.pools.base import AcquireOutcome, Resource
from backend.core.settings import settings
from backend.core.time_utils import utcnow
from backend.models.paypal_number import (
    PAYPAL_NUMBER_STATUS_AVAILABLE,
    PAYPAL_NUMBER_STATUS_BANNED,
    PAYPAL_NUMBER_STATUS_FAILED,
    PAYPAL_NUMBER_STATUS_IN_USE,
    PAYPAL_NUMBER_STATUS_USED,
    PayPalNumber,
)


PAYPAL_NUMBER_COOLDOWN_SETTING = "paypal_number_cooldown_seconds"
PAYPAL_NUMBER_COOLDOWN_DEFAULT = 300


def get_cooldown_seconds() -> int:
    value = settings.get_int(PAYPAL_NUMBER_COOLDOWN_SETTING, PAYPAL_NUMBER_COOLDOWN_DEFAULT)
    return max(0, value)


class PayPalNumberPool:
    name = "paypal_number_pool"
    _lock = threading.Lock()

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        cooldown = get_cooldown_seconds()
        cooldown_threshold = utcnow() - timedelta(seconds=cooldown)
        with self._lock, session_scope() as s:
            row = s.exec(
                select(PayPalNumber)
                .where(
                    or_(
                        PayPalNumber.status == PAYPAL_NUMBER_STATUS_AVAILABLE,
                        (PayPalNumber.status == PAYPAL_NUMBER_STATUS_FAILED)
                        & (
                            (PayPalNumber.last_used_at == None)  # noqa: E711
                            | (PayPalNumber.last_used_at <= cooldown_threshold)
                        ),
                    )
                )
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
            now = utcnow()
            if outcome == AcquireOutcome.CONSUMED:
                row.status = PAYPAL_NUMBER_STATUS_USED
                row.use_count = int(row.use_count or 0) + 1
                row.last_used_at = now
            elif outcome == AcquireOutcome.FAILED:
                row.status = PAYPAL_NUMBER_STATUS_FAILED
                row.use_count = int(row.use_count or 0) + 1
                row.last_error = reason or "failed"
                row.last_used_at = now
            elif outcome == AcquireOutcome.BANNED:
                row.status = PAYPAL_NUMBER_STATUS_BANNED
                row.last_error = reason or "banned"
            else:
                row.status = PAYPAL_NUMBER_STATUS_AVAILABLE
            row.bound_job_id = None
            row.updated_at = now
            s.add(row)

    def stats(self) -> dict[str, Any]:
        cooldown = get_cooldown_seconds()
        threshold = utcnow() - timedelta(seconds=cooldown)
        with Session(engine) as s:
            rows = list(s.exec(select(PayPalNumber)).all())
        out: dict[str, Any] = {
            "total": len(rows),
            "cooldown_seconds": cooldown,
        }
        cooling = 0
        for status in (
            PAYPAL_NUMBER_STATUS_AVAILABLE,
            PAYPAL_NUMBER_STATUS_IN_USE,
            PAYPAL_NUMBER_STATUS_USED,
            PAYPAL_NUMBER_STATUS_FAILED,
            PAYPAL_NUMBER_STATUS_BANNED,
        ):
            out[status] = sum(1 for row in rows if row.status == status)
        for row in rows:
            if row.status != PAYPAL_NUMBER_STATUS_FAILED:
                continue
            if row.last_used_at is not None and row.last_used_at > threshold:
                cooling += 1
        out["cooling_down"] = cooling
        out["failed_ready"] = max(0, out[PAYPAL_NUMBER_STATUS_FAILED] - cooling)
        return out


paypal_number_pool = PayPalNumberPool()
