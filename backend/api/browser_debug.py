"""Browser-debug session API: open/close/list."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.browser_debug import (
    close_debug_session,
    list_open_sessions,
    open_debug_session,
)

router = APIRouter()


class OpenSessionRequest(BaseModel):
    target_url: str | None = None
    account_id: int | None = None
    payment_link_id: int | None = None
    pipeline_id: int | None = None
    job_id: int | None = None
    proxy_url: str | None = None
    browser_type: str = "camoufox"
    inject_cookies: bool = True
    inject_local_storage: bool = True
    inject_fingerprint: bool = True
    record_har: bool = True
    omit_har_content: bool = False


@router.post("/api/browser-debug/open", tags=["browser-debug"])
def open_session(body: OpenSessionRequest):
    info = open_debug_session(
        target_url=body.target_url or "",
        account_id=body.account_id,
        payment_link_id=body.payment_link_id,
        pipeline_id=body.pipeline_id,
        job_id=body.job_id,
        proxy_url=body.proxy_url,
        browser_type=body.browser_type,
        inject_cookies=body.inject_cookies,
        inject_local_storage=body.inject_local_storage,
        inject_fingerprint=body.inject_fingerprint,
        record_har=body.record_har,
        omit_har_content=body.omit_har_content,
    )
    return info


@router.get("/api/browser-debug/sessions", tags=["browser-debug"])
def list_sessions():
    return list_open_sessions()


@router.post("/api/browser-debug/sessions/{session_id}/close", tags=["browser-debug"])
def close_session(session_id: int):
    if not close_debug_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}
