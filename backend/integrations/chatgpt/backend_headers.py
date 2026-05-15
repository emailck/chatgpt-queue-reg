"""
Backend-API 请求头构造器。

在通用浏览器头（build_browser_headers）之上叠加 ChatGPT backend-api 专用的
OAI-* / X-OpenAI-* / X-OAI-IS / ChatGPT-Account-ID 等业务头。
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from .utils import build_browser_headers

# ---------------------------------------------------------------------------
# 常量 — HAR 稳定值
# ---------------------------------------------------------------------------

ACCOUNTS_CHECK_VERSION = "v4-2023-04-27"
OAI_CLIENT_VERSION = "prod-84bfe620fd5dd2d44306ba8091c5a8429e22c609"
OAI_CLIENT_BUILD_NUMBER = "6473332"

_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.I | re.S)
_ACCOUNTS_CHECK_VERSION_RE = re.escape(ACCOUNTS_CHECK_VERSION)

# ---------------------------------------------------------------------------
# ext-passkey-client-capabilities: 按 UA 计算
#   Firefox 135: only conditionalGet → "0100"
#   Chrome/Chromium 130+: 四者全支持 → "1111"
# ---------------------------------------------------------------------------


def compute_passkey_capabilities(user_agent: str | None = None) -> str:
    ua = (user_agent or "").lower()
    if "firefox" in ua:
        return "0100"
    if "chrome" in ua or "chromium" in ua:
        return "1111"
    return "0100"


# ---------------------------------------------------------------------------
# 路由模板归一化
# ---------------------------------------------------------------------------

_ROUTE_TEMPLATES: dict[str, str] = {
    "/backend-api/payments/checkout": "/backend-api/payments/checkout",
    "/backend-api/payments/checkout/approve": "/backend-api/payments/checkout/approve",
    "/backend-api/me": "/backend-api/me",
    "/backend-api/user_granular_consent": "/backend-api/user_granular_consent",
    "/backend-api/sentinel/ping": "/backend-api/sentinel/ping",
    "/backend-api/sentinel/chat-requirements/prepare": "/backend-api/sentinel/chat-requirements/prepare",
    "/backend-api/sentinel/chat-requirements/finalize": "/backend-api/sentinel/chat-requirements/finalize",
    f"/backend-api/accounts/check/{ACCOUNTS_CHECK_VERSION}": "/backend-api/accounts/check/{version}",
    "/backend-api/wham/usage": "/backend-api/wham/usage",
    "/backend-api/models": "/backend-api/models",
    "/backend-api/system_hints": "/backend-api/system_hints",
    "/backend-api/conversation/init": "/backend-api/conversation/init",
    "/backend-api/checkout_pricing_config/countries": "/backend-api/checkout_pricing_config/countries",
    f"/backend-anon/accounts/check/{ACCOUNTS_CHECK_VERSION}": "/backend-anon/accounts/check/{version}",
    "/backend-anon/me": "/backend-anon/me",
    "/backend-anon/models": "/backend-anon/models",
    "/backend-anon/system_hints": "/backend-anon/system_hints",
    "/backend-anon/sentinel/chat-requirements/prepare": "/backend-anon/sentinel/chat-requirements/prepare",
    "/backend-anon/sentinel/chat-requirements/finalize": "/backend-anon/sentinel/chat-requirements/finalize",
    "/backend-anon/conversation/init": "/backend-anon/conversation/init",
    "/backend-anon/checkout_pricing_config/countries": "/backend-anon/checkout_pricing_config/countries",
}

_ROUTE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(rf"^/backend-api/accounts/check/{_ACCOUNTS_CHECK_VERSION_RE}$"),
        "/backend-api/accounts/check/{version}",
    ),
    (
        re.compile(rf"^/backend-anon/accounts/check/{_ACCOUNTS_CHECK_VERSION_RE}$"),
        "/backend-anon/accounts/check/{version}",
    ),
    (
        re.compile(r"^/backend-api/checkout_pricing_config/configs/[A-Z]{2,4}$"),
        "/backend-api/checkout_pricing_config/configs/{country_code}",
    ),
    (
        re.compile(r"^/backend-anon/checkout_pricing_config/configs/[A-Z]{2,4}$"),
        "/backend-anon/checkout_pricing_config/configs/{country_code}",
    ),
    (
        re.compile(r"^/backend-api/conversation/[a-f0-9-]+/stream_status$"),
        "/backend-api/conversation/{conversation_id}/stream_status",
    ),
    (
        re.compile(r"^/backend-api/conversation/[a-f0-9-]+/textdocs$"),
        "/backend-api/conversation/{conversation_id}/textdocs",
    ),
]


def normalize_route_template(path: str) -> str:
    """将实际请求路径归一化为路由模板。

    Args:
        path: URL path（不含 query string），例如 /backend-api/accounts/check/v4-2023-04-27

    Returns:
        路由模板，例如 /backend-api/accounts/check/{version}
    """
    path = (path or "").strip()
    if not path:
        return "/"

    # 精确匹配优先
    if path in _ROUTE_TEMPLATES:
        return _ROUTE_TEMPLATES[path]

    # 正则匹配
    for pattern, template in _ROUTE_PATTERNS:
        if pattern.match(path):
            return template

    # fallback: 原样返回
    return path


def _extract_html_attr(tag: str, attr_name: str) -> str:
    if not tag:
        return ""
    quoted = re.search(
        rf"\b{re.escape(attr_name)}=(['\"])(?P<value>.*?)\1",
        tag,
        re.I | re.S,
    )
    if quoted:
        return str(quoted.group("value") or "").strip()
    bare = re.search(
        rf"\b{re.escape(attr_name)}=(?P<value>[^\s>]+)",
        tag,
        re.I | re.S,
    )
    if bare:
        return str(bare.group("value") or "").strip()
    return ""


def extract_oai_client_versions_from_homepage_html(html: str | None) -> tuple[str, str]:
    """从 ChatGPT 首页 HTML 的 <html> 标签提取 OAI 版本信息。

    Returns:
        (OAI-Client-Version, OAI-Client-Build-Number)
    """
    text = str(html or "")
    if not text:
        return "", ""

    html_tag = _HTML_TAG_RE.search(text)
    if html_tag:
        tag = html_tag.group(0)
        version = _extract_html_attr(tag, "data-build")
        build_number = _extract_html_attr(tag, "data-seq")
        if version or build_number:
            return version, build_number

    # Fallback: tolerate HTML minifiers or unusual serialization order.
    version = _extract_html_attr(text, "data-build")
    build_number = _extract_html_attr(text, "data-seq")
    if version or build_number:
        return version, build_number

    return "", ""


# ---------------------------------------------------------------------------
# 统一 backend-api 请求头构造
# ---------------------------------------------------------------------------


def build_backend_headers(
    *,
    url: str,
    # browser header params
    user_agent: str,
    sec_ch_ua: str | None = None,
    chrome_full_version: str | None = None,
    sec_ch_ua_platform_version: str | None = None,
    platform: str = "Windows",
    accept: str | None = None,
    accept_language: str = "en-US,en;q=0.9",
    referer: str | None = None,
    origin: str | None = None,
    content_type: str | None = None,
    navigation: bool = False,
    fetch_mode: str | None = None,
    fetch_dest: str | None = None,
    fetch_site: str | None = None,
    headed: bool = False,
    oai_client_version: str | None = None,
    oai_client_build_number: str | None = None,
    # backend-api specific params
    device_id: str = "",
    oai_session_id: str = "",
    access_token: str = "",
    chatgpt_account_id: str = "",
    x_oai_is: str = "",
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """构造 backend-api 请求所需的完整请求头。

    先调用 build_browser_headers() 生成通用浏览器头，再叠加 ChatGPT
    backend-api 专用的 OAI-* / X-OpenAI-* / X-OAI-IS / ChatGPT-Account-ID。

    x_oai_is 应从 cookie __Secure-oai-is 读取后传入；没有则跳过。
    """
    headers = build_browser_headers(
        url=url,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        chrome_full_version=chrome_full_version,
        sec_ch_ua_platform_version=sec_ch_ua_platform_version,
        accept=accept,
        accept_language=accept_language,
        referer=referer,
        origin=origin,
        content_type=content_type,
        navigation=navigation,
        fetch_mode=fetch_mode,
        fetch_dest=fetch_dest,
        fetch_site=fetch_site,
        headed=headed,
        extra_headers=extra_headers,
    )

    path = urlparse(url).path or "/"
    route = normalize_route_template(path)
    client_version = str(oai_client_version or OAI_CLIENT_VERSION).strip()
    client_build_number = str(oai_client_build_number or OAI_CLIENT_BUILD_NUMBER).strip()

    headers["OAI-Language"] = _primary_language(accept_language)
    if device_id:
        headers["OAI-Device-Id"] = device_id
    headers["OAI-Client-Version"] = client_version
    headers["OAI-Client-Build-Number"] = client_build_number
    if oai_session_id:
        headers["OAI-Session-Id"] = oai_session_id
    headers["X-OpenAI-Target-Path"] = path
    headers["X-OpenAI-Target-Route"] = route
    if x_oai_is:
        headers["X-OAI-IS"] = x_oai_is
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if chatgpt_account_id:
        headers["ChatGPT-Account-ID"] = chatgpt_account_id

    return headers


def _primary_language(accept_language: str) -> str:
    """从 Accept-Language 值提取主语言标签。"""
    raw = (accept_language or "en-US").strip()
    first = raw.split(",")[0].strip()
    parts = first.split(";")[0].strip().split("-", 1)
    if len(parts) == 2:
        return f"{parts[0].lower()}-{parts[1].upper()}"
    return parts[0].lower()


def _replace_cookie_value(raw_cookie: str, cookie_name: str, value: str) -> str:
    """在 Cookie 字符串里替换或追加指定 cookie。"""
    raw = str(raw_cookie or "").strip()
    cookie_prefix = f"{cookie_name}="
    parts: list[str] = []
    found = False

    if raw:
        for part in raw.split(";"):
            part_s = part.strip()
            if not part_s:
                continue
            if part_s.startswith(cookie_prefix):
                parts.append(f"{cookie_name}={value}")
                found = True
            else:
                parts.append(part_s)

    if not found:
        parts.append(f"{cookie_name}={value}")

    return "; ".join(parts)


def extract_account_id_from_jwt(access_token: str) -> str:
    """从 access_token JWT 中提取 ChatGPT-Account-ID。"""
    from .utils import decode_jwt_payload

    if not access_token or not access_token.strip():
        return ""
    payload = decode_jwt_payload(access_token)
    auth = payload.get("https://api.openai.com/auth") or {}
    return str(auth.get("chatgpt_account_id") or "").strip()


def extract_x_oai_is_from_cookies(session) -> str:
    """从 session 的 cookie jar 或 Cookie header 中读取 __Secure-oai-is。"""
    # 优先解析 Cookie header（部分 session 只在这层有值）
    try:
        cookie_str = ""
        hdr = getattr(session, "headers", {}) or {}
        cookie_str = str(hdr.get("Cookie") or hdr.get("cookie") or "").strip()
        if cookie_str:
            for part in cookie_str.split(";"):
                part = part.strip()
                if part.startswith("__Secure-oai-is="):
                    return part[len("__Secure-oai-is="):].strip()
    except Exception:
        pass
    # 其次从 cookie jar
    try:
        for cookie in session.cookies:
            if getattr(cookie, "name", "") == "__Secure-oai-is":
                return str(getattr(cookie, "value", "") or "").strip()
    except Exception:
        pass
    return ""


def update_x_oai_is_from_response(target, response_headers) -> str:
    """如果响应包含 X-OAI-IS-Update，同步回写到 session/account 状态。

    返回更新值；如果响应没有携带更新头，则返回空字符串。
    """
    value = response_headers.get("X-OAI-IS-Update") or response_headers.get("x-oai-is-update") or ""
    value = str(value).strip()
    if not value:
        return ""

    cookies_obj = getattr(target, "cookies", None)

    # Write to cookie jar when the target is a session object.
    try:
        if hasattr(cookies_obj, "set"):
            cookies_obj.set("__Secure-oai-is", value, domain=".chatgpt.com", path="/")
    except Exception:
        pass

    # Update the raw Cookie header / string cookie representation.
    try:
        if isinstance(cookies_obj, str):
            updated_cookies = _replace_cookie_value(cookies_obj, "__Secure-oai-is", value)
            setattr(target, "cookies", updated_cookies)
        else:
            updated_cookies = ""
    except Exception:
        updated_cookies = ""

    try:
        hdr = getattr(target, "headers", {}) or {}
        raw = str(hdr.get("Cookie") or hdr.get("cookie") or "")
        if raw:
            hdr["Cookie"] = _replace_cookie_value(raw, "__Secure-oai-is", value)
        elif updated_cookies:
            hdr["Cookie"] = updated_cookies
    except Exception:
        pass

    # Keep a lightweight account-like object in sync too, so callers that only
    # carry `account.cookies` / `account.extra` can reuse the updated value.
    try:
        extra = getattr(target, "extra", None)
        if isinstance(extra, dict):
            extra["x_oai_is"] = value
            if updated_cookies:
                extra["cookies"] = updated_cookies
    except Exception:
        pass

    return value
