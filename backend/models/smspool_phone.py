from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


SMSPOOL_PHONE_STATUS_AVAILABLE = "available"
SMSPOOL_PHONE_STATUS_IN_USE = "in_use"
SMSPOOL_PHONE_STATUS_COOLING = "cooling"
SMSPOOL_PHONE_STATUS_FAILED = "failed"
SMSPOOL_PHONE_STATUS_BANNED = "banned"
SMSPOOL_PHONE_STATUS_CONSUMED = "consumed"


class SmsPoolPhone(SQLModel, table=True):
    __tablename__ = "smspool_phones"

    id: Optional[int] = Field(default=None, primary_key=True)

    provider: str = Field(default="smspool", index=True)
    country: str = Field(default="", index=True)
    service: str = Field(default="", index=True)
    pool: str = Field(default="", index=True)

    phone: str = Field(index=True)
    orderid: str = Field(default="", index=True)
    status: str = Field(default=SMSPOOL_PHONE_STATUS_AVAILABLE, index=True)

    success_count: int = Field(default=0, index=True)
    cooldown_until: Optional[datetime] = Field(default=None, index=True)
    locked_until: Optional[datetime] = Field(default=None, index=True)
    last_used_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: str = ""
    last_error_kind: str = ""
    metadata_json: str = "{}"

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
