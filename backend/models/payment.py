"""Team hosted payment long-link row."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.constants import (
    PAYMENT_LINK_STATUS_CREATED,
    TEAM_PROMO_CODE,
)
from backend.core.time_utils import utcnow


class PaymentLink(SQLModel, table=True):
    __tablename__ = "payment_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(index=True)
    pipeline_id: Optional[int] = Field(default=None, index=True)
    job_id: Optional[int] = Field(default=None, index=True)

    plan: str = "team"
    promo_code: str = TEAM_PROMO_CODE
    checkout_url: str = ""
    checkout_session_id: str = Field(default="", index=True)
    payload_json: str = "{}"

    status: str = Field(default=PAYMENT_LINK_STATUS_CREATED, index=True)
    error: str = ""

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
