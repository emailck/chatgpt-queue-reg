"""Pipeline row.

A `pipeline` is a logical user-facing batch unit (one per produced account).
It owns N child `Job`s in a fixed step order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.constants import (
    JOB_STATUS_QUEUED,
    PIPELINE_STEP_REGISTER,
    PIPELINE_TYPE_CHATGPT_ACCOUNT,
)
from backend.core.time_utils import utcnow


class Pipeline(SQLModel, table=True):
    __tablename__ = "pipelines"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(default=PIPELINE_TYPE_CHATGPT_ACCOUNT, index=True)
    status: str = Field(default=JOB_STATUS_QUEUED, index=True)
    current_step: str = Field(default=PIPELINE_STEP_REGISTER, index=True)
    total_steps: int = 3
    completed_steps: int = 0

    account_id: Optional[int] = Field(default=None, index=True)
    payment_link_id: Optional[int] = Field(default=None, index=True)

    proxy_id: Optional[int] = Field(default=None, index=True)
    proxy_url: str = ""

    input_json: str = "{}"
    result_json: str = "{}"
    error: str = ""
    cancel_requested: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)
