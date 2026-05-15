"""Persisted proxy entries."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class Proxy(SQLModel, table=True):
    __tablename__ = "proxies"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True, sa_column_kwargs={"unique": True})
    label: str = ""
    region: str = ""
    enabled: bool = Field(default=True, index=True)
    success_count: int = 0
    fail_count: int = 0
    last_used_at: Optional[datetime] = None
    metadata_json: str = "{}"

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
