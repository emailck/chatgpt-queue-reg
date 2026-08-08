"""TinkMail mailbox registration and OTP polling.

The web app is protected by Cloudflare/Turnstile.  Registration therefore uses
Camoufox (the same anti-CF browser stack already used by payment flows) to get a
real Turnstile token and to keep the resulting authenticated browser state.
Subsequent mailbox polling first tries curl_cffi with the saved cookies, then
falls back to Camoufox with the saved storage/cookies if the HTTP session is not
authenticated.
"""
from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import shutil
import string
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlencode

from sqlmodel import Session
from sqlalchemy import select as sa_select

from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.proxy import build_playwright_proxy_config, build_requests_proxy_config, is_authenticated_socks5_proxy
from backend.core.settings import settings
from backend.models.email import EmailAccount, EmailMessage
import requests as std_requests

try:  # curl_cffi is in the project venv and gives browser-like TLS.
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - import error is raised at call-site.
    curl_requests = None  # type: ignore

logger = logging.getLogger(__name__)

BASE_URL = "https://tinkmail.me"
DEFAULT_X_SIGN = "51b52c1d420e0dafb11da6677095f3fc"
ACCOUNT_TYPE_TINKMAIL = "tinkmail"
PROVIDER_TINKMAIL = "tinkmail"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
DEFAULT_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
SEMANTIC_CODE_RES = (
    re.compile(r"(?is)(?:verification\s+code|temporary\s+verification\s+code|one[-\s]*time\s+(?:password|code)|security\s+code|login\s+code|temporary\s+code|验证码|校验码|动态码|临时代码|登录代码|認證碼|驗證碼)[^0-9]{0,100}(\d{6})"),
    re.compile(r"(?is)\bcode\b[^0-9]{0,50}(\d{6})"),
)
LogFn = Callable[[str], None]


@dataclass
class TinkMailRegistrationResult:
    email: str
    account: str
    password: str
    secure_email: str
    user_id: int = 0
    domains: list[str] = field(default_factory=list)
    secure_email_status: int | None = None
    inbox_folder_id: int | None = None
    client_token: str = ""
    client_token_id: int | None = None
    cookies: list[dict[str, Any]] = field(default_factory=list)
    local_storage: dict[str, str] = field(default_factory=dict)
    session_storage: dict[str, str] = field(default_factory=dict)
    user_agent: str = DEFAULT_UA
    x_sign: str = DEFAULT_X_SIGN
    proxy_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def register_and_store(
    *,
    account: str = "",
    password: str = "",
    secure_email: str = "",
    proxy_url: str = "",
    enabled: bool = True,
    log: LogFn | None = None,
) -> EmailAccount:
    result = register_tinkmail_account(
        account=account,
        password=password,
        secure_email=secure_email,
        proxy_url=proxy_url,
        log=log,
    )
    return persist_tinkmail_account(result, enabled=enabled)


def register_tinkmail_account(
    *,
    account: str = "",
    password: str = "",
    secure_email: str = "",
    proxy_url: str = "",
    headless: bool = True,
    timeout: int = 180,
    log: LogFn | None = None,
) -> TinkMailRegistrationResult:
    account = _normalize_local_part(account) or _random_account()
    password = str(password or "").strip() or f"{account}@tinkmail.me"
    secure_email = str(secure_email or "").strip() or _default_secure_email(account)
    x_sign = settings.get("tinkmail.x_sign", settings.get("tinkmail_x_sign", DEFAULT_X_SIGN)) or DEFAULT_X_SIGN
    ua = settings.get("tinkmail.user_agent", settings.get("tinkmail_user_agent", DEFAULT_UA)) or DEFAULT_UA

    _emit(log, f"TinkMail: registering {account}@tinkmail.me proxy={'yes' if proxy_url else 'no'}")

    def _run(geoip: bool) -> TinkMailRegistrationResult:
        return _register_with_camoufox(
            account=account,
            password=password,
            secure_email=secure_email,
            proxy_url=proxy_url,
            headless=headless,
            timeout=timeout,
            x_sign=x_sign,
            user_agent=ua,
            geoip=geoip,
            log=log,
        )

    try:
        return _run(geoip=True)
    except Exception as exc:
        # Older installs without camoufox[geoip] raise here.  Keep the CF-safe
        # Camoufox browser path but retry without geoip instead of falling back
        # immediately to raw protocol.
        msg = str(exc)
        if "geoip" in msg.lower():
            _emit(log, f"TinkMail: Camoufox geoip unavailable, retry geoip=False: {msg[:180]}")
            return _run(geoip=False)
        raise


