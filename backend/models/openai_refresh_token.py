"""OpenAI OAuth refresh-token account rows."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class OpenAIRefreshToken(SQLModel, table=True):
    __tablename__ = "openai_refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(index=True, sa_column_kwargs={"unique": True})

    refresh_token: str = ""
    oauth_access_token: str = ""
    oauth_id_token: str = ""
    oauth_access_expires_at: Optional[datetime] = None

    next_sync_at: Optional[datetime] = Field(default=None, index=True)
    last_sync_at: Optional[datetime] = None
    consecutive_failures: int = 0
    enabled: bool = Field(default=True, index=True)
    last_error: str = ""

    sub2api_account_id: str = Field(default="", index=True)
    sub2api_status: str = Field(default="pending_upload", index=True)
    sub2api_payload_json: str = "{}"
    uploaded_at: Optional[datetime] = None
    status_checked_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
