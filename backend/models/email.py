"""Email accounts and per-message captures."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class EmailAccount(SQLModel, table=True):
    __tablename__ = "email_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    email: str = Field(index=True)
    password: str = ""
    refresh_token: str = ""
    api_base: str = ""
    enabled: bool = Field(default=True, index=True)
    metadata_json: str = "{}"

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class EmailMessage(SQLModel, table=True):
    __tablename__ = "email_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: Optional[int] = Field(default=None, index=True)
    job_id: Optional[int] = Field(default=None, index=True)

    email: str = Field(index=True)
    provider: str = Field(default="", index=True)
    subject: str = ""
    sender: str = ""
    body_text: str = ""
    code: str = Field(default="", index=True)
    raw_json: str = "{}"

    received_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
