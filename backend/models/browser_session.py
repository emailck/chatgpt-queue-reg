"""Open Camoufox/Playwright debug session metadata.

Live browser/playwright/camoufox handles are kept in
`backend.core.browser_debug.BrowserSessionRegistry` (in-memory) so they don't
get GC'd; this row is a queryable mirror for the UI.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from backend.core.constants import BROWSER_SESSION_STATUS_OPENING
from backend.core.time_utils import utcnow


class BrowserDebugSession(SQLModel, table=True):
    __tablename__ = "browser_debug_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, index=True)
    pipeline_id: Optional[int] = Field(default=None, index=True)
    account_id: Optional[int] = Field(default=None, index=True)
    payment_link_id: Optional[int] = Field(default=None, index=True)

    target_url: str = ""
    browser_type: str = "camoufox"
    proxy_url: str = ""
    user_agent: str = ""
    fingerprint_json: str = "{}"
    cookies_json: str = "[]"
    local_storage_json: str = "{}"
    har_path: str = ""

    status: str = Field(default=BROWSER_SESSION_STATUS_OPENING, index=True)
    error: str = ""

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    closed_at: Optional[datetime] = None
