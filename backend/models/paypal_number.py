from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


PAYPAL_NUMBER_STATUS_AVAILABLE = "available"
PAYPAL_NUMBER_STATUS_IN_USE = "in_use"
PAYPAL_NUMBER_STATUS_USED = "used"
PAYPAL_NUMBER_STATUS_FAILED = "failed"
PAYPAL_NUMBER_STATUS_BANNED = "banned"


class PayPalNumber(SQLModel, table=True):
    __tablename__ = "paypal_numbers"

    id: Optional[int] = Field(default=None, primary_key=True)
    phone: str = Field(index=True)
    smsurl: str = ""

    status: str = Field(default=PAYPAL_NUMBER_STATUS_AVAILABLE, index=True)
    use_count: int = 0
    last_used_at: Optional[datetime] = None
    last_error: str = ""
    bound_job_id: Optional[int] = Field(default=None, index=True)
    note: str = ""

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
