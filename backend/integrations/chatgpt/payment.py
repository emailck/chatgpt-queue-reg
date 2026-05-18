"""
支付核心逻辑 — 生成 Plus/Team 支付链接、无痕打开浏览器、检测订阅状态
"""

from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from typing import Any, Optional

from curl_cffi import requests as cffi_requests
from backend.core.browser_runtime import ensure_browser_display_available
from backend.core.proxy import build_requests_proxy_config

from .backend_headers import (
    build_backend_headers,
    extract_account_id_from_jwt,
    update_x_oai_is_from_response,
)
from .fingerprint import impersonate_from_user_agent, random_fingerprint

# from ..database.models import Account  # removed: external dep

logger = logging.getLogger(__name__)

PAYMENT_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
TEAM_CHECKOUT_BASE_URL = "https://chatgpt.com/checkout/openai_llc/"
TEAM_PROMO_CODE = "STRIPEATLASGPT4BIZ050126"
TEAM_PROMO_URL = f"https://chatgpt.com/?promoCode={TEAM_PROMO_CODE}"


def build_team_promo_hosted_checkout_payload(
    *,
    workspace_name: str = "MyWorkspace",
    price_interval: str = "month",
    seat_quantity: int = 2,
    country: str = "US",
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "plan_name": "chatgptteamplan",
        "team_plan_data": {
            "workspace_name": workspace_name,
            "price_interval": price_interval,
            "seat_quantity": seat_quantity,
        },
        "billing_details": {"country": country, "currency": currency},
        "cancel_url": TEAM_PROMO_URL,
        "promo_code": TEAM_PROMO_CODE,
        "checkout_ui_mode": "hosted",
    }


def _build_proxies(proxy: Optional[str]) -> Optional[dict]:
    return build_requests_proxy_config(proxy)


_COUNTRY_CURRENCY_MAP = {
    "SG": "SGD",
    "US": "USD",
    "TR": "TRY",
    "JP": "JPY",
    "HK": "HKD",
    "GB": "GBP",
    "EU": "EUR",
    "AU": "AUD",
    "CA": "CAD",
    "IN": "INR",
    "BR": "BRL",
    "MX": "MXN",
}


def _extract_oai_did(cookies_str: str) -> Optional[str]:
    """从 cookie 字符串中提取 oai-device-id"""
    for part in cookies_str.split(";"):
        part = part.strip()
        if part.startswith("oai-did="):
            return part[len("oai-did=") :].strip()
    return None


def _extract_secure_oai_is(cookies_str: str) -> str:
    """从 cookie 字符串中提取 __Secure-oai-is"""
    for part in cookies_str.split(";"):
        part = part.strip()
        if part.startswith("__Secure-oai-is="):
            return part[len("__Secure-oai-is="):].strip()
    return ""


def _sync_x_oai_is_state(account: Any, response_headers) -> str:
    """把响应里的 X-OAI-IS-Update 回写到 account 的 cookies / extra 上。"""
    value = update_x_oai_is_from_response(account, response_headers)
    if not value:
        return ""

    extra = getattr(account, "extra", None)
    if isinstance(extra, dict):
        extra["x_oai_is"] = value
        cookies_str = str(getattr(account, "cookies", "") or "").strip()
        if cookies_str:
            extra["cookies"] = cookies_str
    return value


