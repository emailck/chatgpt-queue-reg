"""Job row + JobEvent (append-only log) row."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from backend.core.constants import JOB_STATUS_QUEUED, DEFAULT_MAX_ATTEMPTS
from backend.core.time_utils import utcnow


class Job(SQLModel, table=True):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_type_status", "type", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    pipeline_id: Optional[int] = Field(default=None, index=True)
    # `type` is the stage name, see `backend/core/stages.py`.
    type: str = Field(index=True)
    status: str = Field(default=JOB_STATUS_QUEUED, index=True)
    priority: int = Field(default=0, index=True)
    attempt: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    input_json: str = "{}"
    result_json: str = "{}"
    error: str = ""

    account_id: Optional[int] = Field(default=None, index=True)
    payment_link_id: Optional[int] = Field(default=None, index=True)
    email_address: str = ""
    proxy_id: Optional[int] = Field(default=None, index=True)
    proxy_url: str = ""

    cancel_requested: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    queued_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class JobEvent(SQLModel, table=True):
    __tablename__ = "job_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    pipeline_id: Optional[int] = Field(default=None, index=True)
    level: str = Field(default="info", index=True)
    event_type: str = Field(default="log", index=True)
    message: str = ""
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow, index=True)
