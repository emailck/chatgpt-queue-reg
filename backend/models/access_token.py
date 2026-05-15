"""Access-token only pool.

Records produced by the "register-only" pipeline: ChatGPT account is
registered, but no payment-link step runs.  The row stores everything we'd
later need to keep the access_token alive (refresh_token, cookies, fp,
proxy snapshot).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.time_utils import utcnow


class AccessTokenAccount(SQLModel, table=True):
    __tablename__ = "access_token_accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    pipeline_id: Optional[int] = Field(default=None, index=True)
    chatgpt_account_id: Optional[int] = Field(default=None, index=True)

    email: str = Field(index=True)
    password: str = ""
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

    note: str = ""
    metadata_json: str = "{}"

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
