from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests as std_requests

from .runtime import (
    LogFn,
    PayPalHttpError,
    USER_AGENT,
    emit,
    first_match,
    query_value,
)


def browser_authorize_from_hermes(
    hermes_url: str,
    ba_token: str,
    proxy_url: str,
    log: LogFn | None,
    signup_email: str = "",
    signup_password: str = "",
    http_cookies: list[dict] | None = None,
) -> dict[str, Any]:
    """Drive PayPal hermes authorize via Camoufox browser.

    Falls back to browser when the HTTP-only path fails with ANONYMOUS auth.
    The browser executes PayPal's JS, which establishes the auth state the
    graphql authorize endpoint requires.
    """
    from camoufox.sync_api import Camoufox
    from browserforge.fingerprints import Screen
    from backend.core.proxy import build_playwright_proxy_config, is_authenticated_socks5_proxy

    cf_proxy = _build_camoufox_proxy(proxy_url)

    import tempfile, os
    tmp_profile = tempfile.mkdtemp(prefix="paypal_auth_")
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    emit(log, f"paypal_http: browser authorize starting proxy={'yes' if cf_proxy else 'no'}")

    try:
        with Camoufox(
            headless=not has_display,
            humanize=False,
            persistent_context=True,
            user_data_dir=tmp_profile,
            os="windows",
            screen=Screen(max_width=1920, max_height=1080),
            proxy=cf_proxy,
            geoip=True,
            locale="en-US",
        ) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            if http_cookies:
                _inject_cookies(ctx, http_cookies)

            emit(log, f"paypal_http: browser navigating to hermes")
            page.goto(hermes_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            cur = page.url
            emit(log, f"paypal_http: browser landed on {cur[:100]}")

            if _needs_login(page):
                if not signup_email or not signup_password:
                    raise PayPalHttpError("browser authorize: PayPal 需要登录但缺少 signup_email/password")
                _do_login(page, signup_email, signup_password, log)
                time.sleep(2)
                cur = page.url

            for _ in range(30):
                cur = page.url
                if "/webapps/hermes" in cur or "/pay/" in cur or "/pay?" in cur:
                    break
                if "chatgpt.com" in cur or "pm-redirects" in cur:
                    emit(log, f"paypal_http: browser already completed: {cur[:80]}")
                    return {"ba_token": ba_token, "ec_token": "", "return_url": cur, "final_url": cur, "status_code": 200}
                time.sleep(1)

            hermes_html = page.content()
            hermes_final_url = page.url
            emit(log, f"paypal_http: browser hermes ready at {hermes_final_url[:80]}")

            browser_cookies = ctx.cookies()
            emit(log, f"paypal_http: browser extracted {len(browser_cookies)} cookies")

            return _authorize_with_browser_cookies(
                hermes_html, hermes_final_url, ba_token, browser_cookies, proxy_url, log,
            )
    except PayPalHttpError:
        raise
    except Exception as exc:
        raise PayPalHttpError(f"browser authorize 失败: {exc}") from exc
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_profile, ignore_errors=True)
        except Exception:
            pass


def _build_camoufox_proxy(proxy_url: str) -> dict[str, str] | None:
    from backend.core.proxy import build_playwright_proxy_config, is_authenticated_socks5_proxy

    if not proxy_url:
        return None
    if is_authenticated_socks5_proxy(proxy_url):
        import socket as _sock
        relay_port = 18899
        try:
            with _sock.create_connection(("127.0.0.1", relay_port), timeout=2):
                pass
            return {"server": f"socks5://127.0.0.1:{relay_port}"}
        except Exception:
            raise RuntimeError(f"需要 gost 中继: gost -L=socks5://:{relay_port} -F={proxy_url}")
    return build_playwright_proxy_config(proxy_url)


def _inject_cookies(ctx: Any, http_cookies: list[dict]) -> None:
    try:
        playwright_cookies = []
        for c in http_cookies:
            entry = {
                "name": str(c.get("name") or ""),
                "value": str(c.get("value") or ""),
                "domain": str(c.get("domain") or ".paypal.com"),
                "path": str(c.get("path") or "/"),
            }
            if entry["name"] and entry["value"]:
                playwright_cookies.append(entry)
        if playwright_cookies:
            ctx.add_cookies(playwright_cookies)
    except Exception:
        pass


def _needs_login(page: Any) -> bool:
    url = page.url
    if "/signin" in url or "/authflow" in url:
        return True
    try:
        return bool(page.query_selector('input[name="login_email"], input#email'))
    except Exception:
        return False