def _payment_backend_request_context(
    *,
    url: str,
    account: Any,
    oai_session_id: str = "",
    accept_language: str = "en-US,en;q=0.9",
    content_type: str = "application/json",
    oai_client_version: str = "",
    oai_client_build_number: str = "",
) -> tuple[dict[str, str], str]:
    """为支付请求构造 headers 和一致的 impersonate，返回 (headers, impersonate)。"""
    bound_fingerprint = getattr(account, "browser_fingerprint", None)
    if not isinstance(bound_fingerprint, dict):
        bound_fingerprint = {}
    fp = random_fingerprint() if not bound_fingerprint.get("user_agent") else None
    user_agent = str(
        getattr(account, "user_agent", "")
        or bound_fingerprint.get("user_agent")
        or (fp.user_agent if fp is not None else "")
    ).strip()
    sec_ch_ua = str(bound_fingerprint.get("sec_ch_ua") or (fp.sec_ch_ua if fp is not None else ""))
    chrome_full = str(bound_fingerprint.get("chrome_full") or (fp.chrome_full if fp is not None else ""))
    platform_version = str(bound_fingerprint.get("platform_version") or (fp.platform_version if fp is not None else ""))
    platform = str(bound_fingerprint.get("platform") or (fp.platform if fp is not None else "Windows"))
    impersonate = str(bound_fingerprint.get("impersonate") or impersonate_from_user_agent(user_agent))
    access_token = str(getattr(account, "access_token", "") or "").strip()
    if not access_token:
        raise ValueError("账号缺少 access_token")

    cookies_str = str(getattr(account, "cookies", "") or "").strip()
    device_id = _extract_oai_did(cookies_str) or ""
    x_oai_is = _extract_secure_oai_is(cookies_str) or str(
        (getattr(account, "extra", {}) or {}).get("x_oai_is") or ""
    ).strip()
    account_id = extract_account_id_from_jwt(access_token)

    headers = build_backend_headers(
        url=url,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        chrome_full_version=chrome_full,
        sec_ch_ua_platform_version=platform_version,
        platform=platform,
        accept="application/json",
        accept_language=accept_language,
        content_type=content_type,
        fetch_site="same-origin",
        device_id=device_id,
        oai_session_id=oai_session_id or str(uuid.uuid4()),
        access_token=access_token,
        chatgpt_account_id=account_id,
        x_oai_is=x_oai_is,
        oai_client_version=(
            str(oai_client_version or "").strip()
            or str((getattr(account, "extra", {}) or {}).get("oai_client_version") or "").strip()
        ),
        oai_client_build_number=(
            str(oai_client_build_number or "").strip()
            or str((getattr(account, "extra", {}) or {}).get("oai_client_build_number") or "").strip()
        ),
    )

    if cookies_str:
        headers["Cookie"] = cookies_str

    return headers, impersonate


def _parse_cookie_str(cookies_str: str, domain: str) -> list:
    """将 'key=val; key2=val2' 格式解析为 Playwright cookie 列表"""
    cookies = []
    for part in cookies_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            }
        )
    return cookies


def _open_url_system_browser(url: str) -> bool:
    """回退方案：调用系统浏览器以无痕模式打开"""
    platform = sys.platform
    try:
        if platform == "win32":
            for browser, flag in [("chrome", "--incognito"), ("msedge", "--inprivate")]:
                try:
                    subprocess.Popen(f'start {browser} {flag} "{url}"', shell=True)
                    return True
                except Exception:
                    continue
        elif platform == "darwin":
            subprocess.Popen(
                ["open", "-a", "Google Chrome", "--args", "--incognito", url]
            )
            return True
        else:
            for binary in ["google-chrome", "chromium-browser", "chromium"]:
                try:
                    subprocess.Popen([binary, "--incognito", url])
                    return True
                except FileNotFoundError:
                    continue
    except Exception as e:
        logger.warning(f"系统浏览器无痕打开失败: {e}")
    return False


def build_plus_promo_hosted_checkout_payload(
    *,
    country: str = "ID",
    currency: str = "IDR",
    entry_point: str = "all_plans_pricing_modal",
    promo_campaign_id: str = "plus-1-month-free",
) -> dict[str, Any]:
    """Hosted (long-link) Plus checkout payload, mirrors gopay.py step 1."""
    return {
        "entry_point": entry_point,
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency},
        "promo_campaign": {
            "promo_campaign_id": promo_campaign_id,
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }


