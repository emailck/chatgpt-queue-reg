"""SMS provider project row.

A `SmsProject` is a (provider, business-purpose) pair the `sms_pool` routes
to. Examples:

  - name="stripe_payment" provider="smstome"
  - name="openai_oauth"   provider="smstome"

Stage code calls `sms_pool.acquire(project=...)` to fetch an active number
on the right project.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class SmsProject(SQLModel, table=True):
    __tablename__ = "sms_projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, sa_column_kwargs={"unique": True})
    provider: str = Field(index=True)            # adapter id, e.g. "smstome"
    config_json: str = "{}"                       # api_key/endpoint/country/service code
    enabled: bool = Field(default=True, index=True)
    note: str = ""

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
