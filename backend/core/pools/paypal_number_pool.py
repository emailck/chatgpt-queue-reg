from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
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
    PAYPAL_NUMBER_STATUS_COOLING,
    PAYPAL_NUMBER_STATUS_IN_USE,
    PayPalNumber,
)


PAYPAL_NUMBER_COOLDOWN_SETTING = "paypal_number_cooldown_seconds"
PAYPAL_NUMBER_COOLDOWN_DEFAULT = 300


def get_cooldown_seconds() -> int:
    value = settings.get_int(PAYPAL_NUMBER_COOLDOWN_SETTING, PAYPAL_NUMBER_COOLDOWN_DEFAULT)
    return max(0, value)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def sweep_expired_cooling() -> int:
    cooldown = get_cooldown_seconds()
    threshold = utcnow() - timedelta(seconds=cooldown)
    count = 0
    with session_scope() as s:
        rows = list(s.exec(
            select(PayPalNumber)
            .where(PayPalNumber.status == PAYPAL_NUMBER_STATUS_COOLING)
        ).all())
        now = utcnow()
        for row in rows:
            last = _as_utc(row.last_used_at)
            if last is None or last <= threshold:
                row.status = PAYPAL_NUMBER_STATUS_AVAILABLE
                row.updated_at = now
                s.add(row)
                count += 1
    return count


class PayPalNumberPool:
    name = "paypal_number_pool"
    _lock = threading.Lock()

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        sweep_expired_cooling()
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
            now = utcnow()
            if outcome == AcquireOutcome.CONSUMED:
                row.status = PAYPAL_NUMBER_STATUS_COOLING
                row.use_count = int(row.use_count or 0) + 1
                row.last_used_at = now
                row.last_error = ""
            elif outcome == AcquireOutcome.FAILED:
                row.status = PAYPAL_NUMBER_STATUS_COOLING
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

    def fetch_otp(self, number_id: int, expected_length: int = 6, timeout: int = 90) -> str:
        """Poll the number's smsurl for an OTP matching expected_length.

        Returns the OTP string, or raises RuntimeError on timeout.
        This is the single source of truth for OTP retrieval — payment modules
        should call this instead of polling smsurl directly.
        """
        import json
        import re
        import time

        import requests as _requests

        with Session(engine) as s:
            row = s.get(PayPalNumber, number_id)
            if row is None:
                raise RuntimeError(f"PayPalNumber {number_id} not found")
            smsurl = str(row.smsurl or "").strip()
            if not smsurl:
                raise RuntimeError(f"PayPalNumber {number_id} has no smsurl")

        deadline = time.time() + timeout
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            try:
                resp = _requests.get(smsurl, timeout=15)
                text = resp.text or ""
                try:
                    payload = resp.json()
                    text += " " + json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass
                for match in re.finditer(r"\b(\d{4,8})\b", text):
                    token = match.group(1)
                    if len(token) == expected_length:
                        return token
            except Exception:
                pass
            time.sleep(3)

        raise RuntimeError(f"OTP 获取超时 ({timeout}s) number_id={number_id} expected_length={expected_length}")

    def stats(self) -> dict[str, Any]:
        sweep_expired_cooling()
        cooldown = get_cooldown_seconds()
        with Session(engine) as s:
            rows = list(s.exec(select(PayPalNumber)).all())
        out: dict[str, Any] = {
            "total": len(rows),
            "cooldown_seconds": cooldown,
        }
        for status in (
            PAYPAL_NUMBER_STATUS_AVAILABLE,
            PAYPAL_NUMBER_STATUS_IN_USE,
            PAYPAL_NUMBER_STATUS_COOLING,
            PAYPAL_NUMBER_STATUS_BANNED,
        ):
            out[status] = sum(1 for row in rows if row.status == status)
        return out


paypal_number_pool = PayPalNumberPool()
