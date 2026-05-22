"""Local binding between ChatGPT accounts and sub2api OpenAI accounts."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class Sub2ApiAccountBinding(SQLModel, table=True):
    __tablename__ = "sub2api_account_bindings"

    id: Optional[int] = Field(default=None, primary_key=True)
    chatgpt_account_id: int = Field(index=True)
    platform: str = Field(default="openai", index=True)
    sub2api_account_id: str = Field(default="", index=True)
    sub2api_base_url: str = Field(default="", index=True)
    auth_mode: str = Field(default="chatgpt_web_session", index=True)
    status: str = Field(default="", index=True)
    schedulable: bool = Field(default=True, index=True)
    last_sync_at: Optional[datetime] = Field(default=None, index=True)
    last_refresh_at: Optional[datetime] = None
    last_status_check_at: Optional[datetime] = None
    relogin_required: bool = Field(default=False, index=True)
    last_error: str = ""
    credential_fingerprint: str = Field(default="", index=True)
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
