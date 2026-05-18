"""Pipeline row.

A `pipeline` is a logical user-facing batch unit (one per produced account).
It owns a declared `stages` list (stored in `stages_json`) and walks through
them in order, stopping when `stop_after` is reached or any stage fails.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.constants import JOB_STATUS_QUEUED
from backend.core.time_utils import utcnow


class Pipeline(SQLModel, table=True):
    __tablename__ = "pipelines"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Free-form preset name ("register_only", "account_paid", ...). Purely
    # informational; the actual stage order is in `stages_json`.
    preset: str = Field(default="", index=True)

    status: str = Field(default=JOB_STATUS_QUEUED, index=True)

    # Declarative stage list + optional stop point.
    stages_json: str = Field(default="[]")          # JSON array of stage names
    stop_after: str = Field(default="", index=True)  # "" = run all
    stage_inputs_json: str = Field(default="{}")    # per-stage input dict
    resource_bindings_json: str = Field(default="{}")  # e.g. sms project routing

    # Live progress.
    current_stage: str = Field(default="", index=True)
    total_steps: int = 0
    completed_steps: int = 0

    account_id: Optional[int] = Field(default=None, index=True)
    payment_link_id: Optional[int] = Field(default=None, index=True)

    proxy_id: Optional[int] = Field(default=None, index=True)
    proxy_url: str = ""

    # Original request payload (for re-runs / debugging).
    input_json: str = "{}"
    result_json: str = "{}"
    error: str = ""
    cancel_requested: bool = Field(default=False, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)