def _do_login(page: Any, email: str, password: str, log: LogFn | None) -> None:
    emit(log, "paypal_http: browser login required, filling credentials")

    for sel in ['input[name="login_email"]', 'input#email', 'input[type="email"]']:
        el = page.query_selector(sel)
        if el:
            el.fill(email)
            break
    time.sleep(1)

    next_btn = page.query_selector('button#btnNext, button[type="submit"], button:has-text("Next")')
    if next_btn:
        next_btn.click()
        time.sleep(2)

    for sel in ['input[name="login_password"]', 'input#password', 'input[type="password"]']:
        el = page.query_selector(sel)
        if el:
            el.fill(password)
            break
    time.sleep(1)

    login_btn = page.query_selector('button#btnLogin, button[type="submit"], button:has-text("Log In")')
    if login_btn:
        login_btn.click()
        time.sleep(3)

    for _ in range(15):
        cur = page.url
        if "/webapps/hermes" in cur or "/pay/" in cur or "chatgpt.com" in cur:
            break
        time.sleep(1)

    emit(log, f"paypal_http: browser login done, at {page.url[:80]}")


_AUTHORIZE_QUERY = (
    "mutation authorize($billingAgreementId: String!, $addressId: String, "
    "$fundingPreference: billingFundingPreferenceInput, "
    "$legalAgreements: billingLegalAgreementsInput) { "
    "billing { authorize(billingAgreementId: $billingAgreementId addressId: $addressId "
    "fundingPreference: $fundingPreference legalAgreements: $legalAgreements) "
    "{ billingAgreementToken paymentAction returnURL { href __typename } "
    "buyer { userId __typename } __typename } __typename } }"
)


def _authorize_with_browser_cookies(
    hermes_html: str,
    hermes_url: str,
    ba_token: str,
    browser_cookies: list[dict],
    proxy_url: str,
    log: LogFn | None,
) -> dict[str, Any]:
    funding_id = first_match([
        r'"fundingOptionId"\s*:\s*"([^"]+)"',
        r'\\"fundingOptionId\\"\s*:\s*\\"([^\\"]+)\\"',
    ], hermes_html)
    ec_token = query_value(hermes_url, "token") or first_match([
        r"(EC-[A-Z0-9]{17,})",
    ], hermes_html)
    if not ec_token:
        raise PayPalHttpError(f"browser authorize: hermes 缺少 EC token, url={hermes_url[:120]}")

    emit(log, f"paypal_http: browser authorize funding={bool(funding_id)} ec={bool(ec_token)}")

    http = std_requests.Session()
    http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    try:
        http.trust_env = False
    except Exception:
        pass
    if proxy_url:
        from backend.core.proxy import build_requests_proxy_config
        http.proxies = build_requests_proxy_config(proxy_url) or {}

    for c in browser_cookies:
        if "paypal.com" in c.get("domain", ""):
            http.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ".paypal.com"),
                path=c.get("path", "/"),
            )

    funding_preference: dict[str, Any] = {"balancePreference": "OPT_OUT"}
    if funding_id:
        funding_preference["fundingOptionId"] = funding_id
    gql = [{
        "operationName": "authorize",
        "variables": {
            "billingAgreementId": ec_token,
            "fundingPreference": funding_preference,
            "legalAgreements": {},
        },
        "query": _AUTHORIZE_QUERY,
    }]

    resp = http.post(
        "https://www.paypal.com/graphql/",
        json=gql,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "fetch",
            "x-app-name": "checkoutuinodeweb",
            "Origin": "https://www.paypal.com",
            "Referer": hermes_url,
        },
        timeout=30,
    )
    emit(log, f"paypal_http: browser graphql authorize status={resp.status_code}")
    if resp.status_code != 200:
        raise PayPalHttpError(f"browser graphql authorize 失败 [{resp.status_code}]: {(resp.text or '')[:500]}")
    payload = resp.json()
    try:
        return_url = payload[0]["data"]["billing"]["authorize"]["returnURL"]["href"]
    except Exception as exc:
        raise PayPalHttpError(f"browser graphql authorize 响应缺少 returnURL: {payload}") from exc

    ret_resp = http.get(str(return_url), allow_redirects=True, timeout=30)
    emit(log, f"paypal_http: browser paypal return status={ret_resp.status_code}")
    return {
        "ba_token": ba_token,
        "ec_token": ec_token,
        "return_url": return_url,
        "final_url": str(ret_resp.url),
        "status_code": ret_resp.status_code,
    }
