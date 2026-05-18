"""Payment card pool row.

Cards belong to the `card_pool` resource pool, consumed by the `payment` stage.
Statuses follow the pool contract (ARCHITECTURE.md §2.3):
  available | in_use | used | failed | banned
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


CARD_STATUS_AVAILABLE = "available"
CARD_STATUS_IN_USE = "in_use"
CARD_STATUS_USED = "used"
CARD_STATUS_FAILED = "failed"
CARD_STATUS_BANNED = "banned"


class PaymentCard(SQLModel, table=True):
    __tablename__ = "payment_cards"

    id: Optional[int] = Field(default=None, primary_key=True)

    number: str = Field(index=True)
    exp_month: int = 0
    exp_year: int = 0
    cvv: str = ""
    holder_name: str = ""
    billing_country: str = ""
    billing_postal: str = ""

    status: str = Field(default=CARD_STATUS_AVAILABLE, index=True)
    bind_count: int = 0

    last_used_at: Optional[datetime] = None
    last_error: str = ""
    bound_job_id: Optional[int] = Field(default=None, index=True)
    note: str = ""

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
