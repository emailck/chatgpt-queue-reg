"""Local mirror for a Codex RT row owned by sub2api.

`oauth_codex` creates/updates this row after obtaining an OpenAI refresh token,
then uploads the token to sub2api. sub2api owns RT rotation; this table stores
local handoff/status metadata for UI and job orchestration.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class CodexToken(SQLModel, table=True):
    __tablename__ = "codex_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(index=True, sa_column_kwargs={"unique": True})

    refresh_token: str = ""
    access_token: str = ""
    id_token: str = ""

    expires_at: Optional[datetime] = None
    next_refresh_at: Optional[datetime] = Field(default=None, index=True)
    last_refreshed_at: Optional[datetime] = None
    consecutive_failures: int = 0
    alive: bool = Field(default=True, index=True)
    last_error: str = ""

    sub2api_external_id: str = Field(default="", index=True)
    sub2api_status: str = Field(default="pending_upload", index=True)
    sub2api_payload_json: str = "{}"
    uploaded_at: Optional[datetime] = None
    status_checked_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
