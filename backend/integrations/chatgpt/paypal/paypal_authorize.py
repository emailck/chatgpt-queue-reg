from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from .paypal_graphql import graphql_authorize
from .paypal_guest_signup import paypal_guest_signup_authorize
from .paypal_login import paypal_full_login_http
from .runtime import (
    LogFn,
    PayPalHttpError,
    USER_AGENT,
    emit,
    first_match,
    new_http_session,
    paypal_cookie_header,
    query_value,
    seed_paypal_cookies,
)


_AUTHORIZE_QUERY = (
    "mutation authorize($billingAgreementId: String!, $addressId: String, "
    "$fundingPreference: billingFundingPreferenceInput, "
    "$legalAgreements: billingLegalAgreementsInput) { "
    "billing { authorize(billingAgreementId: $billingAgreementId addressId: $addressId "
    "fundingPreference: $fundingPreference legalAgreements: $legalAgreements) "
    "{ billingAgreementToken paymentAction returnURL { href __typename } "
    "buyer { userId __typename } __typename } __typename } }"
)


def authorize_paypal_http(
    approve_url: str,
    paypal_cfg: dict[str, Any],
    proxy_url: str,
    log: LogFn | None,
) -> dict[str, Any]:
    cookies = paypal_cookie_header(paypal_cfg.get("cookies") or paypal_cfg.get("cookie_header") or "")
    email = str(paypal_cfg.get("email") or "").strip()
    password = str(paypal_cfg.get("password") or "").strip()

    phone = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()

    if not cookies and not (email and password) and not (phone and smsurl):
        raise PayPalHttpError("PayPal HTTP 授权需要 paypal.phone/smsurl 或 paypal.cookies 或 paypal.email/password")

    http = new_http_session(proxy_url)
    http.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": str(paypal_cfg.get("accept_language") or "en-US,en;q=0.9"),
        "sec-ch-ua": '"Chromium";v="146", "Google Chrome";v="146", "Not=A?Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
    })
    seed_paypal_cookies(http, cookies)

    resp = http.get(approve_url, allow_redirects=True, timeout=30)
    html = resp.text or ""
    ba_token = query_value(resp.url, "ba_token") or query_value(approve_url, "ba_token")
    emit(log, f"paypal_http: paypal approve status={resp.status_code} ba={bool(ba_token)}")
    if resp.status_code == 403:
        raise PayPalHttpError("PayPal approve 返回 403")

    csrf = first_match([
        r'name="_csrf"\s+value="([^"]+)"',
        r'"csrfNonce"\s*:\s*"([^"]+)"',
        r'"token"\s*:\s*"([^"]{20,})"',
    ], html)
    sid = first_match([r'_sessionID.*?value="([^"]+)"', r'"_sessionID"\s*:\s*"([^"]+)"'], html)
    ctx_id = first_match([r'"ctxId"\s*:\s*"([^"]+)"'], html)
    flow_id = first_match([r'"flowId"\s*:\s*"([^"]+)"'], html) or ctx_id

    if phone and smsurl and not cookies and "/webapps/hermes" not in str(resp.url):
        from .paypal_browser_authorize import browser_paypal_checkout
        address = {
            "first_name": str(paypal_cfg.get("first_name") or ""),
            "last_name": str(paypal_cfg.get("last_name") or ""),
            "line1": str(paypal_cfg.get("billing_line1") or ""),
            "city": str(paypal_cfg.get("billing_city") or ""),
            "state": str(paypal_cfg.get("billing_state") or ""),
            "postal_code": str(paypal_cfg.get("billing_postal") or ""),
        }
        return browser_paypal_checkout(approve_url, ba_token, proxy_url, paypal_cfg, address, log)

    at_hermes = "/webapps/hermes" in str(resp.url)
    logged_in = at_hermes
    ud_return_url = ""
    if not logged_in and cookies:
        ud_resp = http.post(
            "https://www.paypal.com/signin/ud-token",
            data={
                "_csrf": csrf,
                "_sessionID": sid,
                "intent": "checkout",
                "ctxId": ctx_id,
                "flowId": flow_id,
                "returnUri": "/webapps/hermes",
                "locale.x": "en_US",
                "state": urlparse(resp.url).query,
                "fn_sync_data": "",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.paypal.com",
                "Referer": resp.url,
            },
            timeout=30,
        )
        emit(log, f"paypal_http: paypal ud-token status={ud_resp.status_code}")
        if ud_resp.status_code == 200:
            try:
                ud_json = ud_resp.json()
                if ud_json.get("returnUrl") or ud_json.get("email"):
                    logged_in = True
                    ud_return_url = str(ud_json.get("returnUrl") or "")
            except Exception:
                pass

    if not logged_in:
        if not (email and password):
            raise PayPalHttpError("PayPal cookies 未能进入 hermes/ud-token 登录态，且缺少 email/password")
        paypal_full_login_http(http, html, str(resp.url), paypal_cfg, csrf, sid, flow_id, ctx_id, log)
        logged_in = True

    hermes_url = f"https://www.paypal.com/webapps/hermes?flow=1-P&ulReturn=true&ba_token={ba_token}"
    if flow_id:
        hermes_url += f"&token={flow_id}"
    if ud_return_url and "ba_token=" in ud_return_url and "token=" in ud_return_url:
        hermes_url = urljoin("https://www.paypal.com", ud_return_url)

    if at_hermes:
        hermes_html = html
        hermes_final_url = str(resp.url)
    else:
        hermes_resp = http.get(hermes_url, timeout=30)
        hermes_html = hermes_resp.text or ""
        hermes_final_url = str(hermes_resp.url)
        emit(log, f"paypal_http: hermes status={hermes_resp.status_code}")

    return _authorize_from_hermes_html(http, hermes_html, hermes_final_url, ba_token, log)


