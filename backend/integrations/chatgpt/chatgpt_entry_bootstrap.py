"""Shared ChatGPT entry bootstrap flow.

This module keeps the HAR-aligned ChatGPT entry prewarm separate from the
registration state machine. It intentionally operates through the client object
so ChatGPTClient can reuse its existing session, fingerprint, retry, and logging
behavior.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from .backend_headers import (
    ACCOUNTS_CHECK_VERSION,
    build_backend_headers,
    compute_passkey_capabilities,
    extract_x_oai_is_from_cookies,
    update_x_oai_is_from_response,
)
from .fingerprint import normalize_impersonate


CHATGPT_BASE = "https://chatgpt.com"
DEFAULT_COUNTRY_CODE = "JP"
COUNTRY_CODE_HINT_RE = re.compile(
    r'["\']?country_code_hint["\']?\s*(?::|,)\s*["\']([A-Za-z0-9]{2,4})["\']',
    re.I,
)
NAVIGATION_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)


def bootstrap_chatgpt_entry(
    client,
    email: str,
    device_id: str = "",
    *,
    csrf_token: str = "",
    user_agent: str | None = None,
    sec_ch_ua: str | None = None,
    impersonate: str | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> str:
    """Prewarm ChatGPT web entry and return the final authorize URL.

    The call sequence mirrors the browser path captured in recent HARs:
    homepage, backend-anon prewarm requests, providers, csrf, signin/openai,
    and finally the auth.openai.com authorize navigation.
    """
    device_id = str(device_id or getattr(client, "device_id", "") or "").strip()
    if not device_id:
        device_id = str(uuid.uuid4())
        try:
            client.device_id = device_id
        except Exception:
            pass

    _ensure_oai_session_id(client)
    ua = str(user_agent or getattr(client, "ua", "") or "").strip() or "Mozilla/5.0"
    sec = str(sec_ch_ua or getattr(client, "sec_ch_ua", "") or "").strip() or None
    imp_raw = str(impersonate or getattr(client, "impersonate", "") or "").strip()
    imp = normalize_impersonate(imp_raw) if imp_raw else None
    if imp:
        try:
            client.impersonate = imp
        except Exception:
            pass

    homepage_url = f"{CHATGPT_BASE}/"
    _visit_homepage(client, homepage_url, ua, sec, imp)
    prewarm_country = _run_backend_anon_prewarm(
        client,
        device_id=device_id,
        user_agent=ua,
        sec_ch_ua=sec,
        impersonate=imp,
        country_code=country_code,
    )

    _request_providers(client, ua, sec, imp)
    token = csrf_token or _request_csrf(client, ua, sec, imp)
    authorize_url = _request_signin(client, email, token, device_id, ua, sec, imp)
    if not authorize_url:
        return ""
    final_url = _follow_authorize(client, authorize_url, ua, sec, imp)
    auth_country = _resolve_country_code(client, prewarm_country)
    if auth_country and auth_country != prewarm_country:
        _request_country_pricing_config(
            client,
            auth_country,
            device_id=device_id,
            user_agent=ua,
            sec_ch_ua=sec,
            impersonate=imp,
        )
    return final_url


def _ensure_oai_session_id(client) -> str:
    session_id = str(getattr(client, "oai_session_id", "") or "").strip()
    if not session_id:
        session_id = str(uuid.uuid4())
        try:
            client.oai_session_id = session_id
        except Exception:
            pass
    return session_id


def _call_headers(
    client,
    url: str,
    *,
    user_agent: str,
    sec_ch_ua: str | None,
    accept: str,
    referer: str | None = None,
    origin: str | None = None,
    content_type: str | None = None,
    navigation: bool = False,
    fetch_site: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers_kwargs = {
        "accept": accept,
        "referer": referer,
        "origin": origin,
        "content_type": content_type,
        "navigation": navigation,
        "fetch_site": fetch_site,
        "extra_headers": extra_headers,
    }
    try:
        return client._headers(
            url,
            user_agent=user_agent,
            sec_ch_ua=sec_ch_ua,
            **headers_kwargs,
        )
    except TypeError:
        return client._headers(url, **headers_kwargs)


def _request_kwargs(impersonate: str | None, **kwargs) -> dict:
    if impersonate:
        kwargs["impersonate"] = normalize_impersonate(impersonate)
    return kwargs


def _visit_homepage(client, url: str, user_agent: str, sec_ch_ua: str | None, impersonate: str | None) -> None:
    try:
        client._log("ChatGPT entry: 访问首页")
        client._browser_pause()
        response = client._session_get(
            url,
            **_request_kwargs(
                impersonate,
                headers=_call_headers(
                    client,
                    url,
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    accept=NAVIGATION_ACCEPT,
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            ),
        )
        _sync_versions(client, response)
        try:
            client.last_homepage_status = int(response.status_code or 0)
            client.last_homepage_url = str(response.url or "")
        except Exception:
            pass
        client._log(f"ChatGPT entry: 首页状态 {getattr(response, 'status_code', '-')}")
    except Exception as exc:
        client._log(f"ChatGPT entry: 首页访问异常: {exc}")


def _run_backend_anon_prewarm(
    client,
    *,
    device_id: str,
    user_agent: str,
    sec_ch_ua: str | None,
    impersonate: str | None,
    country_code: str,
) -> str:
    country = _resolve_country_code(client, country_code)
    steps = [
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/accounts/check/{ACCOUNTS_CHECK_VERSION}?timezone_offset_min={_timezone_offset_min()}"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/me"),
        ("POST", lambda _country: f"{CHATGPT_BASE}/backend-anon/sentinel/chat-requirements/prepare"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/system_hints?mode=basic"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/system_hints?mode=connectors"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/system_hints?mode=custom_agents"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/models?iim=false&is_gizmo=false"),
        ("POST", lambda _country: f"{CHATGPT_BASE}/backend-anon/conversation/init"),
        ("GET", lambda _country: f"{CHATGPT_BASE}/backend-anon/checkout_pricing_config/countries"),
        ("GET", lambda current_country: f"{CHATGPT_BASE}/backend-anon/checkout_pricing_config/configs/{current_country or DEFAULT_COUNTRY_CODE}"),
        ("POST", lambda _country: f"{CHATGPT_BASE}/backend-anon/sentinel/chat-requirements/finalize"),
    ]

    for method, url_builder in steps:
        url = url_builder(country)
        try:
            headers = _backend_anon_headers(
                client,
                url,
                method=method,
                device_id=device_id,
                user_agent=user_agent,
                sec_ch_ua=sec_ch_ua,
            )
            kwargs = _request_kwargs(impersonate, headers=headers, timeout=30)
            if method == "POST":
                kwargs["json"] = {}
                response = client._session_post(url, **kwargs)
            else:
                response = client._session_get(url, **kwargs)
            _sync_versions(client, response)
            try:
                update_x_oai_is_from_response(client.session, response.headers)
            except Exception:
                pass
            client._log(
                f"ChatGPT entry: {method} {url.replace(CHATGPT_BASE, '')} -> "
                f"{getattr(response, 'status_code', '-')}"
            )
            if _is_backend_anon_me_url(url):
                extracted_country = _extract_country_code_from_me_response(response)
                if extracted_country:
                    country = extracted_country
                    _cache_country_code(client, country)
                    client._log(f"ChatGPT entry: backend-anon/me country={country} cached")
                else:
                    client._log(
                        "ChatGPT entry: backend-anon/me country unavailable, "
                        f"fallback={country or DEFAULT_COUNTRY_CODE}"
                    )
            if _is_chat_requirements_url(url):
                client._log(
                    "ChatGPT entry: sentinel chat-requirements "
                    f"{url.rsplit('/', 1)[-1]} summary: "
                    f"{_response_summary(response)}"
                )
        except Exception as exc:
            client._log(f"ChatGPT entry: 预热异常 {method} {url}: {exc}")
    return country or DEFAULT_COUNTRY_CODE


def _backend_anon_headers(
    client,
    url: str,
    *,
    method: str,
    device_id: str,
    user_agent: str,
    sec_ch_ua: str | None,
) -> dict[str, str]:
    accept_language = _accept_language(client)
    return build_backend_headers(
        url=url,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        chrome_full_version=getattr(client, "chrome_full", None),
        sec_ch_ua_platform_version=getattr(client, "sec_ch_ua_platform_version", None),
        accept="*/*",
        accept_language=accept_language,
        referer=f"{CHATGPT_BASE}/",
        content_type="application/json" if str(method).upper() == "POST" else None,
        fetch_site="same-origin",
        device_id=device_id,
        oai_session_id=_ensure_oai_session_id(client),
        oai_client_version=getattr(client, "oai_client_version", ""),
        oai_client_build_number=getattr(client, "oai_client_build_number", ""),
        x_oai_is=extract_x_oai_is_from_cookies(getattr(client, "session", None)),
    )


def _request_providers(client, user_agent: str, sec_ch_ua: str | None, impersonate: str | None) -> None:
    url = f"{CHATGPT_BASE}/api/auth/providers"
    try:
        client._log("ChatGPT entry: 访问 api/auth/providers")
        response = client._session_get(
            url,
            **_request_kwargs(
                impersonate,
                headers=_call_headers(
                    client,
                    url,
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    accept="*/*",
                    referer=f"{CHATGPT_BASE}/",
                    content_type="application/json",
                    fetch_site="same-origin",
                ),
                timeout=30,
            ),
        )
        client._log(f"ChatGPT entry: providers 状态 {getattr(response, 'status_code', '-')}")
    except Exception as exc:
        client._log(f"ChatGPT entry: providers 异常: {exc}")


def _request_csrf(client, user_agent: str, sec_ch_ua: str | None, impersonate: str | None) -> str:
    url = f"{CHATGPT_BASE}/api/auth/csrf"
    try:
        client._log("ChatGPT entry: 获取 CSRF token")
        response = client._session_get(
            url,
            **_request_kwargs(
                impersonate,
                headers=_call_headers(
                    client,
                    url,
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    accept="*/*",
                    referer=f"{CHATGPT_BASE}/",
                    content_type="application/json",
                    fetch_site="same-origin",
                ),
                timeout=30,
            ),
        )
        if getattr(response, "status_code", 0) == 200:
            token = (response.json() or {}).get("csrfToken", "") or ""
            if token:
                client._log(f"ChatGPT entry: CSRF token={token[:16]}...")
                return token
    except Exception as exc:
        client._log(f"ChatGPT entry: 获取 CSRF 异常: {exc}")
    return ""


def _request_signin(
    client,
    email: str,
    csrf_token: str,
    device_id: str,
    user_agent: str,
    sec_ch_ua: str | None,
    impersonate: str | None,
) -> str:
    url = f"{CHATGPT_BASE}/api/auth/signin/openai"
    try:
        client._log("ChatGPT entry: 提交 signin/openai")
        params = {
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": compute_passkey_capabilities(user_agent),
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }
        form_data = {
            "callbackUrl": f"{CHATGPT_BASE}/",
            "csrfToken": csrf_token,
            "json": "true",
        }
        response = client._session_post(
            url,
            **_request_kwargs(
                impersonate,
                params=params,
                data=form_data,
                headers=_call_headers(
                    client,
                    url,
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    accept="*/*",
                    referer=f"{CHATGPT_BASE}/",
                    origin=CHATGPT_BASE,
                    content_type="application/x-www-form-urlencoded",
                    fetch_site="same-origin",
                ),
                timeout=30,
            ),
        )
        if getattr(response, "status_code", 0) == 200:
            authorize_url = (response.json() or {}).get("url", "") or ""
            if authorize_url:
                client._log("ChatGPT entry: 已获取 authorize URL")
                return authorize_url
        client._log(
            f"ChatGPT entry: signin/openai 失败 {getattr(response, 'status_code', '-')}"
        )
    except Exception as exc:
        client._log(f"ChatGPT entry: signin/openai 异常: {exc}")
    return ""


def _follow_authorize(
    client,
    authorize_url: str,
    user_agent: str,
    sec_ch_ua: str | None,
    impersonate: str | None,
) -> str:
    try:
        client._log("ChatGPT entry: 访问 authorize URL")
        client._browser_pause()
        response = client._session_get(
            authorize_url,
            **_request_kwargs(
                impersonate,
                headers=_call_headers(
                    client,
                    authorize_url,
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=f"{CHATGPT_BASE}/",
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            ),
        )
        country = _extract_country_code_hint_from_auth_html(getattr(response, "text", ""))
        if country:
            _cache_country_code(client, country)
            client._log(f"ChatGPT entry: auth country_code_hint={country} cached")
        final_url = str(getattr(response, "url", "") or authorize_url)
        client._log(f"ChatGPT entry: authorize 最终跳转 {final_url[:160]}")
        return final_url
    except Exception as exc:
        client._log(f"ChatGPT entry: authorize 异常: {exc}")
        return authorize_url


def _request_country_pricing_config(
    client,
    country_code: str,
    *,
    device_id: str,
    user_agent: str,
    sec_ch_ua: str | None,
    impersonate: str | None,
) -> None:
    country = _normalize_country_code(country_code)
    if not country:
        return
    url = f"{CHATGPT_BASE}/backend-anon/checkout_pricing_config/configs/{country}"
    try:
        headers = _backend_anon_headers(
            client,
            url,
            method="GET",
            device_id=device_id,
            user_agent=user_agent,
            sec_ch_ua=sec_ch_ua,
        )
        response = client._session_get(
            url,
            **_request_kwargs(impersonate, headers=headers, timeout=30),
        )
        _sync_versions(client, response)
        try:
            update_x_oai_is_from_response(client.session, response.headers)
        except Exception:
            pass
        client._log(
            f"ChatGPT entry: GET /backend-anon/checkout_pricing_config/configs/{country} -> "
            f"{getattr(response, 'status_code', '-')}"
        )
    except Exception as exc:
        client._log(f"ChatGPT entry: country pricing config 异常 {country}: {exc}")


def _accept_language(client) -> str:
    value = str(getattr(client, "accept_language", "") or "").strip()
    if value:
        return value
    try:
        value = str(client.session.headers.get("Accept-Language") or "").strip()
    except Exception:
        value = ""
    return value or "en-US,en;q=0.9"


def _sync_versions(client, response) -> None:
    try:
        client._sync_oai_client_versions(getattr(response, "text", ""))
    except Exception:
        pass


def _resolve_country_code(client, fallback_country_code: str = "") -> str:
    candidates = [
        getattr(client, "chatgpt_country_code", ""),
        getattr(getattr(client, "session", None), "chatgpt_country_code", ""),
        fallback_country_code,
        DEFAULT_COUNTRY_CODE,
    ]
    for candidate in candidates:
        country = _normalize_country_code(candidate)
        if country:
            return country
    return DEFAULT_COUNTRY_CODE


def _cache_country_code(client, country_code: str) -> None:
    country = _normalize_country_code(country_code)
    if not country:
        return
    try:
        client.chatgpt_country_code = country
    except Exception:
        pass
    try:
        client.last_country_code = country
    except Exception:
        pass
    try:
        session = getattr(client, "session", None)
        setattr(session, "chatgpt_country_code", country)
    except Exception:
        pass


def _extract_country_code_hint_from_auth_html(html: str | None) -> str:
    text = str(html or "")
    if not text:
        return ""
    for match in COUNTRY_CODE_HINT_RE.finditer(text):
        country = _normalize_country_code(match.group(1))
        if country:
            return country
    compact = text.replace('\\"', '"').replace("\\'", "'")
    if compact != text:
        for match in COUNTRY_CODE_HINT_RE.finditer(compact):
            country = _normalize_country_code(match.group(1))
            if country:
                return country
    return ""


def _extract_country_code_from_me_response(response) -> str:
    payload = None
    json_fn = getattr(response, "json", None)
    if callable(json_fn):
        try:
            payload = json_fn()
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        return ""

    candidates = [payload.get("country"), payload.get("country_code")]
    nested = payload.get("data")
    if isinstance(nested, dict):
        candidates.extend([nested.get("country"), nested.get("country_code")])

    for candidate in candidates:
        country = _normalize_country_code(candidate)
        if country:
            return country
    return ""


def _normalize_country_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if not text.isalnum():
        return ""
    if len(text) < 2 or len(text) > 4:
        return ""
    return text


def _is_backend_anon_me_url(url: str) -> bool:
    path = str(url or "").split("?", 1)[0]
    return path.endswith("/backend-anon/me")


def _is_chat_requirements_url(url: str) -> bool:
    path = str(url or "").split("?", 1)[0]
    return path.endswith("/backend-anon/sentinel/chat-requirements/prepare") or path.endswith(
        "/backend-anon/sentinel/chat-requirements/finalize"
    )


def _response_summary(response, *, max_body_chars: int = 500) -> str:
    status = getattr(response, "status_code", "-")
    headers = getattr(response, "headers", {}) or {}
    header_parts = []
    for name in (
        "content-type",
        "x-oai-is-update",
        "x-oai-request-id",
        "cf-ray",
    ):
        value = ""
        try:
            value = headers.get(name) or headers.get(name.title()) or ""
        except Exception:
            value = ""
        if value:
            header_parts.append(f"{name}={_clip_inline(value, 96)}")

    body = ""
    try:
        body = getattr(response, "text", "") or ""
    except Exception:
        body = ""
    body = _clip_inline(body, max_body_chars)
    if not body:
        try:
            payload = response.json()
            body = _clip_inline(str(payload), max_body_chars)
        except Exception:
            body = ""

    headers_text = " ".join(header_parts) if header_parts else "-"
    body_text = body if body else "-"
    return f"status={status} headers=[{headers_text}] body={body_text}"


def _clip_inline(value: object, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _timezone_offset_min() -> int:
    try:
        local_now = datetime.now().astimezone()
        offset = local_now.utcoffset()
        return -int((offset.total_seconds() if offset else 0) / 60)
    except Exception:
        return 0
