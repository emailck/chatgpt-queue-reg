"""Persisted ChatGPT account row.

Also captures browser state (cookies/localStorage/fingerprint) so that
debug browser sessions can later replay the exact context.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.constants import ACCOUNT_STATUS_CREATED
from backend.core.time_utils import utcnow


class ChatGPTAccount(SQLModel, table=True):
    __tablename__ = "chatgpt_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    password: str = ""
    status: str = Field(default=ACCOUNT_STATUS_CREATED, index=True)

    account_id: str = Field(default="", index=True)
    workspace_id: str = ""

    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""

    cookies_json: str = "[]"
    local_storage_json: str = "{}"
    browser_fingerprint_json: str = "{}"
    user_agent: str = ""

    proxy_id: Optional[int] = Field(default=None, index=True)
    proxy_url: str = ""

    last_error: str = ""
    last_payment_link_id: Optional[int] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    registered_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    metadata_json: str = "{}"