def generate_plus_link(
    account: Any,
    proxy: Optional[str] = None,
    country: str = "ID",
    *,
    currency: Optional[str] = None,
    entry_point: str = "all_plans_pricing_modal",
    promo_campaign_id: str = "plus-1-month-free",
) -> str:
    """生成 Plus 支付长链。

    默认走 IDR 套餐（GoPay 通道），返回 hosted checkout 的完整 url。
    沿用 `gopay.py` 中 step 1 的 payload 形态。
    """
    effective_currency = currency or _COUNTRY_CURRENCY_MAP.get(country, "USD")
    headers, impersonate = _payment_backend_request_context(
        url=PAYMENT_CHECKOUT_URL,
        account=account,
        oai_client_version=getattr(account, "oai_client_version", ""),
        oai_client_build_number=getattr(account, "oai_client_build_number", ""),
    )

    payload = build_plus_promo_hosted_checkout_payload(
        country=country,
        currency=effective_currency,
        entry_point=entry_point,
        promo_campaign_id=promo_campaign_id,
    )

    resp = cffi_requests.post(
        PAYMENT_CHECKOUT_URL,
        headers=headers,
        json=payload,
        proxies=_build_proxies(proxy),
        timeout=30,
        impersonate=impersonate,
    )
    _sync_x_oai_is_state(account, resp.headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("url"):
        return str(data["url"])
    cs_id = (
        data.get("checkout_session_id")
        or data.get("session_id")
        or data.get("id")
        or ""
    )
    if cs_id:
        return TEAM_CHECKOUT_BASE_URL + str(cs_id)
    raise ValueError(data.get("detail", "API 未返回 checkout_session_id"))


def generate_team_link(
    account: Any,
    workspace_name: str = "MyWorkspace",
    price_interval: str = "month",
    seat_quantity: int = 2,
    proxy: Optional[str] = None,
    country: str = "US",
) -> str:
    """生成 Team 支付链接（后端携带账号 cookie 发请求）"""
    currency = _COUNTRY_CURRENCY_MAP.get(country, "USD")
    headers, impersonate = _payment_backend_request_context(
        url=PAYMENT_CHECKOUT_URL,
        account=account,
        oai_client_version=getattr(account, "oai_client_version", ""),
        oai_client_build_number=getattr(account, "oai_client_build_number", ""),
    )
    headers["Referer"] = TEAM_PROMO_URL

    payload = build_team_promo_hosted_checkout_payload(
        workspace_name=workspace_name,
        price_interval=price_interval,
        seat_quantity=seat_quantity,
        country=country,
        currency=currency,
    )

    resp = cffi_requests.post(
        PAYMENT_CHECKOUT_URL,
        headers=headers,
        json=payload,
        proxies=_build_proxies(proxy),
        timeout=30,
        impersonate=impersonate,
    )
    _sync_x_oai_is_state(account, resp.headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("url"):
        return str(data["url"])
    if "checkout_session_id" in data:
        return TEAM_CHECKOUT_BASE_URL + data["checkout_session_id"]
    raise ValueError(data.get("detail", "API 未返回 checkout_session_id"))


def open_url_incognito(url: str, cookies_str: Optional[str] = None) -> bool:
    """用 Playwright 以无痕模式打开 URL，可注入 cookie"""
    import threading

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright 未安装，回退到系统浏览器")
        return _open_url_system_browser(url)

    def _launch():
        try:
            with sync_playwright() as p:
                ensure_browser_display_available(False)
                browser = p.chromium.launch(headless=False, args=["--incognito"])
                ctx = browser.new_context()
                if cookies_str:
                    ctx.add_cookies(_parse_cookie_str(cookies_str, "chatgpt.com"))
                page = ctx.new_page()
                page.goto(url)
                # 保持窗口打开直到用户关闭
                page.wait_for_timeout(300_000)  # 最多等待 5 分钟
        except Exception as e:
            logger.warning(f"Playwright 无痕打开失败: {e}")

    threading.Thread(target=_launch, daemon=True).start()
    return True


def check_subscription_status(account: Any, proxy: Optional[str] = None) -> str:
    """
    检测账号当前订阅状态。

    Returns:
        'free' / 'plus' / 'team'
    """
    url = "https://chatgpt.com/backend-api/me"
    headers, impersonate = _payment_backend_request_context(url=url, account=account)

    resp = cffi_requests.get(
        url,
        headers=headers,
        proxies=_build_proxies(proxy),
        timeout=20,
        impersonate=impersonate,
    )
    _sync_x_oai_is_state(account, resp.headers)
    resp.raise_for_status()
    data = resp.json()

    # 解析订阅类型
    plan = data.get("plan_type") or ""
    if "team" in plan.lower():
        return "team"
    if "plus" in plan.lower():
        return "plus"

    # 尝试从 orgs 或 workspace 信息判断
    orgs = data.get("orgs", {}).get("data", [])
    for org in orgs:
        settings_ = org.get("settings", {})
        if settings_.get("workspace_plan_type") in ("team", "enterprise"):
            return "team"

    return "free"
