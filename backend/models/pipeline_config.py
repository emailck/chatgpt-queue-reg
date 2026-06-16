"""Saved pipeline stage configurations."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class PipelineConfig(SQLModel, table=True):
    __tablename__ = "pipeline_configs"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, sa_column_kwargs={"unique": True})
    stages_json: str = "[]"  # JSON array of stage names, e.g. '["register","openai_oauth"]'
    stop_after: str = ""  # optional stop_after stage name
    note: str = ""  # user note

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
