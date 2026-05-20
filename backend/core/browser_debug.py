"""BrowserDebugService.

Public surface (call from anywhere — API, flows, jobs):

    open_debug_session(
        target_url=...,
        account_id=None,
        payment_link_id=None,
        proxy_url=None,
        browser_type="camoufox" | "chromium",
        inject_cookies=True,
        inject_local_storage=True,
        inject_fingerprint=True,
        record_har=True,
        omit_har_content=False,
        har_dir=None,
        log=None,
    ) -> dict

The returned dict contains the persisted `BrowserDebugSession` id, the HAR
path, and the session id (for later `close_debug_session(session_id)`).

Live playwright handles are kept in `BrowserSessionRegistry` so the window
stays open until the user closes it manually or via the API.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.browser_state import (
    cookie_names,
    cookies_for_playwright,
    normalize_local_storage,
    resolve_har_path,
)
from backend.core.constants import (
    BROWSER_SESSION_STATUS_CLOSED,
    BROWSER_SESSION_STATUS_FAILED,
    BROWSER_SESSION_STATUS_OPEN,
    BROWSER_SESSION_STATUS_OPENING,
)
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.proxy import build_playwright_proxy_config
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.browser_session import BrowserDebugSession
from backend.models.payment import PaymentLink

logger = logging.getLogger(__name__)


class BrowserSessionRegistry:
    """In-memory store of live playwright/camoufox handles."""

    def __init__(self) -> None:
        self._items: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def attach(self, session_id: int, handles: dict[str, Any]) -> None:
        with self._lock:
            self._items[session_id] = handles

    def detach(self, session_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._items.pop(session_id, None)

    def is_open(self, session_id: int) -> bool:
        with self._lock:
            return session_id in self._items

    def all_ids(self) -> list[int]:
        with self._lock:
            return list(self._items.keys())


registry = BrowserSessionRegistry()


def _resolve_account_state(account_id: int | None) -> dict[str, Any]:
    if not account_id:
        return {}
    with Session(engine) as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            return {}
        return {
            "cookies": json_loads(account.cookies_json, fallback=[]),
            "local_storage": json_loads(account.local_storage_json, fallback={}),
            "fingerprint": json_loads(account.browser_fingerprint_json, fallback={}),
            "user_agent": account.user_agent,
            "proxy_url": account.proxy_url,
            "email": account.email,
        }


def _resolve_payment_link(payment_link_id: int | None) -> dict[str, Any]:
    if not payment_link_id:
        return {}
    with Session(engine) as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is None:
            return {}
        return {
            "checkout_url": row.checkout_url,
            "account_id": row.account_id,
        }


def open_debug_session(
    *,
    target_url: str,
    account_id: int | None = None,
    payment_link_id: int | None = None,
    pipeline_id: int | None = None,
    job_id: int | None = None,
    proxy_url: str | None = None,
    browser_type: str = "camoufox",
    inject_cookies: bool = True,
    inject_local_storage: bool = True,
    inject_fingerprint: bool = True,
    record_har: bool = True,
    omit_har_content: bool = False,
    har_dir: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log_fn = log or (lambda message: logger.info("[BrowserDebug] %s", message))

    account_state = _resolve_account_state(account_id)
    payment_state = _resolve_payment_link(payment_link_id)
    if account_id is None and payment_state.get("account_id"):
        account_id = int(payment_state.get("account_id") or 0) or None
        account_state = _resolve_account_state(account_id)
    if not target_url:
        target_url = payment_state.get("checkout_url") or "https://chatgpt.com/"

    cookies_source: Any = []
    if inject_cookies:
        cookies_source = account_state.get("cookies") or []
    cookies = cookies_for_playwright(cookies_source) if inject_cookies else []

    local_storage: dict[str, str] = {}
    if inject_local_storage:
        local_storage = normalize_local_storage(account_state.get("local_storage"))

    user_agent = account_state.get("user_agent") if inject_fingerprint else ""
    fingerprint = account_state.get("fingerprint") if inject_fingerprint else {}
    effective_proxy = proxy_url or account_state.get("proxy_url") or ""

    har_path = resolve_har_path(har_dir) if record_har else ""

    with session_scope() as s:
        row = BrowserDebugSession(
            job_id=job_id,
            pipeline_id=pipeline_id,
            account_id=account_id,
            payment_link_id=payment_link_id,
            target_url=target_url,
            browser_type=browser_type,
            proxy_url=effective_proxy,
            user_agent=str(user_agent or ""),
            fingerprint_json=json_dumps(fingerprint or {}),
            cookies_json=json_dumps(cookies or []),
            local_storage_json=json_dumps(local_storage or {}),
            har_path=har_path,
            status=BROWSER_SESSION_STATUS_OPENING,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        session_id = int(row.id or 0)

    log_fn(
        f"opening browser session id={session_id} type={browser_type} target={target_url} "
        f"proxy={'yes' if effective_proxy else 'no'} cookies={cookie_names(cookies)} "
        f"har={har_path or 'off'}"
    )

    try:
        handles = _launch(
            target_url=target_url,
            browser_type=browser_type,
            proxy_url=effective_proxy,
            user_agent=str(user_agent or ""),
            cookies=cookies,
            local_storage=local_storage,
            fingerprint=fingerprint or {},
            har_path=har_path,
            omit_har_content=omit_har_content,
            log=log_fn,
        )
    except Exception as exc:
        with session_scope() as s:
            db_row = s.get(BrowserDebugSession, session_id)
            if db_row is not None:
                db_row.status = BROWSER_SESSION_STATUS_FAILED
                db_row.error = str(exc) or exc.__class__.__name__
                db_row.updated_at = utcnow()
                db_row.closed_at = utcnow()
                s.add(db_row)
        log_fn(f"browser session id={session_id} failed to open: {exc}")
        raise

    registry.attach(session_id, handles)

    with session_scope() as s:
        db_row = s.get(BrowserDebugSession, session_id)
        if db_row is not None:
            db_row.status = BROWSER_SESSION_STATUS_OPEN
            db_row.updated_at = utcnow()
            s.add(db_row)

    return {
        "session_id": session_id,
        "target_url": target_url,
        "har_path": har_path,
        "browser_type": browser_type,
    }


def close_debug_session(session_id: int) -> bool:
    handles = registry.detach(session_id)
    error = ""
    if handles is not None:
        for key in ("page", "context", "browser"):
            obj = handles.get(key)
            if obj is None:
                continue
            try:
                if hasattr(obj, "close"):
                    obj.close()
            except Exception as exc:
                error = f"{key} close error: {exc}"
        camoufox_ctx = handles.get("camoufox_ctx")
        if camoufox_ctx is not None:
            try:
                camoufox_ctx.__exit__(None, None, None)
            except Exception as exc:
                error = f"camoufox close error: {exc}"
        playwright = handles.get("playwright")
        if playwright is not None:
            try:
                playwright.stop()
            except Exception as exc:
                error = f"playwright stop error: {exc}"

    with session_scope() as s:
        row = s.get(BrowserDebugSession, session_id)
        if row is None:
            return False
        row.status = BROWSER_SESSION_STATUS_CLOSED
        row.error = error
        row.updated_at = utcnow()
        row.closed_at = utcnow()
        s.add(row)
    return True


def list_open_sessions() -> list[dict[str, Any]]:
    open_ids = set(registry.all_ids())
    rows: list[dict[str, Any]] = []
    with Session(engine) as s:
        results = s.exec(
            sa_select(BrowserDebugSession).order_by(BrowserDebugSession.id.desc())
        ).scalars()
        for row in results:
            rows.append({
                "id": row.id,
                "status": row.status,
                "is_alive": (row.id or 0) in open_ids,
                "target_url": row.target_url,
                "browser_type": row.browser_type,
                "proxy_url": row.proxy_url,
                "har_path": row.har_path,
                "account_id": row.account_id,
                "payment_link_id": row.payment_link_id,
                "pipeline_id": row.pipeline_id,
                "job_id": row.job_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "closed_at": row.closed_at.isoformat() if row.closed_at else None,
            })
    return rows


# ---- launcher -----------------------------------------------------------------

def _context_options_from_fingerprint(fingerprint: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fingerprint, dict):
        return {}
    out: dict[str, Any] = {}
    direct_keys = {
        "locale",
        "timezone_id",
        "device_scale_factor",
        "is_mobile",
        "has_touch",
        "color_scheme",
    }
    aliases = {
        "timezone": "timezone_id",
        "timezoneId": "timezone_id",
        "deviceScaleFactor": "device_scale_factor",
        "isMobile": "is_mobile",
        "hasTouch": "has_touch",
        "colorScheme": "color_scheme",
    }
    for key in direct_keys:
        value = fingerprint.get(key)
        if value not in (None, ""):
            out[key] = value
    for source, target in aliases.items():
        value = fingerprint.get(source)
        if value not in (None, "") and target not in out:
            out[target] = value
    viewport = fingerprint.get("viewport") or fingerprint.get("screen")
    if isinstance(viewport, dict):
        width = int(viewport.get("width") or viewport.get("w") or 0)
        height = int(viewport.get("height") or viewport.get("h") or 0)
        if width > 0 and height > 0:
            out["viewport"] = {"width": width, "height": height}
    screen = fingerprint.get("screen")
    if isinstance(screen, dict):
        width = int(screen.get("width") or screen.get("w") or 0)
        height = int(screen.get("height") or screen.get("h") or 0)
        if width > 0 and height > 0:
            out["screen"] = {"width": width, "height": height}
    return out


def _launch(
    *,
    target_url: str,
    browser_type: str,
    proxy_url: str,
    user_agent: str,
    cookies: list[dict[str, Any]],
    local_storage: dict[str, str],
    fingerprint: dict[str, Any],
    har_path: str,
    omit_har_content: bool,
    log: Callable[[str], None],
) -> dict[str, Any]:
    proxy_config = build_playwright_proxy_config(proxy_url) if proxy_url else None

    playwright = None
    camoufox_ctx = None

    if browser_type == "camoufox":
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
        except Exception as exc:
            log(f"camoufox unavailable, falling back to chromium: {exc}")
            return _launch(
                target_url=target_url,
                browser_type="chromium",
                proxy_url=proxy_url,
                user_agent=user_agent,
                cookies=cookies,
                local_storage=local_storage,
                fingerprint=fingerprint,
                har_path=har_path,
                omit_har_content=omit_har_content,
                log=log,
            )
        kwargs: dict[str, Any] = {"headless": False}
        if proxy_config:
            kwargs["proxy"] = proxy_config
        camoufox_ctx = Camoufox(**kwargs)
        browser = camoufox_ctx.__enter__()
    else:
        from playwright.sync_api import sync_playwright  # type: ignore

        playwright = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": False}
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config
        browser = playwright.chromium.launch(**launch_kwargs)

    context_kwargs: dict[str, Any] = {"locale": "en-US"}
    context_kwargs.update(_context_options_from_fingerprint(fingerprint))
    if user_agent:
        context_kwargs["user_agent"] = user_agent
    if har_path:
        context_kwargs["record_har_path"] = har_path
        context_kwargs["record_har_omit_content"] = omit_har_content

    context = browser.new_context(**context_kwargs)

    if cookies:
        try:
            context.add_cookies(cookies)
        except Exception as exc:
            log(f"add_cookies failed: {exc}")

    if local_storage:
        # Inject before any chatgpt.com page evaluates by registering an init
        # script that seeds localStorage on document creation.
        try:
            payload = json_dumps(local_storage)
            init_script = (
                "(() => { try { const data = " + payload + "; "
                "for (const k of Object.keys(data)) { "
                "try { window.localStorage.setItem(k, String(data[k])); } catch (e) {} "
                "} } catch (e) {} })();"
            )
            context.add_init_script(init_script)
        except Exception as exc:
            log(f"local_storage init_script failed: {exc}")

    page = context.new_page()
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
        page.bring_to_front()
    except Exception as exc:
        log(f"page.goto({target_url}) failed: {exc}")

    return {
        "browser": browser,
        "context": context,
        "page": page,
        "playwright": playwright,
        "camoufox_ctx": camoufox_ctx,
        "har_path": har_path,
        "started_at": datetime.now().isoformat(),
    }