def authorize_from_hermes(http: Any, hermes_url: str, ba_token: str, log: LogFn | None) -> dict[str, Any]:
    hermes_resp = http.get(hermes_url, allow_redirects=True, timeout=30)
    hermes_html = hermes_resp.text or ""
    hermes_final_url = str(hermes_resp.url)
    emit(log, f"paypal_http: hermes status={hermes_resp.status_code}")
    return _authorize_from_hermes_html(http, hermes_html, hermes_final_url, ba_token, log)


def _authorize_from_hermes_html(http: Any, hermes_html: str, hermes_final_url: str, ba_token: str, log: LogFn | None) -> dict[str, Any]:
    funding_id = first_match([
        r'"fundingOptionId"\s*:\s*"([^"]+)"',
        r'\\"fundingOptionId\\"\s*:\s*\\"([^\\"]+)\\"',
    ], hermes_html)
    ec_token = query_value(hermes_final_url, "token") or first_match([
        r"(EC-[A-Z0-9]{17,})",
        r"(EC-[A-Z0-9-]{17,})",
    ], hermes_html)
    if not ec_token:
        title = first_match([r"<title>(.*?)</title>"], hermes_html, re.I | re.S) or ""
        raise PayPalHttpError(f"PayPal hermes 参数缺失 ec={bool(ec_token)} title={title[:120]}")
    funding_preference: dict[str, Any] = {"balancePreference": "OPT_OUT"}
    if funding_id:
        funding_preference["fundingOptionId"] = funding_id
    payload = [{
        "operationName": "authorize",
        "variables": {
            "billingAgreementId": ec_token,
            "fundingPreference": funding_preference,
            "legalAgreements": {},
        },
        "query": _AUTHORIZE_QUERY,
    }]
    response = graphql_authorize(http, payload, referer=hermes_final_url)
    try:
        return_url = response[0]["data"]["billing"]["authorize"]["returnURL"]["href"]
    except Exception as exc:
        raise PayPalHttpError(f"PayPal graphql 响应缺少 returnURL: {response}") from exc
    ret_resp = http.get(str(return_url), allow_redirects=True, timeout=30)
    emit(log, f"paypal_http: paypal return status={ret_resp.status_code}")
    return {
        "ba_token": ba_token,
        "ec_token": ec_token,
        "return_url": return_url,
        "final_url": str(ret_resp.url),
        "status_code": ret_resp.status_code,
    }