def _register_with_camoufox(
    *,
    account: str,
    password: str,
    secure_email: str,
    proxy_url: str,
    headless: bool,
    timeout: int,
    x_sign: str,
    user_agent: str,
    geoip: bool,
    log: LogFn | None,
) -> TinkMailRegistrationResult:
    try:
        from camoufox.sync_api import Camoufox  # type: ignore
        try:
            from browserforge.fingerprints import Screen  # type: ignore
        except Exception:
            Screen = None  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Camoufox 不可用，无法避开 TinkMail Cloudflare/Turnstile: {exc}") from exc

    tmp_profile = tempfile.mkdtemp(prefix="tinkmail_")
    cf_proxy = _build_camoufox_proxy(proxy_url)
    kwargs: dict[str, Any] = {
        "headless": bool(headless),
        "humanize": True,
        "persistent_context": True,
        "user_data_dir": tmp_profile,
        "os": "windows",
        "geoip": bool(geoip),
        "locale": "en-US",
        "i_know_what_im_doing": True,
        "disable_coop": True,
        "config": {"showcursor": False},
    }
    if Screen is not None:
        kwargs["screen"] = Screen(max_width=1920, max_height=1080)
    if cf_proxy:
        kwargs["proxy"] = cf_proxy

    try:
        with Camoufox(**kwargs) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.set_default_timeout(max(30_000, int(timeout or 180) * 1000))
                page.set_extra_http_headers({"accept-language": "en-US,en;q=0.9", "x-sign": x_sign})
            except Exception:
                pass
            _emit(log, "TinkMail: navigating sign-up")
            page.goto(f"{BASE_URL}/sign-up", wait_until="domcontentloaded", timeout=60_000)
            _wait_ready(page, log)
            site_key = _api_fetch_in_page(page, "/api/config/turnstile", x_sign=x_sign, referer=f"{BASE_URL}/sign-up").get("data", {}).get("siteKey")
            if not site_key:
                raise RuntimeError("TinkMail turnstile siteKey missing")
            _emit(log, "TinkMail: solving Turnstile via Camoufox")
            token = _configured_turnstile_token() or ""
            if not token:
                try:
                    token = _turnstile_token(page, site_key=str(site_key), timeout_ms=max(60_000, int(timeout or 180) * 1000))
                except Exception as exc:
                    _emit(log, f"TinkMail: browser Turnstile failed, trying configured solver: {str(exc)[:180]}")
                    token = _solve_turnstile_external(site_key=str(site_key), page_url=f"{BASE_URL}/sign-up", proxy_url=proxy_url, user_agent=user_agent, log=log)
            if not token:
                raise RuntimeError("TinkMail Turnstile token empty（可配置 tinkmail.turnstile_token 或 tinkmail.captcha_api_key/CTF_CAPTCHA_API_KEY）")
            _emit(log, "TinkMail: Turnstile token acquired")

            payload = {
                "isBusiness": False,
                "name": account,
                "account": account,
                "secureEmail": secure_email,
                "password": password,
                "password2": password,
                "agree": True,
                "honeypotGender": "",
                "honeypotNorobot": False,
                "turnstileToken": token,
            }
            signup = _api_fetch_in_page(
                page,
                "/api/sign-up",
                method="POST",
                body=payload,
                x_sign=x_sign,
                referer=f"{BASE_URL}/sign-up",
            )
            if int(signup.get("code", -1)) != 0:
                raise RuntimeError(f"TinkMail sign-up failed: {signup}")
            data = signup.get("data") or {}
            email_addr = str(data.get("account") or f"{account}@tinkmail.me").strip()
            user_id = int(data.get("id") or 0)
            _emit(log, f"TinkMail: registered {email_addr} user_id={user_id or '-'}")

            # Hydrate post-signup state and create a client token.  The web API
            # uses the authenticated browser state; keeping cookies/storage lets
            # later OTP polling replay the same state.
            try:
                page.goto(f"{BASE_URL}/home/inbox", wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass
            folders = _api_fetch_in_page(page, "/api/list-folder", x_sign=x_sign, referer=f"{BASE_URL}/home/inbox")
            inbox_id = _find_inbox_id(folders)
            client_token = ""
            client_token_id: int | None = None
            try:
                created = _api_fetch_in_page(
                    page,
                    "/api/client-token/create-token",
                    method="POST",
                    body={"remark": "chatgpt-queue-reg"},
                    x_sign=x_sign,
                    referer=f"{BASE_URL}/home/settings/client-tokens",
                )
                cdata = created.get("data") if int(created.get("code", -1)) == 0 else {}
                client_token = str((cdata or {}).get("token") or "")
                client_token_id = int((cdata or {}).get("id") or 0) or None
            except Exception as exc:
                _emit(log, f"TinkMail: client-token create skipped: {exc}")
            cookies = ctx.cookies([BASE_URL]) if hasattr(ctx, "cookies") else []
            local_storage = _storage_dump(page, "localStorage")
            session_storage = _storage_dump(page, "sessionStorage")
            return TinkMailRegistrationResult(
                email=email_addr,
                account=account,
                password=password,
                secure_email=secure_email,
                user_id=user_id,
                domains=list(data.get("domains") or ["tinkmail.me"]),
                secure_email_status=data.get("secureEmailStatus"),
                inbox_folder_id=inbox_id,
                client_token=client_token,
                client_token_id=client_token_id,
                cookies=cookies or [],
                local_storage=local_storage,
                session_storage=session_storage,
                user_agent=user_agent,
                x_sign=x_sign,
                proxy_url=proxy_url or "",
                raw={"signup": signup, "folders": folders},
            )
    finally:
        shutil.rmtree(tmp_profile, ignore_errors=True)


def persist_tinkmail_account(result: TinkMailRegistrationResult, *, enabled: bool = True) -> EmailAccount:
    meta = build_metadata(result)
    with session_scope() as s:
        existing = s.exec(sa_select(EmailAccount).where(EmailAccount.email == result.email)).scalars().first()
        if existing is not None:
            existing.provider = PROVIDER_TINKMAIL
            existing.password = result.password
            existing.refresh_token = result.client_token or existing.refresh_token
            existing.api_base = BASE_URL
            existing.enabled = bool(enabled)
            existing.metadata_json = json_dumps(meta)
            s.add(existing)
            s.commit()
            s.refresh(existing)
            s.expunge(existing)
            return existing
        row = EmailAccount(
            provider=PROVIDER_TINKMAIL,
            email=result.email,
            password=result.password,
            refresh_token=result.client_token or "",
            api_base=BASE_URL,
            enabled=bool(enabled),
            metadata_json=json_dumps(meta),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
        return row


def build_metadata(result: TinkMailRegistrationResult | None = None, **kwargs: Any) -> dict[str, Any]:
    if result is None:
        meta = dict(kwargs)
    else:
        meta = {
            "account_type": ACCOUNT_TYPE_TINKMAIL,
            "account": result.account,
            "secure_email": result.secure_email,
            "secure_email_status": result.secure_email_status,
            "user_id": result.user_id,
            "domains": result.domains,
            "inbox_folder_id": result.inbox_folder_id,
            "client_token": result.client_token,
            "client_token_id": result.client_token_id,
            "cookies": result.cookies,
            "local_storage": result.local_storage,
            "session_storage": result.session_storage,
            "user_agent": result.user_agent,
            "x_sign": result.x_sign,
            "proxy_url": result.proxy_url,
            "pool_status": "available",
        }
    meta.setdefault("account_type", ACCOUNT_TYPE_TINKMAIL)
    return meta


def wait_for_tinkmail_otp(
    account: EmailAccount,
    *,
    keyword: str = "",
    code_pattern: str | None = None,
    timeout: int = 180,
    poll_interval: float = 5.0,
    since_dt: datetime | None = None,
    exclude_codes=None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    meta = json_loads(account.metadata_json, fallback={}) or {}
    deadline = time.time() + max(1, int(timeout or 180))
    excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
    last_error = ""
    http_failed_auth = False
    while time.time() < deadline:
        try:
            if not http_failed_auth:
                payload = fetch_mailbox_payload(account, meta=meta)
                data = select_tinkmail_otp(payload, keyword=keyword, code_pattern=code_pattern, since_dt=since_dt, exclude_codes=excluded)
                if data:
                    _emit(log, f"TinkMail received OTP subject={data.get('subject')!r}")
                    return data
                _emit(log, f"TinkMail no matching OTP yet; mails={len(payload.get('emails') or [])}; polling again")
            else:
                break
        except TinkMailAuthError as exc:
            http_failed_auth = True
            last_error = str(exc)
            _emit(log, f"TinkMail HTTP auth failed, fallback browser polling: {last_error}")
            break
        except Exception as exc:
            last_error = str(exc)
            _emit(log, f"TinkMail poll error: {last_error}")
        time.sleep(float(poll_interval or 5.0))

    # Browser fallback keeps the same anti-CF method as registration and can
    # replay cookies/localStorage if a raw HTTP session cannot authenticate.
    remaining = max(1, int(deadline - time.time()))
    try:
        return _wait_for_tinkmail_otp_browser(
            account,
            meta=meta,
            keyword=keyword,
            code_pattern=code_pattern,
            timeout=remaining,
            poll_interval=poll_interval,
            since_dt=since_dt,
            exclude_codes=excluded,
            log=log,
        )
    except TimeoutError:
        raise TimeoutError(f"TinkMail OTP not received within {timeout}s" + (f": {last_error}" if last_error else ""))


class TinkMailAuthError(RuntimeError):
    pass


def fetch_mailbox_payload(account: EmailAccount, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    if curl_requests is None:
        raise RuntimeError("curl_cffi unavailable")
    meta = meta or (json_loads(account.metadata_json, fallback={}) or {})
    session = _build_curl_session(meta)
    folders = _api_fetch_curl(session, "/api/list-folder", meta=meta, referer=f"{BASE_URL}/home/inbox")
    inbox_id = int(meta.get("inbox_folder_id") or _find_inbox_id(folders) or 0)
    if not inbox_id:
        raise RuntimeError("TinkMail inbox folder not found")
    listed = _api_fetch_curl(session, f"/api/list-mail?{urlencode({'folderId': inbox_id, 'page': 1})}", meta=meta, referer=f"{BASE_URL}/home/inbox")
    emails: list[dict[str, Any]] = []
    for item in list(listed.get("data") or []):
        if not isinstance(item, dict):
            continue
        detail_items: list[dict[str, Any]] = []
        mail_id = item.get("id")
        if mail_id:
            try:
                detail = _api_fetch_curl(session, f"/api/read-mail?{urlencode({'mailId': mail_id})}", meta=meta, referer=f"{BASE_URL}/home/inbox")
                detail_items = _normalize_read_mail_data(detail.get("data"))
            except Exception:
                detail_items = []
        if detail_items:
            emails.extend(_normalize_mail_item(x, envelope=item) for x in detail_items)
        else:
            emails.append(_normalize_mail_item(item))
    return {"emails": emails, "raw": {"folders": folders, "list": listed}}


def _wait_for_tinkmail_otp_browser(
    account: EmailAccount,
    *,
    meta: dict[str, Any],
    keyword: str,
    code_pattern: str | None,
    timeout: int,
    poll_interval: float,
    since_dt: datetime | None,
    exclude_codes: set[str],
    log: LogFn | None,
) -> dict[str, Any]:
    try:
        from camoufox.sync_api import Camoufox  # type: ignore
        try:
            from browserforge.fingerprints import Screen  # type: ignore
        except Exception:
            Screen = None  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Camoufox 不可用，无法浏览器收取 TinkMail: {exc}") from exc

    proxy_url = _poll_proxy_url(meta)
    tmp_profile = tempfile.mkdtemp(prefix="tinkmail_poll_")
    cf_proxy = _build_camoufox_proxy(proxy_url)
    kwargs: dict[str, Any] = {
        "headless": True,
        "humanize": True,
        "persistent_context": True,
        "user_data_dir": tmp_profile,
        "os": "windows",
        "locale": "en-US",
        "i_know_what_im_doing": True,
        "disable_coop": True,
        "config": {"showcursor": False},
    }
    if settings.get_bool("tinkmail.browser.geoip", True):
        kwargs["geoip"] = True
    if Screen is not None:
        kwargs["screen"] = Screen(max_width=1920, max_height=1080)
    if cf_proxy:
        kwargs["proxy"] = cf_proxy

    try:
        try:
            cm = Camoufox(**kwargs)
            ctx = cm.__enter__()
        except Exception as exc:
            if "geoip" in str(exc).lower() and kwargs.pop("geoip", None) is not None:
                _emit(log, "TinkMail browser poll: retry Camoufox geoip=False")
                cm = Camoufox(**kwargs)
                ctx = cm.__enter__()
            else:
                raise
        try:
            if meta.get("cookies"):
                try:
                    ctx.add_cookies(list(meta.get("cookies") or []))
                except Exception as exc:
                    _emit(log, f"TinkMail browser poll add_cookies failed: {exc}")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
            _restore_storage(page, meta)
            page.goto(f"{BASE_URL}/home/inbox", wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + max(1, int(timeout or 1))
            while time.time() < deadline:
                try:
                    payload = _fetch_mailbox_payload_in_page(page, account, meta)
                    data = select_tinkmail_otp(payload, keyword=keyword, code_pattern=code_pattern, since_dt=since_dt, exclude_codes=exclude_codes)
                    if data:
                        _emit(log, f"TinkMail browser received OTP subject={data.get('subject')!r}")
                        return data
                    _emit(log, f"TinkMail browser no OTP; mails={len(payload.get('emails') or [])}")
                except Exception as exc:
                    _emit(log, f"TinkMail browser poll error: {exc}")
                time.sleep(float(poll_interval or 5.0))
            raise TimeoutError("TinkMail browser OTP timeout")
        finally:
            cm.__exit__(None, None, None)
    finally:
        shutil.rmtree(tmp_profile, ignore_errors=True)


def _fetch_mailbox_payload_in_page(page: Any, account: EmailAccount, meta: dict[str, Any]) -> dict[str, Any]:
    x_sign = str(meta.get("x_sign") or DEFAULT_X_SIGN)
    folders = _api_fetch_in_page(page, "/api/list-folder", x_sign=x_sign, referer=f"{BASE_URL}/home/inbox")
    inbox_id = int(meta.get("inbox_folder_id") or _find_inbox_id(folders) or 0)
    if not inbox_id:
        raise RuntimeError("TinkMail inbox folder not found")
    listed = _api_fetch_in_page(page, f"/api/list-mail?{urlencode({'folderId': inbox_id, 'page': 1})}", x_sign=x_sign, referer=f"{BASE_URL}/home/inbox")
    emails: list[dict[str, Any]] = []
    for item in list(listed.get("data") or []):
        if not isinstance(item, dict):
            continue
        detail_items: list[dict[str, Any]] = []
        mail_id = item.get("id")
        if mail_id:
            try:
                detail = _api_fetch_in_page(page, f"/api/read-mail?{urlencode({'mailId': mail_id})}", x_sign=x_sign, referer=f"{BASE_URL}/home/inbox")
                detail_items = _normalize_read_mail_data(detail.get("data"))
            except Exception:
                detail_items = []
        if detail_items:
            emails.extend(_normalize_mail_item(x, envelope=item) for x in detail_items)
        else:
            emails.append(_normalize_mail_item(item))
    return {"emails": emails, "raw": {"folders": folders, "list": listed}}


def select_tinkmail_otp(payload: dict[str, Any], *, keyword: str, code_pattern: str | None, since_dt: datetime | None, exclude_codes) -> dict[str, Any] | None:
    emails = list((payload or {}).get("emails") or [])
    emails.sort(key=lambda item: _mail_dt(item).timestamp() if _mail_dt(item) else 0, reverse=True)
    keyword_lower = str(keyword or "").lower().strip()
    excluded = {str(x or "").strip() for x in (exclude_codes or ()) if str(x or "").strip()}
    for item in emails:
        if not isinstance(item, dict):
            continue
        mail_dt = _mail_dt(item)
        if since_dt and mail_dt and mail_dt < since_dt.astimezone(timezone.utc):
            continue
        haystack = "\n".join(str(item.get(k) or "") for k in ("subject", "body", "body_text", "body_preview", "from", "sender", "to"))
        if keyword_lower and keyword_lower not in haystack.lower():
            continue
        code = _extract_code(haystack, code_pattern)
        if code and code not in excluded:
            return {
                "code": code,
                "subject": str(item.get("subject") or ""),
                "sender": str(item.get("from") or item.get("sender") or ""),
                "received_at": str(item.get("date") or item.get("received_at") or item.get("createdAt") or ""),
                "body_text": str(item.get("body") or item.get("body_text") or item.get("body_preview") or ""),
                "folder": "inbox",
                "id": str(item.get("id") or code),
                "raw": item,
            }
    return None


def persist_tinkmail_message(account: EmailAccount, data: dict[str, Any]) -> None:
    with Session(engine) as s:
        s.add(EmailMessage(
            account_id=account.id,
            email=account.email,
            provider=PROVIDER_TINKMAIL,
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=json.dumps(data.get("raw") or {}, ensure_ascii=False, default=str),
        ))
        s.commit()


def _api_fetch_in_page(page: Any, path: str, *, method: str = "GET", body: Any = None, x_sign: str, referer: str) -> dict[str, Any]:
    result = page.evaluate(
        """async ({path, method, body, xSign, referer}) => {
          const headers = { accept: 'application/json,*/*', 'x-sign': xSign };
          if (method !== 'GET') headers['content-type'] = 'application/json';
          const res = await fetch(path, {
            method,
            credentials: 'include',
            mode: 'cors',
            referrer: referer,
            headers,
            body: method === 'GET' ? undefined : JSON.stringify(body || {}),
          });
          const text = await res.text();
          return { status: res.status, ok: res.ok, contentType: res.headers.get('content-type') || '', text };
        }""",
        {"path": path, "method": method.upper(), "body": body, "xSign": x_sign, "referer": referer},
    )
    return _parse_api_result(result, path)


def _api_fetch_curl(session: Any, path: str, *, meta: dict[str, Any], referer: str, method: str = "GET", body: Any = None) -> dict[str, Any]:
    url = BASE_URL + path
    headers = _api_headers(meta, referer=referer, json_body=method.upper() != "GET")
    resp = session.request(method.upper(), url, headers=headers, json=body if method.upper() != "GET" else None, timeout=30, allow_redirects=True)
    text = resp.text or ""
    ctype = resp.headers.get("content-type") or ""
    return _parse_api_result({"status": resp.status_code, "ok": resp.ok, "contentType": ctype, "text": text}, path)


def _parse_api_result(result: dict[str, Any], path: str) -> dict[str, Any]:
    status = int(result.get("status") or 0)
    text = str(result.get("text") or "")
    ctype = str(result.get("contentType") or "").lower()
    if status >= 400:
        raise RuntimeError(f"TinkMail {path} HTTP {status}: {text[:300]}")
    stripped = text.strip()
    if "application/json" not in ctype and stripped.startswith("<!DOCTYPE html") or "Sign In to TinkMail" in stripped[:2000]:
        raise TinkMailAuthError(f"TinkMail {path} returned sign-in/html")
    try:
        data = json.loads(stripped)
    except Exception as exc:
        raise RuntimeError(f"TinkMail {path} non-json: {stripped[:300]}") from exc
    if isinstance(data, dict) and "code" in data and int(data.get("code") or 0) != 0:
        raise RuntimeError(f"TinkMail {path} API error: {data}")
    return data if isinstance(data, dict) else {"code": 0, "data": data}


def _api_headers(meta: dict[str, Any], *, referer: str, json_body: bool = False) -> dict[str, str]:
    headers = {
        "accept": "application/json,*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": referer,
        "user-agent": str(meta.get("user_agent") or DEFAULT_UA),
        "x-sign": str(meta.get("x_sign") or DEFAULT_X_SIGN),
    }
    if json_body:
        headers["content-type"] = "application/json"
        headers["origin"] = BASE_URL
    return headers


def _build_curl_session(meta: dict[str, Any]):
    if curl_requests is None:
        raise RuntimeError("curl_cffi unavailable")
    sess = curl_requests.Session(impersonate="chrome")
    proxy_url = _poll_proxy_url(meta)
    proxies = build_requests_proxy_config(proxy_url) if proxy_url else None
    if proxies:
        sess.proxies = proxies
    for c in list(meta.get("cookies") or []):
        try:
            sess.cookies.set(str(c.get("name") or ""), str(c.get("value") or ""), domain=str(c.get("domain") or "tinkmail.me"), path=str(c.get("path") or "/"))
        except Exception:
            pass
    return sess


def _poll_proxy_url(meta: dict[str, Any]) -> str:
    if settings.get_bool("tinkmail.poll_no_proxy", False):
        return ""
    return str(settings.get("tinkmail.poll_proxy_url", "") or meta.get("proxy_url") or "").strip()


def _wait_ready(page: Any, log: LogFn | None = None) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass
    try:
        page.wait_for_function("() => document.readyState === 'complete' || document.readyState === 'interactive'", timeout=20_000)
    except Exception as exc:
        _emit(log, f"TinkMail: document readiness wait skipped: {exc}")


def _turnstile_token(page: Any, *, site_key: str, timeout_ms: int) -> str:
    return str(page.evaluate(
        """async ({siteKey, timeoutMs}) => {
          async function ensureTurnstile() {
            if (window.turnstile && typeof window.turnstile.render === 'function') return;
            await new Promise((resolve, reject) => {
              const existing = Array.from(document.scripts).find(s => /challenges\.cloudflare\.com\/turnstile/.test(s.src || ''));
              if (existing) { existing.addEventListener('load', resolve, {once:true}); setTimeout(resolve, 2500); return; }
              const s = document.createElement('script');
              s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
              s.async = true; s.defer = true;
              s.onload = resolve;
              s.onerror = () => reject(new Error('turnstile api load failed'));
              document.head.appendChild(s);
            });
            const start = Date.now();
            while (!(window.turnstile && typeof window.turnstile.render === 'function')) {
              if (Date.now() - start > 30000) throw new Error('turnstile api missing');
              await new Promise(r => setTimeout(r, 250));
            }
          }
          await ensureTurnstile();
          return await new Promise((resolve, reject) => {
            const box = document.createElement('div');
            box.id = 'queue-reg-turnstile-' + Math.random().toString(36).slice(2);
            box.style.cssText = 'position:fixed;left:20px;bottom:20px;z-index:999999;background:white;padding:8px;';
            document.body.appendChild(box);
            let widgetId = null;
            let done = false;
            const finish = (value) => { if (!done) { done = true; resolve(value || ''); } };
            const fail = (err) => { if (!done) { done = true; reject(err instanceof Error ? err : new Error(String(err || 'turnstile error'))); } };
            widgetId = window.turnstile.render(box, {
              sitekey: siteKey,
              callback: token => finish(token),
              'error-callback': err => fail(new Error('turnstile error ' + (err || ''))),
              'timeout-callback': () => fail(new Error('turnstile timeout')),
            });
            const started = Date.now();
            const timer = setInterval(() => {
              try {
                const token = window.turnstile.getResponse(widgetId);
                if (token) { clearInterval(timer); finish(token); }
                if (Date.now() - started > timeoutMs) { clearInterval(timer); fail(new Error('turnstile timeout')); }
              } catch (e) {}
            }, 500);
          });
        }""",
        {"siteKey": site_key, "timeoutMs": int(timeout_ms)},
    ) or "")


def _configured_turnstile_token() -> str:
    return str(
        settings.get("tinkmail.turnstile_token", "")
        or settings.get("tinkmail_turnstile_token", "")
        or os.getenv("TINKMAIL_TURNSTILE_TOKEN", "")
        or ""
    ).strip()


def _solve_turnstile_external(*, site_key: str, page_url: str, proxy_url: str, user_agent: str, log: LogFn | None = None) -> str:
    """Optional YesCaptcha-compatible Turnstile solver.

    This is only used when a real browser cannot produce a token (common on
    headless hosts without a usable graphics stack).  Config keys:
      - tinkmail.captcha_api_key / CTF_CAPTCHA_API_KEY
      - tinkmail.captcha_api_url / CTF_CAPTCHA_API_URL (default YesCaptcha)
      - tinkmail.captcha_timeout (default 120)
      - tinkmail.captcha_poll_interval (default 5)
    """
    api_key = str(
        settings.get("tinkmail.captcha_api_key", "")
        or settings.get("captcha_api_key", "")
        or os.getenv("CTF_CAPTCHA_API_KEY", "")
        or ""
    ).strip()
    provider = str(
        settings.get("tinkmail.captcha_provider", "")
        or settings.get("captcha_provider", "")
        or os.getenv("CTF_CAPTCHA_PROVIDER", "")
        or ""
    ).strip().lower()
    api_url = str(
        settings.get("tinkmail.captcha_api_url", "")
        or os.getenv("CTF_CAPTCHA_API_URL", "")
        or ("https://api.anti-captcha.com" if api_key and provider in {"anticaptcha", "anti-captcha"} else "")
        or ("https://api.yescaptcha.com" if api_key else "")
    ).rstrip("/")
    if not api_key or not api_url:
        return ""
    task: dict[str, Any] = {
        "type": "TurnstileTaskProxyless",
        "websiteURL": page_url,
        "websiteKey": site_key,
        "userAgent": user_agent or DEFAULT_UA,
        "action": "sign-up",
    }
    if settings.get_bool("tinkmail.captcha_use_proxy", False) and proxy_url:
        parsed = _proxy_parts_for_solver(proxy_url)
        if parsed:
            task = {"type": "TurnstileTask", "websiteURL": page_url, "websiteKey": site_key, "userAgent": user_agent or DEFAULT_UA, **parsed}
    try:
        create = std_requests.post(f"{api_url}/createTask", json={"clientKey": api_key, "task": task}, timeout=30)
        payload = create.json() or {}
    except Exception as exc:
        _emit(log, f"TinkMail Turnstile solver create error: {exc}")
        return ""
    if payload.get("errorId"):
        _emit(log, f"TinkMail Turnstile solver error: {payload.get('errorCode') or ''} {payload.get('errorDescription') or ''}".strip())
        return ""
    task_id = payload.get("taskId")
    if not task_id:
        _emit(log, f"TinkMail Turnstile solver missing taskId: {payload}")
        return ""
    deadline = time.time() + max(30, settings.get_int("tinkmail.captcha_timeout", 120))
    interval = max(2, settings.get_int("tinkmail.captcha_poll_interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            result = std_requests.post(f"{api_url}/getTaskResult", json={"clientKey": api_key, "taskId": task_id}, timeout=20).json() or {}
        except Exception as exc:
            _emit(log, f"TinkMail Turnstile solver poll error: {exc}")
            continue
        if result.get("status") == "ready":
            sol = result.get("solution") if isinstance(result.get("solution"), dict) else {}
            token = str(sol.get("token") or sol.get("gRecaptchaResponse") or result.get("token") or "").strip()
            if token:
                _emit(log, "TinkMail: Turnstile solver token acquired")
                return token
        if result.get("errorId"):
            _emit(log, f"TinkMail Turnstile solver error: {result.get('errorCode') or ''} {result.get('errorDescription') or ''}".strip())
            return ""
    _emit(log, "TinkMail Turnstile solver timeout")
    return ""


def _proxy_parts_for_solver(proxy_url: str) -> dict[str, Any]:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        if not parsed.hostname or not parsed.port:
            return {}
        ptype = (parsed.scheme or "http").replace("socks5h", "socks5").lower()
        out: dict[str, Any] = {"proxyType": ptype, "proxyAddress": parsed.hostname, "proxyPort": int(parsed.port)}
        if parsed.username:
            out["proxyLogin"] = parsed.username
        if parsed.password:
            out["proxyPassword"] = parsed.password
        return out
    except Exception:
        return {}


def _storage_dump(page: Any, storage_name: str) -> dict[str, str]:
    try:
        return dict(page.evaluate(
            """(storageName) => {
              const store = window[storageName]; const out = {};
              for (let i = 0; i < store.length; i++) { const k = store.key(i); out[k] = store.getItem(k); }
              return out;
            }""",
            storage_name,
        ) or {})
    except Exception:
        return {}


def _restore_storage(page: Any, meta: dict[str, Any]) -> None:
    local_storage = meta.get("local_storage") or {}
    session_storage = meta.get("session_storage") or {}
    try:
        page.evaluate(
            """({localStorageData, sessionStorageData}) => {
              for (const [k,v] of Object.entries(localStorageData || {})) localStorage.setItem(k, String(v));
              for (const [k,v] of Object.entries(sessionStorageData || {})) sessionStorage.setItem(k, String(v));
            }""",
            {"localStorageData": local_storage, "sessionStorageData": session_storage},
        )
    except Exception:
        pass


def _find_inbox_id(folders_payload: dict[str, Any]) -> int | None:
    for item in list((folders_payload or {}).get("data") or []):
        if isinstance(item, dict) and int(item.get("type") or 0) == 1:
            return int(item.get("id") or 0) or None
    return None


def _normalize_read_mail_data(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _normalize_mail_item(item: dict[str, Any], *, envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = dict(envelope or {})
    msg.update(item or {})
    body_text = str(msg.get("text") or msg.get("body") or msg.get("body_text") or msg.get("plain") or "")
    body_html = str(msg.get("html") or msg.get("body_html") or msg.get("htmlBody") or "")
    if body_text and re.search(r"(?is)<(?:!doctype\s+html|html|body|table|div|p|span|style)\b", body_text):
        body_text = _html_to_text(body_text)
    if not body_text and body_html:
        body_text = _html_to_text(body_html)
    if not body_text:
        body_text = str(msg.get("content") or msg.get("bodyPreview") or msg.get("snippet") or "")
    msg["body"] = body_text
    msg["body_text"] = body_text
    msg["body_preview"] = body_text[:500]
    msg.setdefault("sender", msg.get("from") or "")
    msg.setdefault("received_at", msg.get("createdAt") or msg.get("date") or "")
    return msg


def _html_to_text(fragment: str) -> str:
    fragment = html.unescape(str(fragment or ""))
    fragment = re.sub(r"(?is)<(?:style|script)\b[^>]*>.*?</(?:style|script)>", " ", fragment)
    fragment = re.sub(r"(?is)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)</(?:p|div|tr|td|h[1-6]|li)>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    return html.unescape(fragment).strip()


def _extract_code(text: str, pattern: str | None) -> str:
    patterns = []
    if pattern:
        patterns.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
    patterns.extend(SEMANTIC_CODE_RES)
    patterns.append(DEFAULT_CODE_RE)
    for regex in patterns:
        match = regex.search(text or "")
        if match:
            raw = match.group(1) if match.groups() else match.group(0)
            m = DEFAULT_CODE_RE.search(str(raw))
            if m:
                return m.group(1)
    return ""


def _mail_dt(item: dict[str, Any]) -> datetime | None:
    raw = str(item.get("createdAt") or item.get("date") or item.get("received_at") or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_local_part(value: str) -> str:
    text = str(value or "").strip().lower()
    if "@" in text:
        text = text.split("@", 1)[0]
    text = re.sub(r"[^a-z0-9._-]+", "", text)
    text = text.strip("._-")
    return text[:32]


def _random_account() -> str:
    prefix = _normalize_local_part(settings.get("tinkmail.account_prefix", settings.get("tinkmail_account_prefix", "tm"))) or "tm"
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return _normalize_local_part(prefix + suffix)


def _default_secure_email(account: str) -> str:
    configured = settings.get("tinkmail.secure_email", settings.get("tinkmail_secure_email", ""))
    if configured:
        return configured.strip()
    return f"{account}.recovery@hotmail.com"


def _build_camoufox_proxy(proxy_url: str) -> dict[str, str] | None:
    if not proxy_url:
        return None
    if is_authenticated_socks5_proxy(proxy_url):
        import socket as _sock
        relay_port = int(settings.get_int("tinkmail.gost_relay_port", 18899))
        try:
            with _sock.create_connection(("127.0.0.1", relay_port), timeout=2):
                return {"server": f"socks5://127.0.0.1:{relay_port}"}
        except Exception:
            raise RuntimeError(f"需要 gost 中继: gost -L=socks5://:{relay_port} -F={proxy_url}")
    return build_playwright_proxy_config(proxy_url)


def _emit(log: LogFn | None, msg: str) -> None:
    if log:
        log(msg)
    else:
        logger.info(msg)
