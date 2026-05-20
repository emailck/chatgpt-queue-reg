from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import requests as std_requests

from backend.core.proxy import build_requests_proxy_config

from .payment import _payment_backend_request_context

try:
    from curl_cffi.requests import Session as CurlCffiSession
except Exception:  # pragma: no cover - requests fallback is exercised only when curl_cffi is absent.
    CurlCffiSession = None


STRIPE_API = "https://api.stripe.com"
DEFAULT_STRIPE_PK = (
    "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRac"
    "ViovU3kLKvpkjh7IqkW00iXQsjo3n"
)
STRIPE_VERSION_BASE = "2020-08-27"
STRIPE_VERSION_CUSTOM_CHECKOUT = "2020-08-27;custom_checkout_beta=v1"
STRIPE_VERSION_FULL = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
DEFAULT_STRIPE_RUNTIME_VERSION = "37f1b46e8e"
PAY_OPENAI_ORIGIN = "https://pay.openai.com"
PAY_OPENAI_REFERER = "https://pay.openai.com/"
CHATGPT_APPROVE_URL = "https://chatgpt.com/backend-api/payments/checkout/approve"
CHATGPT_SENTINEL_URL = "https://chatgpt.com/backend-api/sentinel/ping"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

LogFn = Callable[[str, str, dict[str, Any] | None], None]
CheckCancelledFn = Callable[[], None]


class PayPalHttpError(RuntimeError):
    pass


def run_paypal_http_payment(
    *,
    account: Any,
    checkout_url: str,
    checkout_session_id: str = "",
    paypal: dict[str, Any] | None = None,
    billing: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    stripe: dict[str, Any] | None = None,
    proxy_url: str = "",
    chatgpt_proxy_url: str = "",
    log: LogFn | None = None,
    check_cancelled: CheckCancelledFn | None = None,
) -> dict[str, Any]:
    paypal = paypal or {}
    billing = _normalize_billing(billing or {}, account)
    runtime = dict(runtime or {})
    stripe = dict(stripe or {})

    session_id = _extract_session_id(checkout_session_id, checkout_url)
    if not session_id:
        raise PayPalHttpError("checkout_url/payment_link 缺少 cs_ session id")

    stripe_pk = str(
        stripe.get("publishable_key")
        or stripe.get("pk")
        or runtime.get("publishable_key")
        or DEFAULT_STRIPE_PK
    ).strip()
    if not stripe_pk:
        raise PayPalHttpError("缺少 Stripe publishable_key")

    ctx = _build_runtime_context(runtime, session_id, checkout_url)
    http = _new_http_session(proxy_url)
    http.headers.update({
        "User-Agent": str(runtime.get("user_agent") or USER_AGENT),
        "Accept-Language": str(runtime.get("accept_language") or "en-US,en;q=0.9"),
    })

    _emit(log, "paypal_http: stripe init")
    init_resp = _stripe_init(http, stripe_pk, session_id, ctx)
    _merge_init_context(ctx, init_resp)
    _checkpoint(check_cancelled)

    _emit(log, "paypal_http: stripe allowed origins")
    _fetch_allowed_origins(http, stripe_pk, session_id)
    _checkpoint(check_cancelled)

    if ctx.get("use_elements_session"):
        _emit(log, "paypal_http: stripe elements session")
        elements_resp = _fetch_elements_session(http, stripe_pk, session_id, ctx, init_resp)
        _merge_elements_context(ctx, elements_resp)
        _checkpoint(check_cancelled)

    _emit(log, "paypal_http: stripe billing address update")
    _update_payment_page_address(http, stripe_pk, session_id, ctx, billing)
    _checkpoint(check_cancelled)

    _emit(log, "paypal_http: create paypal payment_method")
    pm_id = _create_paypal_payment_method(http, stripe_pk, session_id, billing, ctx)
    _checkpoint(check_cancelled)

    _emit(log, "paypal_http: stripe confirm")
    confirm_data = _confirm_payment(http, stripe_pk, session_id, pm_id, init_resp, ctx)
    redirect_url = _extract_redirect_url(confirm_data)

    if _requires_manual_approval(confirm_data):
        _emit(log, "paypal_http: chatgpt manual approval required")
        _chatgpt_approve(
            account=account,
            session_id=session_id,
            processor_entity=str(runtime.get("processor_entity") or "openai_llc"),
            proxy_url=chatgpt_proxy_url,
            log=log,
        )
        _checkpoint(check_cancelled)
        if not redirect_url:
            redirect_url = _poll_payment_page_redirect(http, stripe_pk, session_id, ctx, log, check_cancelled)

    if not redirect_url:
        redirect_url = _poll_payment_page_redirect(http, stripe_pk, session_id, ctx, log, check_cancelled)
    if not redirect_url:
        raise PayPalHttpError("Stripe confirm/poll 未返回 PayPal redirect_to_url")

    _emit(log, "paypal_http: resolve paypal approve url")
    paypal_approve_url = _resolve_paypal_approve_url(http, redirect_url)
    _checkpoint(check_cancelled)

    paypal_runtime = {**paypal, "_runtime": ctx}
    _emit(log, "paypal_http: authorize paypal billing agreement")
    paypal_result = _authorize_paypal_http(paypal_approve_url, paypal_runtime, proxy_url, log)
    _checkpoint(check_cancelled)

    _emit(log, "paypal_http: stripe allowed origins after return")
    _fetch_allowed_origins(http, stripe_pk, session_id)
    _checkpoint(check_cancelled)

    _emit(log, "paypal_http: stripe poll result")
    poll_data = _poll_result(http, stripe_pk, session_id, runtime, log, check_cancelled)

    state = str(poll_data.get("state") or "paypal_authorized")
    return {
        "state": state,
        "checkout_session_id": session_id,
        "checkout_url": checkout_url,
        "payment_method_id": pm_id,
        "stripe_redirect_url": redirect_url,
        "paypal_approve_url": paypal_approve_url,
        "paypal_final_url": paypal_result.get("final_url", ""),
        "paypal_ba_token": paypal_result.get("ba_token", ""),
        "paypal_ec_token": paypal_result.get("ec_token", ""),
        "stripe_poll_state": state,
        "stripe_payment_status": poll_data.get("payment_object_status") or poll_data.get("payment_status") or "",
        "stripe_poll": poll_data,
    }


def _new_http_session(proxy_url: str = "", impersonate: str = "chrome136"):
    if CurlCffiSession is not None:
        session = CurlCffiSession(impersonate=impersonate or "chrome136")
    else:
        session = std_requests.Session()
    try:
        session.trust_env = False
    except Exception:
        pass
    proxies = build_requests_proxy_config(proxy_url)
    if hasattr(session, "proxies"):
        session.proxies = proxies or {"http": "", "https": ""}
    return session


def _stripe_headers(referer: str = PAY_OPENAI_REFERER) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PAY_OPENAI_ORIGIN,
        "Referer": referer,
        "sec-ch-ua": '"Chromium";v="146", "Google Chrome";v="146", "Not=A?Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }


def _extract_session_id(checkout_session_id: str, checkout_url: str) -> str:
    value = str(checkout_session_id or "").strip()
    if value.startswith("cs_"):
        return value
    text = str(checkout_url or "")
    match = re.search(r"(cs_(?:live|test)_[A-Za-z0-9]+)", text)
    return match.group(1) if match else ""


def _gen_fingerprint() -> str:
    return str(uuid.uuid4()) + uuid.uuid4().hex[:5]


def _gen_elements_session_id() -> str:
    return "elements_session_" + uuid.uuid4().hex[:24]


def _short_random_id(length: int = 13) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def _build_runtime_context(runtime: dict[str, Any], session_id: str, checkout_url: str) -> dict[str, Any]:
    version = str(runtime.get("version") or runtime.get("runtime_version") or DEFAULT_STRIPE_RUNTIME_VERSION)
    stripe_js_id = str(runtime.get("stripe_js_id") or runtime.get("client_session_id") or uuid.uuid4())
    elements_session_id = str(runtime.get("elements_session_id") or _gen_elements_session_id())
    elements_session_config_id = str(runtime.get("elements_session_config_id") or uuid.uuid4())
    checkout_config_id = str(runtime.get("checkout_config_id") or runtime.get("top_checkout_config_id") or uuid.uuid4())
    pm_checkout_config_id = str(
        runtime.get("payment_method_checkout_config_id")
        or runtime.get("checkout_config_id")
        or checkout_config_id
    )
    return_url = str(runtime.get("return_url") or "")
    if not return_url:
        return_url = _hosted_return_url(session_id)
    return {
        "guid": str(runtime.get("guid") or _gen_fingerprint()),
        "muid": str(runtime.get("muid") or _gen_fingerprint()),
        "sid": str(runtime.get("sid") or _gen_fingerprint()),
        "runtime_version": version,
        "version": version,
        "stripe_js_id": stripe_js_id,
        "client_session_id": stripe_js_id,
        "elements_session_id": elements_session_id,
        "elements_session_config_id": elements_session_config_id,
        "checkout_config_id": checkout_config_id,
        "top_checkout_config_id": checkout_config_id,
        "payment_method_checkout_config_id": pm_checkout_config_id,
        "browser_locale": str(runtime.get("browser_locale") or "en-US"),
        "locale": str(runtime.get("locale") or "en"),
        "browser_timezone": str(runtime.get("browser_timezone") or "Asia/Taipei"),
        "eid": str(runtime.get("eid") or "NA"),
        "return_url": return_url,
        "checkout_url": checkout_url,
        "js_checksum": str(runtime.get("js_checksum") or ""),
        "rv_timestamp": str(runtime.get("rv_timestamp") or ""),
        "passive_captcha_token": str(runtime.get("passive_captcha_token") or ""),
        "passive_captcha_ekey": str(runtime.get("passive_captcha_ekey") or ""),
        "time_on_page": str(runtime.get("time_on_page") or random.randint(25000, 55000)),
        "use_elements_session": not _falsey(runtime.get("use_elements_session")),
        "poll_attempts": int(runtime.get("poll_attempts") or 30),
        "poll_interval_seconds": float(runtime.get("poll_interval_seconds") or 2),
        "redirect_poll_seconds": float(runtime.get("redirect_poll_seconds") or 60),
        "pre_tax_country": str(runtime.get("pre_tax_country") or "CA"),
        "pre_tax_state": str(runtime.get("pre_tax_state") or "ON"),
        "pre_tax_postal_code": str(runtime.get("pre_tax_postal_code") or "L4W 3Z1"),
        "pre_tax_line1": str(runtime.get("pre_tax_line1") or "5500 Dixie Road"),
        "pre_tax_line2": str(runtime.get("pre_tax_line2") or "Unit G"),
        "pre_tax_city": str(runtime.get("pre_tax_city") or "Mississauga"),
        "paypal_address_autocomplete": not _falsey(runtime.get("paypal_address_autocomplete")),
    }


def _hosted_return_url(session_id: str) -> str:
    return (
        f"{PAY_OPENAI_ORIGIN}/c/pay/{session_id}?"
        + urlencode({"redirect_pm_type": "paypal", "lid": str(uuid.uuid4()), "ui_mode": "hosted"})
    )


def _stripe_init(session: Any, pk: str, session_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
    url = f"{STRIPE_API}/v1/payment_pages/{session_id}/init"
    data = {
        "key": pk,
        "eid": ctx["eid"],
        "browser_locale": ctx["browser_locale"],
        "browser_timezone": ctx["browser_timezone"],
        "redirect_type": "url",
    }
    resp = session.post(url, data=data, headers=_stripe_headers(), timeout=30)
    if resp.status_code == 400 and "parameter_unknown" in _short_body(resp):
        resp = session.post(url, data={"key": pk, "eid": ctx["eid"]}, headers=_stripe_headers(), timeout=30)
    payload = _json_response(resp, "stripe init")
    if not payload.get("init_checksum"):
        raise PayPalHttpError(f"stripe init 未返回 init_checksum: {_short_body(resp)}")
    return payload


def _merge_init_context(ctx: dict[str, Any], init_resp: dict[str, Any]) -> None:
    eid = init_resp.get("eid")
    if isinstance(eid, str) and eid:
        ctx["address_eid"] = eid
    customer_email = init_resp.get("customer_email")
    if isinstance(customer_email, str) and customer_email:
        ctx["customer_email"] = customer_email


def _fetch_allowed_origins(session: Any, pk: str, session_id: str) -> dict[str, Any]:
    resp = session.get(
        f"{STRIPE_API}/v1/payment_pages/allowed_origins",
        params={"key": pk, "session_id": session_id},
        headers=_stripe_headers(),
        timeout=30,
    )
    return _json_response(resp, "stripe allowed_origins")


def _fetch_elements_session(
    session: Any,
    pk: str,
    session_id: str,
    ctx: dict[str, Any],
    init_resp: dict[str, Any],
) -> dict[str, Any]:
    url = f"{STRIPE_API}/v1/elements/sessions"
    currency = str(
        init_resp.get("currency")
        or (init_resp.get("invoice") or {}).get("currency")
        or "usd"
    ).lower()
    amount = _expected_amount(init_resp)
    params = {
        "client_betas[0]": "google_pay_beta_1",
        "client_betas[1]": "disable_deferred_intent_client_validation_beta_1",
        "client_betas[2]": "blocked_card_brands_beta_2",
        "deferred_intent[mode]": "subscription",
        "deferred_intent[amount]": amount,
        "deferred_intent[currency]": currency,
        "deferred_intent[setup_future_usage]": "off_session",
        "deferred_intent[payment_method_types][0]": "card",
        "currency": currency,
        "key": pk,
        "elements_init_source": "checkout",
        "hosted_surface": "checkout",
        "referrer_host": "pay.openai.com",
        "stripe_js_id": ctx["stripe_js_id"],
        "locale": ctx["browser_locale"],
        "type": "deferred_intent",
        "checkout_session_id": session_id,
    }
    resp = session.get(url, params=params, headers=_stripe_headers(), timeout=30)
    return _json_response(resp, "stripe elements session")


def _merge_elements_context(ctx: dict[str, Any], elements_resp: dict[str, Any]) -> None:
    if not isinstance(elements_resp, dict):
        return
    elements_id = elements_resp.get("id") or elements_resp.get("session_id")
    if isinstance(elements_id, str) and elements_id.startswith("elements_session_"):
        ctx["elements_session_id"] = elements_id
    config_id = (
        elements_resp.get("elements_session_config_id")
        or elements_resp.get("configuration_id")
        or elements_resp.get("config_id")
        or (elements_resp.get("configuration") or {}).get("id")
    )
    if isinstance(config_id, str) and config_id:
        ctx["elements_session_config_id"] = config_id


def _update_payment_page_address(
    session: Any,
    pk: str,
    session_id: str,
    ctx: dict[str, Any],
    billing: dict[str, Any],
) -> None:
    url = f"{STRIPE_API}/v1/payment_pages/{session_id}"
    addr = billing.get("address") or {}
    base = {
        "eid": str(ctx.get("address_eid") or ctx.get("eid") or "NA"),
        "key": pk,
    }
    country = str(addr.get("country") or "US")
    pre_country = str(ctx.get("pre_tax_country") or "CA")
    steps = [
        {
            "tax_region[country]": pre_country,
            "tax_region[state]": str(ctx.get("pre_tax_state") or ""),
            "tax_region[postal_code]": str(ctx.get("pre_tax_postal_code") or ""),
            "tax_region[line1]": str(ctx.get("pre_tax_line1") or ""),
            "tax_region[line2]": str(ctx.get("pre_tax_line2") or ""),
            "tax_region[city]": str(ctx.get("pre_tax_city") or ""),
        },
        {
            "tax_region[country]": pre_country,
            "tax_region[postal_code]": str(ctx.get("pre_tax_postal_code") or ""),
            "tax_region[line1]": str(ctx.get("pre_tax_line1") or ""),
            "tax_region[line2]": str(ctx.get("pre_tax_line2") or ""),
            "tax_region[city]": str(ctx.get("pre_tax_city") or ""),
        },
        {"tax_region[country]": country},
        {
            "tax_id_collection[purchasing_as_business]": "false",
            "tax_id_collection[tax_id]": "",
        },
        {
            "tax_region[country]": country,
            "tax_region[state]": addr.get("state", ""),
            "tax_region[postal_code]": addr.get("postal_code", ""),
            "tax_region[line1]": addr.get("line1", ""),
            "tax_region[city]": addr.get("city", ""),
        },
    ]
    for step in steps:
        data = {**base, **step}
        resp = session.post(url, data=data, headers=_stripe_headers(), timeout=30)
        _json_response(resp, "stripe address update")


def _create_paypal_payment_method(
    session: Any,
    pk: str,
    session_id: str,
    billing: dict[str, Any],
    ctx: dict[str, Any],
) -> str:
    addr = billing.get("address") or {}
    data = {
        "type": "paypal",
        "billing_details[email]": str(billing.get("email") or ctx.get("customer_email") or "buyer@example.com"),
        "billing_details[address][country]": str(addr.get("country") or "US"),
        "billing_details[address][line1]": str(addr.get("line1") or ""),
        "billing_details[address][city]": str(addr.get("city") or ""),
        "billing_details[address][postal_code]": str(addr.get("postal_code") or ""),
        "billing_details[address][state]": str(addr.get("state") or ""),
        "guid": ctx["guid"],
        "muid": ctx["muid"],
        "sid": ctx["sid"],
        "_stripe_version": STRIPE_VERSION_CUSTOM_CHECKOUT,
        "key": pk,
        "payment_user_agent": f"stripe.js/{ctx['runtime_version']}; stripe-js-v3/{ctx['runtime_version']}; checkout",
        "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
        "client_attribution_metadata[checkout_session_id]": session_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "hosted_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[checkout_config_id]": ctx["payment_method_checkout_config_id"],
    }
    resp = session.post(f"{STRIPE_API}/v1/payment_methods", data=data, headers=_stripe_headers(), timeout=30)
    payload = _json_response(resp, "stripe paypal payment_method")
    pm_id = str(payload.get("id") or "")
    if not pm_id.startswith("pm_"):
        raise PayPalHttpError(f"stripe payment_methods 未返回 pm_: {payload}")
    return pm_id


def _confirm_payment(
    session: Any,
    pk: str,
    session_id: str,
    pm_id: str,
    init_resp: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    data = {
        "guid": ctx["guid"],
        "muid": ctx["muid"],
        "sid": ctx["sid"],
        "eid": ctx["eid"],
        "payment_method": pm_id,
        "expected_amount": _expected_amount(init_resp),
        "consent[terms_of_service]": "accepted",
        "expected_payment_method_type": "paypal",
        "return_url": ctx["return_url"],
        "key": pk,
        "_stripe_version": STRIPE_VERSION_CUSTOM_CHECKOUT,
        "init_checksum": str(init_resp.get("init_checksum") or ""),
        "version": ctx["runtime_version"],
        "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
        "client_attribution_metadata[checkout_session_id]": session_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "hosted_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[checkout_config_id]": ctx["top_checkout_config_id"],
        "link_brand": "link",
    }
    for key in ("js_checksum", "rv_timestamp", "passive_captcha_token", "passive_captcha_ekey"):
        if ctx.get(key):
            data[key] = ctx[key]
    resp = session.post(
        f"{STRIPE_API}/v1/payment_pages/{session_id}/confirm",
        data=data,
        headers=_stripe_headers(),
        timeout=30,
    )
    return _json_response(resp, "stripe confirm")


def _expected_amount(init_resp: dict[str, Any]) -> str:
    total_summary = init_resp.get("total_summary") or {}
    if total_summary.get("due") is not None:
        return str(total_summary["due"])
    invoice = init_resp.get("invoice") or {}
    if invoice.get("amount_due") is not None:
        return str(invoice["amount_due"])
    line_items = init_resp.get("line_items") or []
    if isinstance(line_items, list) and line_items:
        try:
            return str(sum(int((item or {}).get("amount") or 0) for item in line_items if isinstance(item, dict)))
        except Exception:
            pass
    return "0"


def _requires_manual_approval(payload: dict[str, Any]) -> bool:
    return "requires_approval" in json.dumps(payload, ensure_ascii=False).lower()


def _chatgpt_approve(
    *,
    account: Any,
    session_id: str,
    processor_entity: str,
    proxy_url: str,
    log: LogFn | None,
) -> None:
    headers, impersonate = _payment_backend_request_context(url=CHATGPT_SENTINEL_URL, account=account)
    http = _new_http_session(proxy_url, impersonate=impersonate)
    try:
        http.post(CHATGPT_SENTINEL_URL, json={}, headers=headers, timeout=20)
    except Exception as exc:
        _emit(log, f"paypal_http: sentinel ping skipped: {exc}", level="warning")

    headers, _ = _payment_backend_request_context(url=CHATGPT_APPROVE_URL, account=account)
    resp = http.post(
        CHATGPT_APPROVE_URL,
        json={"checkout_session_id": session_id, "processor_entity": processor_entity},
        headers=headers,
        timeout=30,
    )
    payload = _json_response(resp, "chatgpt checkout approve")
    result = payload.get("result") or payload.get("status")
    if result not in ("approved", "success", True):
        raise PayPalHttpError(f"chatgpt approve 未通过: {payload}")


def _poll_payment_page_redirect(
    session: Any,
    pk: str,
    session_id: str,
    ctx: dict[str, Any],
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None,
) -> str:
    deadline = time.time() + float(ctx.get("redirect_poll_seconds") or 60)
    last = ""
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": ctx["elements_session_id"],
        "elements_session_client[stripe_js_id]": ctx["stripe_js_id"],
        "elements_session_client[locale]": ctx["locale"],
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[stripe_js_locale]": "auto",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": pk,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    while time.time() < deadline:
        _checkpoint(check_cancelled)
        resp = session.get(f"{STRIPE_API}/v1/payment_pages/{session_id}", params=params, headers=_stripe_headers(), timeout=30)
        if resp.status_code == 200:
            payload = resp.json() or {}
            redirect_url = _extract_redirect_url(payload)
            if redirect_url:
                return redirect_url
            setup_intent = _find_setup_intent(payload) or {}
            last = f"status={payload.get('status')} payment_status={payload.get('payment_status')} setup={setup_intent.get('status')}"
        else:
            last = f"http={resp.status_code} body={_short_body(resp)}"
        _emit(log, f"paypal_http: waiting redirect ({last})")
        time.sleep(1)
    raise PayPalHttpError(f"等待 Stripe PayPal redirect 超时: {last}")


def _resolve_paypal_approve_url(session: Any, redirect_url: str) -> str:
    current = str(redirect_url or "").strip()
    for _ in range(6):
        if _is_paypal_approve_url(current):
            return current
        resp = session.get(current, allow_redirects=False, headers=_stripe_headers(), timeout=30)
        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        if not location and _is_paypal_approve_url(str(resp.url)):
            return str(resp.url)
        if not location:
            raise PayPalHttpError(f"pm redirect 未返回 PayPal Location: status={resp.status_code} body={_short_body(resp)}")
        current = urljoin(current, location)
    raise PayPalHttpError("pm redirect 跳转层数过多，未到 PayPal approve")


def _is_paypal_approve_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.netloc.endswith("paypal.com") and "/agreements/approve" in parsed.path


def _authorize_paypal_http(
    approve_url: str,
    paypal_cfg: dict[str, Any],
    proxy_url: str,
    log: LogFn | None,
) -> dict[str, Any]:
    cookies = _paypal_cookie_header(paypal_cfg.get("cookies") or paypal_cfg.get("cookie_header") or "")
    email = str(paypal_cfg.get("email") or "").strip()
    password = str(paypal_cfg.get("password") or "").strip()

    phone = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()

    if not cookies and not (email and password) and not (phone and smsurl):
        raise PayPalHttpError("PayPal HTTP 授权需要 paypal.phone/smsurl 或 paypal.cookies 或 paypal.email/password")

    http = _new_http_session(proxy_url)
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
    _seed_paypal_cookies(http, cookies)

    resp = http.get(approve_url, allow_redirects=True, timeout=30)
    html = resp.text or ""
    ba_token = _query_value(resp.url, "ba_token") or _query_value(approve_url, "ba_token")
    _emit(log, f"paypal_http: paypal approve status={resp.status_code} ba={bool(ba_token)}")
    if resp.status_code == 403:
        raise PayPalHttpError("PayPal approve 返回 403")

    csrf = _first_match([
        r'name="_csrf"\s+value="([^"]+)"',
        r'"csrfNonce"\s*:\s*"([^"]+)"',
        r'"token"\s*:\s*"([^"]{20,})"',
    ], html)
    sid = _first_match([r'_sessionID.*?value="([^"]+)"', r'"_sessionID"\s*:\s*"([^"]+)"'], html)
    ctx_id = _first_match([r'"ctxId"\s*:\s*"([^"]+)"'], html)
    flow_id = _first_match([r'"flowId"\s*:\s*"([^"]+)"'], html) or ctx_id

    if phone and smsurl and not cookies and "/webapps/hermes" not in str(resp.url):
        return _paypal_guest_signup_authorize(http, str(resp.url), html, ba_token, paypal_cfg, log)

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
        _emit(log, f"paypal_http: paypal ud-token status={ud_resp.status_code}")
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
        _paypal_full_login_http(http, html, str(resp.url), paypal_cfg, csrf, sid, flow_id, ctx_id, log)
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
        _emit(log, f"paypal_http: hermes status={hermes_resp.status_code}")

    funding_id = _first_match([
        r'"fundingOptionId"\s*:\s*"([^"]+)"',
        r'\\"fundingOptionId\\"\s*:\s*\\"([^\\"]+)\\"',
    ], hermes_html)
    ec_token = _query_value(hermes_final_url, "token") or _first_match([
        r"(EC-[A-Z0-9]{17,})",
        r"(EC-[A-Z0-9-]{17,})",
    ], hermes_html)
    if not ec_token:
        title = _first_match([r"<title>(.*?)</title>"], hermes_html, re.I | re.S) or ""
        raise PayPalHttpError(f"PayPal hermes 参数缺失 ec={bool(ec_token)} title={title[:120]}")

    funding_preference = {"balancePreference": "OPT_OUT"}
    if funding_id:
        funding_preference["fundingOptionId"] = funding_id
    gql = [{
        "operationName": "authorize",
        "variables": {
            "billingAgreementId": ec_token,
            "fundingPreference": funding_preference,
            "legalAgreements": {},
        },
        "query": (
            "mutation authorize("
            "$billingAgreementId: String!, $addressId: String, "
            "$fundingPreference: billingFundingPreferenceInput, "
            "$legalAgreements: billingLegalAgreementsInput"
            ") { billing { authorize( "
            "billingAgreementId: $billingAgreementId "
            "addressId: $addressId "
            "fundingPreference: $fundingPreference "
            "legalAgreements: $legalAgreements "
            ") { billingAgreementToken paymentAction "
            "returnURL { href __typename } "
            "buyer { userId __typename } __typename } __typename } }"
        ),
    }]
    gql_resp = http.post(
        "https://www.paypal.com/graphql/",
        json=gql,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "fetch",
            "X-App-Name": "checkoutuinodeweb",
            "Origin": "https://www.paypal.com",
            "Referer": hermes_final_url,
        },
        timeout=30,
    )
    payload = _json_response(gql_resp, "paypal graphql authorize")
    try:
        return_url = payload[0]["data"]["billing"]["authorize"]["returnURL"]["href"]
    except Exception as exc:
        raise PayPalHttpError(f"PayPal graphql 响应缺少 returnURL: {payload}") from exc

    ret_resp = http.get(str(return_url), allow_redirects=True, timeout=30)
    _emit(log, f"paypal_http: paypal return status={ret_resp.status_code}")
    return {
        "ba_token": ba_token,
        "ec_token": ec_token,
        "return_url": return_url,
        "final_url": str(ret_resp.url),
        "status_code": ret_resp.status_code,
    }


def _paypal_guest_signup_authorize(
    http: Any,
    approve_url: str,
    approve_html: str,
    ba_token: str,
    paypal_cfg: dict[str, Any],
    log: LogFn | None,
) -> dict[str, Any]:
    phone_raw = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if not phone_raw or not smsurl:
        raise PayPalHttpError("PayPal guest signup 缺少 phone/smsurl")
    phone_country = str(paypal_cfg.get("phone_country") or paypal_cfg.get("country") or "US").upper()
    phone_country_code = str(paypal_cfg.get("phone_country_code") or _phone_country_code(phone_country))
    phone_number = _strip_phone_country_code(phone_raw, phone_country_code)
    country = str(paypal_cfg.get("country") or "US").upper()
    lang = str(paypal_cfg.get("lang") or "en")
    runtime_ctx = paypal_cfg.get("_runtime") if isinstance(paypal_cfg.get("_runtime"), dict) else {}

    signup_url = _paypal_signup_url(http, approve_url, ba_token, log)
    signup_resp = http.get(signup_url, allow_redirects=True, timeout=30)
    signup_url = str(signup_resp.url)
    html = signup_resp.text or approve_html
    ec_token = _query_value(signup_url, "token") or _first_match([r"(EC-[A-Z0-9]{17,})", r"(EC-[A-Z0-9-]{17,})"], html)
    if not ec_token:
        raise PayPalHttpError("PayPal checkoutweb signup 未返回 EC token")
    _emit(log, f"paypal_http: paypal guest signup ec={bool(ec_token)}")

    _paypal_graphql(http, _paypal_deferred_feature_payload(ec_token, country), signup_url, "paypal DeferredFeature")
    _paypal_graphql(http, _paypal_griffin_metadata_payload(country, lang), signup_url, "paypal GriffinMetadataQuery")
    _paypal_graphql(http, _paypal_checkout_session_payload(ec_token), signup_url, "paypal CheckoutSessionDataQuery")
    if runtime_ctx.get("paypal_address_autocomplete"):
        _paypal_address_autocomplete(http, signup_url, paypal_cfg, runtime_ctx, country, lang)

    init_data = _paypal_graphql(
        http,
        _paypal_initiate_phone_payload(ec_token, phone_number, phone_country, country, lang),
        signup_url,
        "paypal InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    phone_state = _extract_phone_confirmation(init_data)
    otp = _fetch_paypal_otp({**paypal_cfg, "otp_file": paypal_cfg.get("otp_file") or "", "smsurl": smsurl}, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("PayPal phone OTP 获取失败")
    confirm_data = _paypal_graphql(
        http,
        _paypal_confirm_phone_payload(ec_token, phone_state["authId"], phone_state["challengeId"], otp),
        signup_url,
        "paypal ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    _extract_phone_confirmation(confirm_data)

    signup_payload = _paypal_signup_payload(ec_token, paypal_cfg, phone_number, phone_country_code, country)
    signup_data = _paypal_graphql(http, signup_payload, signup_url, "paypal SignUpNewMemberMutation")
    access_token = _extract_paypal_buyer_access_token(signup_data)
    if access_token:
        http.headers.update({"Authorization": f"Bearer {access_token}"})

    drop_resp = http.get("https://www.paypal.com/checkoutweb/drop", headers={"Referer": signup_url}, allow_redirects=True, timeout=30)
    hermes_url = str(drop_resp.url)
    if "/webapps/hermes" not in hermes_url:
        hermes_url = _paypal_hermes_url(signup_url, ba_token, ec_token)
    return _paypal_authorize_from_hermes(http, hermes_url, ba_token, log)



def _paypal_signup_url(http: Any, approve_url: str, ba_token: str, log: LogFn | None) -> str:
    parsed = urlparse(approve_url)
    query = parse_qs(parsed.query)
    if "ssrt" not in query:
        query["ssrt"] = [str(int(time.time() * 1000))]
    query.setdefault("ul", ["1"])
    query.setdefault("modxo_redirect_reason", ["guest_user"])
    query.setdefault("ulOnboardRedirect", ["true"])
    query["ba_token"] = [ba_token]
    query.setdefault("locale.x", ["en_US"])
    query.setdefault("country.x", ["US"])
    url = "https://www.paypal.com/agreements/approve?" + urlencode({k: v[-1] for k, v in query.items()})
    resp = http.get(url, allow_redirects=False, timeout=30)
    location = resp.headers.get("Location") or resp.headers.get("location") or ""
    _emit(log, f"paypal_http: paypal guest approve redirect status={resp.status_code}")
    if location:
        return urljoin("https://www.paypal.com", location)
    return str(getattr(resp, "url", "") or url)



def _paypal_authorize_from_hermes(http: Any, hermes_url: str, ba_token: str, log: LogFn | None) -> dict[str, Any]:
    hermes_resp = http.get(hermes_url, allow_redirects=True, timeout=30)
    hermes_html = hermes_resp.text or ""
    hermes_final_url = str(hermes_resp.url)
    _emit(log, f"paypal_http: hermes status={hermes_resp.status_code}")
    funding_id = _first_match([
        r'"fundingOptionId"\s*:\s*"([^"]+)"',
        r'\\"fundingOptionId\\"\s*:\s*\\"([^\\"]+)\\"',
    ], hermes_html)
    ec_token = _query_value(hermes_final_url, "token") or _first_match([
        r"(EC-[A-Z0-9]{17,})",
        r"(EC-[A-Z0-9-]{17,})",
    ], hermes_html)
    if not ec_token:
        title = _first_match([r"<title>(.*?)</title>"], hermes_html, re.I | re.S) or ""
        raise PayPalHttpError(f"PayPal hermes 参数缺失 ec={bool(ec_token)} title={title[:120]}")
    funding_preference = {"balancePreference": "OPT_OUT"}
    if funding_id:
        funding_preference["fundingOptionId"] = funding_id
    gql = [{
        "operationName": "authorize",
        "variables": {
            "billingAgreementId": ec_token,
            "fundingPreference": funding_preference,
            "legalAgreements": {},
        },
        "query": (
            "mutation authorize("
            "$billingAgreementId: String!, $addressId: String, "
            "$fundingPreference: billingFundingPreferenceInput, "
            "$legalAgreements: billingLegalAgreementsInput"
            ") { billing { authorize( "
            "billingAgreementId: $billingAgreementId "
            "addressId: $addressId "
            "fundingPreference: $fundingPreference "
            "legalAgreements: $legalAgreements "
            ") { billingAgreementToken paymentAction "
            "returnURL { href __typename } "
            "buyer { userId __typename } __typename } __typename } }"
        ),
    }]
    payload = _paypal_graphql(http, gql, hermes_final_url, "paypal graphql authorize")
    try:
        return_url = payload[0]["data"]["billing"]["authorize"]["returnURL"]["href"]
    except Exception as exc:
        raise PayPalHttpError(f"PayPal graphql 响应缺少 returnURL: {payload}") from exc
    ret_resp = http.get(str(return_url), allow_redirects=True, timeout=30)
    _emit(log, f"paypal_http: paypal return status={ret_resp.status_code}")
    return {
        "ba_token": ba_token,
        "ec_token": ec_token,
        "return_url": return_url,
        "final_url": str(ret_resp.url),
        "status_code": ret_resp.status_code,
    }



def _paypal_graphql(http: Any, payload: Any, referer: str, label: str) -> Any:
    op = ""
    if isinstance(payload, dict):
        op = str(payload.get("operationName") or "")
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        op = str(payload[0].get("operationName") or "")
    url = "https://www.paypal.com/graphql/" if op == "authorize" else "https://www.paypal.com/graphql" + (f"?{op}" if op else "/")
    resp = http.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "fetch",
            "X-App-Name": "checkoutuinodeweb",
            "Origin": "https://www.paypal.com",
            "Referer": referer,
        },
        timeout=30,
    )
    return _json_response(resp, label)



def _paypal_deferred_feature_payload(ec_token: str, country: str) -> dict[str, Any]:
    return {
        "operationName": "DeferredFeature",
        "variables": {
            "channel": "WEB",
            "countryCodeAsString": country,
            "integrationType": "XoSignupAuth",
            "isBaslAsString": "false",
            "isForcedGuest": "false",
            "token": ec_token,
        },
        "query": (
            "query DeferredFeature($channel: String!, $countryCodeAsString: String!, "
            "$isBaslAsString: String!, $isForcedGuest: String!, $token: String!, "
            "$integrationType: String!) { otpLoginContext(token: $token, integrationType: $integrationType) "
            "{ __typename context } elmoExperiment(app: \"checkoutuinodeweb\" filters: "
            "[{key: \"Country\", value: $countryCodeAsString}, {key: \"Channel\", value: $channel}, "
            "{key: \"IsBasl\", value: $isBaslAsString}, {key: \"IsGuestOnly\", value: $isForcedGuest}] "
            "res: \"weasley:deferredFeature:memberAsDefault\") { __typename treatments { __typename "
            "experimentId experimentName factors { __typename key value } treatmentId treatmentName } } }"
        ),
    }



def _paypal_griffin_metadata_payload(country: str, lang: str) -> dict[str, Any]:
    return {
        "operationName": "GriffinMetadataQuery",
        "variables": {"countryCode": country, "languageCode": lang, "shippingCountryCode": country},
        "query": (
            "query GriffinMetadataQuery($countryCode: CountryCodes!, $languageCode: CheckoutContentLanguageCode!, "
            "$shippingCountryCode: CountryCodes!) { localeMetadata { address(countryCode: $countryCode, "
            "languageCode: $languageCode) { ...AddressMetadata __typename } shippingAddress: address("
            "countryCode: $shippingCountryCode languageCode: $languageCode) { ...AddressMetadata __typename } "
            "currencyCode(countryCode: $countryCode) date(countryCode: $countryCode, languageCode: $languageCode) "
            "{ displayFormat datePattern __typename } phone(countryCode: $countryCode) { masks { mobile "
            "__typename } patterns { default __typename } __typename } territories(countryCode: $countryCode, "
            "languageCode: $languageCode) { code internationalDialingCode name region suggestedDefaultLanguage "
            "__typename } __typename } } fragment AddressMetadata on LocaleAddressMetadata { layout { maxLength "
            "minLength isRequired name regex __typename } strings { cityLabel line1Label line2Label optionalLabel "
            "postcodeLabel stateLabel stateList { displayText value __typename } __typename } __typename }"
        ),
    }


def _paypal_checkout_session_payload(ec_token: str) -> dict[str, Any]:
    return {
        "operationName": "CheckoutSessionDataQuery",
        "variables": {"token": ec_token},
        "query": (
            "query CheckoutSessionDataQuery($token: String!) { checkoutSession(token: $token) { "
            "cart { billingAddress { city country line1 line2 postalCode state formattedFullAddress __typename } "
            "email { stringValue __typename } payer { name { familyName givenName __typename } __typename } "
            "formattedPhoneNumber(shouldValidate: true, useInternationalFormat: true) "
            "phoneNumber(shouldValidate: true, stripDialingCode: true) __typename } "
            "checkoutSessionType merchant { country merchantId name __typename } __typename } }"
        ),
    }



def _paypal_address_autocomplete(
    http: Any,
    signup_url: str,
    paypal_cfg: dict[str, Any],
    runtime_ctx: dict[str, Any],
    country: str,
    lang: str,
) -> None:
    address = _paypal_signup_address(paypal_cfg, str(paypal_cfg.get("first_name") or "Jealous"), str(paypal_cfg.get("last_name") or "Lane"), country)
    line1 = str(address.get("line1") or "")
    session_id = str(paypal_cfg.get("address_session_id") or runtime_ctx.get("paypal_address_session_id") or _short_random_id())
    location = str(paypal_cfg.get("address_location") or runtime_ctx.get("paypal_address_location") or "43.110,-88.070")
    place_id = str(paypal_cfg.get("address_place_id") or runtime_ctx.get("paypal_address_place_id") or "")
    if len(line1) > 1:
        _paypal_graphql(http, _paypal_address_autocomplete_payload(line1[:-1], country, lang, session_id, location), signup_url, "paypal AddressAutocompleteQuery")
    if line1:
        suggestions = _paypal_graphql(http, _paypal_address_autocomplete_payload(line1, country, lang, session_id, location), signup_url, "paypal AddressAutocompleteQuery")
        if not place_id:
            place_id = str(_find_key_recursive(suggestions, "placeId") or "")
    if place_id:
        _paypal_graphql(http, _paypal_address_place_payload(place_id, lang, session_id), signup_url, "paypal AddressFromAutocompletePlaceIdQuery")


def _paypal_address_autocomplete_payload(line1: str, country: str, lang: str, session_id: str, location: str) -> dict[str, Any]:
    return {
        "operationName": "AddressAutocompleteQuery",
        "variables": {
            "count": 4,
            "countries": [country],
            "input": line1,
            "language": lang,
            "radius": 1500,
            "sessionId": session_id,
            "location": location,
        },
        "query": (
            "query AddressAutocompleteQuery($count: Int, $countries: [CountryCodes], $input: String!, "
            "$language: CheckoutContentLanguageCode, $location: GeoLocation, $radius: Int, $sessionId: String!) "
            "{ addressAutoComplete(count: $count countries: $countries input: $input language: $language "
            "location: $location radius: $radius sessionId: $sessionId) { suggestions { addressText mainText "
            "placeId secondaryText __typename } __typename } }"
        ),
    }


def _paypal_address_place_payload(place_id: str, lang: str, session_id: str) -> dict[str, Any]:
    return {
        "operationName": "AddressFromAutocompletePlaceIdQuery",
        "variables": {"language": lang, "placeId": place_id, "sessionId": session_id},
        "query": (
            "query AddressFromAutocompletePlaceIdQuery($language: CheckoutContentLanguageCode, $placeId: ID!, "
            "$sessionId: String!) { addressFromAutoCompletePlaceId(language: $language placeId: $placeId "
            "sessionId: $sessionId) { address { line1 line2 city state postalCode country __typename } __typename } }"
        ),
    }


def _paypal_initiate_phone_payload(ec_token: str, phone: str, phone_country: str, country: str, lang: str) -> dict[str, Any]:
    return {
        "operationName": "InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
        "variables": {
            "locale": {"country": country, "lang": lang},
            "phoneCountry": phone_country,
            "phoneNumber": phone,
            "token": ec_token,
        },
        "query": (
            "mutation InitiateRiskBasedTwoFactorPhoneConfirmationMutation($phoneNumber: String!, "
            "$locale: LocaleInput!, $phoneCountry: CountryCodes!, $token: String!) { "
            "initiateRiskBasedTwoFactorPhoneConfirmation(locale: $locale phoneCountry: $phoneCountry "
            "phoneNumber: $phoneNumber token: $token) { authId challengeId state __typename } }"
        ),
    }



def _paypal_confirm_phone_payload(ec_token: str, auth_id: str, challenge_id: str, otp: str) -> dict[str, Any]:
    return {
        "operationName": "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
        "variables": {"authId": auth_id, "challengeId": challenge_id, "pin": otp, "token": ec_token},
        "query": (
            "mutation ConfirmRiskBasedTwoFactorPhoneConfirmationMutation($pin: String!, $authId: String!, "
            "$challengeId: String!, $token: String!) { confirmRiskBasedTwoFactorPhoneConfirmation("
            "pin: $pin authId: $authId challengeId: $challengeId token: $token) "
            "{ authId challengeId state __typename } }"
        ),
    }



def _paypal_signup_payload(
    ec_token: str,
    paypal_cfg: dict[str, Any],
    phone_number: str,
    phone_country_code: str,
    country: str,
) -> dict[str, Any]:
    first_name = str(paypal_cfg.get("first_name") or "Jealous")
    last_name = str(paypal_cfg.get("last_name") or "Lane")
    email = str(paypal_cfg.get("signup_email") or paypal_cfg.get("guest_email") or paypal_cfg.get("email") or f"ctf{uuid.uuid4().hex[:10]}@example.com")
    password = str(paypal_cfg.get("signup_password") or paypal_cfg.get("guest_password") or paypal_cfg.get("password") or _gen_paypal_password())
    card = _paypal_signup_card(paypal_cfg)
    address = _paypal_signup_address(paypal_cfg, first_name, last_name, country)
    return {
        "operationName": "SignUpNewMemberMutation",
        "variables": {
            "card": card,
            "country": country,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "phone": {"countryCode": phone_country_code, "number": phone_number, "type": "MOBILE"},
            "supportedThreeDsExperiences": ["IFRAME"],
            "token": ec_token,
            "billingAddress": address,
            "shippingAddress": {
                "line1": "",
                "city": "",
                "state": "",
                "postalCode": "",
                "accountQuality": {"autoCompleteType": "MANUAL", "isUserModified": False},
                "country": country,
                "familyName": last_name,
                "givenName": first_name,
            },
            "contentIdentifier": str(paypal_cfg.get("content_identifier") or "US:en:f411614ea3eaac38abc54763fcfca00e:compliance.signupTerms"),
            "marketingOptOut": bool(paypal_cfg.get("marketing_opt_out") or False),
            "password": password,
            "crsData": None,
            "legalAgreements": {},
        },
        "query": (
            "mutation SignUpNewMemberMutation($billingAddress: AddressInput, $card: CardInput, "
            "$contentIdentifier: String, $country: CountryCodes, $email: String!, $firstName: String!, "
            "$lastName: String!, $marketingOptOut: Boolean, $password: String, $phone: PhoneInput!, "
            "$shippingAddress: AddressInput, $supportedThreeDsExperiences: [ThreeDSPaymentExperience], "
            "$token: String!, $crsData: CommonReportingStandardsInput, $legalAgreements: LegalAgreementsInput) { "
            "onboardAccount: signUpNewMember(billingAddress: $billingAddress card: $card "
            "contentIdentifier: $contentIdentifier country: $country crsData: $crsData email: $email "
            "firstName: $firstName lastName: $lastName marketingOptOut: $marketingOptOut password: $password "
            "phone: $phone shippingAddress: $shippingAddress supportedThreeDsExperiences: $supportedThreeDsExperiences "
            "token: $token legalAgreements: $legalAgreements) { buyer { auth { accessToken __typename } "
            "userId __typename } flags { is3DSecureRequired __typename } fundingOptions { fundingInstrument { id "
            "lastDigits name nameDescription type __typename } __typename } __typename } }"
        ),
        "fn_sync_data": _generate_fn_sync_data(email, password),
    }



def _paypal_signup_card(paypal_cfg: dict[str, Any]) -> dict[str, str]:
    card = paypal_cfg.get("card") if isinstance(paypal_cfg.get("card"), dict) else {}
    number = str(card.get("number") or paypal_cfg.get("card_number") or "").replace(" ", "")
    exp_month = str(card.get("exp_month") or paypal_cfg.get("card_exp_month") or "").zfill(2)
    exp_year = str(card.get("exp_year") or paypal_cfg.get("card_exp_year") or "")
    cvv = str(card.get("cvv") or paypal_cfg.get("card_cvv") or "")
    if not number or not exp_month.strip("0") or not exp_year or not cvv:
        generated = _generate_paypal_signup_card()
        number = generated["number"]
        exp_month = generated["exp_month"]
        exp_year = generated["exp_year"]
        cvv = generated["cvv"]
    if len(exp_year) == 2:
        exp_year = "20" + exp_year
    return {
        "cardNumber": number,
        "expirationDate": f"{exp_month}/{exp_year}",
        "securityCode": cvv,
        "type": str(card.get("type") or paypal_cfg.get("card_type") or _card_type(number)),
    }


def _generate_paypal_signup_card() -> dict[str, str]:
    base = "4147"
    while len(base) < 15:
        base += str(random.randint(0, 9))
    year = utc_year() + 2 + random.randint(0, 3)
    return {
        "number": base + _luhn_check_digit(base),
        "exp_month": str(random.randint(1, 12)).zfill(2),
        "exp_year": str(year),
        "cvv": str(random.randint(100, 999)),
    }


def _luhn_check_digit(prefix: str) -> str:
    digits = [int(ch) for ch in prefix]
    total = 0
    parity = (len(digits) + 1) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - (total % 10)) % 10)


def utc_year() -> int:
    return int(time.strftime("%Y", time.gmtime()))



def _paypal_signup_address(paypal_cfg: dict[str, Any], first_name: str, last_name: str, country: str) -> dict[str, Any]:
    billing = paypal_cfg.get("billing") if isinstance(paypal_cfg.get("billing"), dict) else {}
    return {
        "line1": str(billing.get("line1") or paypal_cfg.get("billing_line1") or "Driftwood Court"),
        "city": str(billing.get("city") or paypal_cfg.get("billing_city") or "Brookfield"),
        "state": str(billing.get("state") or paypal_cfg.get("billing_state") or "WI"),
        "postalCode": str(billing.get("postal_code") or billing.get("postalCode") or paypal_cfg.get("billing_postal") or "53005"),
        "accountQuality": {"autoCompleteType": "GOOGLE", "isUserModified": False},
        "country": str(billing.get("country") or country),
        "familyName": last_name,
        "givenName": first_name,
    }



def _extract_phone_confirmation(payload: Any) -> dict[str, str]:
    found = _find_key_recursive(payload, "initiateRiskBasedTwoFactorPhoneConfirmation") or _find_key_recursive(payload, "confirmRiskBasedTwoFactorPhoneConfirmation")
    if not isinstance(found, dict):
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少确认状态: {payload}")
    auth_id = str(found.get("authId") or "")
    challenge_id = str(found.get("challengeId") or "")
    if not auth_id or not challenge_id:
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少 authId/challengeId: {payload}")
    return {"authId": auth_id, "challengeId": challenge_id, "state": str(found.get("state") or "")}



def _extract_paypal_buyer_access_token(payload: Any) -> str:
    value = _find_key_recursive(payload, "accessToken")
    return str(value or "")



def _find_key_recursive(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _find_key_recursive(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_key_recursive(item, key)
            if found is not None:
                return found
    return None



def _paypal_hermes_url(signup_url: str, ba_token: str, ec_token: str) -> str:
    parsed = urlparse(signup_url)
    q = parse_qs(parsed.query)
    q.update({
        "ba_token": [ba_token],
        "token": [ec_token],
        "fromSignupLite": ["true"],
        "addFIContingency": ["noretry"],
        "redirectToHermes": ["true"],
        "fallback": ["1"],
        "reason": ["Q0FSRF9HRU5FUklDX0VSUk9S"],
    })
    return "https://www.paypal.com/webapps/hermes?" + urlencode({k: v[-1] for k, v in q.items()})



def _phone_country_code(country: str) -> str:
    return {"US": "1", "CA": "1"}.get(str(country or "").upper(), "1")



def _strip_phone_country_code(phone: str, country_code: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if country_code and digits.startswith(country_code) and len(digits) > 10:
        return digits[len(country_code):]
    return digits



def _card_type(number: str) -> str:
    if number.startswith("4"):
        return "VISA"
    if number[:2] in {"51", "52", "53", "54", "55"}:
        return "MASTERCARD"
    if number.startswith(("34", "37")):
        return "AMEX"
    return "VISA"



def _gen_paypal_password() -> str:
    return "Aa" + uuid.uuid4().hex[:10] + "*9"



def _paypal_full_login_http(
    http: Any,
    approve_html: str,
    approve_url: str,
    paypal_cfg: dict[str, Any],
    csrf: str,
    sid: str,
    flow_id: str,
    ctx_id: str,
    log: LogFn | None,
) -> None:
    email = str(paypal_cfg.get("email") or "").strip()
    password = str(paypal_cfg.get("password") or "").strip()
    if not email or not password:
        raise PayPalHttpError("PayPal full login 缺少 email/password")

    lr_resp = http.post(
        "https://www.paypal.com/signin/load-resource",
        data={"_csrf": csrf, "flowId": flow_id, "intent": "checkout", "_sessionID": sid},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
        },
        timeout=30,
    )
    _emit(log, f"paypal_http: login load-resource status={lr_resp.status_code}")
    try:
        csrf = lr_resp.json().get("nonce") or csrf
    except Exception:
        pass

    email_resp = http.post(
        "https://www.paypal.com/signin",
        data={
            "splitLoginContext": "inputEmail",
            "login_email": email,
            "_csrf": csrf,
            "_sessionID": sid,
            "intent": "checkout",
            "flowId": flow_id,
            "ctxId": ctx_id or f"xo_ctx_{flow_id}",
            "fn_sync_data": _generate_fn_sync_data(email),
            "locale.x": str(paypal_cfg.get("locale") or "en_US"),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/html, */*",
        },
        timeout=30,
    )
    _emit(log, f"paypal_http: login email status={email_resp.status_code}")
    try:
        csrf = email_resp.json().get("nonce") or csrf
    except Exception:
        found = _first_match([r'name="_csrf"\s+value="([^"]+)"'], email_resp.text or "")
        csrf = found or csrf

    pwd_resp = http.post(
        "https://www.paypal.com/signin",
        data={
            "splitLoginContext": "inputPassword",
            "login_email": email,
            "login_password": password,
            "_csrf": csrf,
            "_sessionID": sid,
            "intent": "checkout",
            "flowId": flow_id,
            "ctxId": ctx_id or f"xo_ctx_{flow_id}",
            "fn_sync_data": _generate_fn_sync_data(email, password),
            "locale.x": str(paypal_cfg.get("locale") or "en_US"),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
            "X-Requested-With": "XMLHttpRequest",
        },
        allow_redirects=False,
        timeout=30,
    )
    _emit(log, f"paypal_http: login password status={pwd_resp.status_code}")
    current_resp = _submit_paypal_hcaptcha_if_required(http, pwd_resp, paypal_cfg, csrf, sid, approve_url, log)
    current_resp = _follow_paypal_redirects(http, current_resp, log)
    _answer_paypal_email_otp_if_required(http, current_resp, paypal_cfg, ctx_id, log)


def _submit_paypal_hcaptcha_if_required(
    http: Any,
    pwd_resp: Any,
    paypal_cfg: dict[str, Any],
    csrf: str,
    sid: str,
    approve_url: str,
    log: LogFn | None,
) -> Any:
    if pwd_resp.status_code != 200:
        return pwd_resp
    html = pwd_resp.text or ""
    needs_hcaptcha = "hcaptcha" in html.lower() or bool(_first_match([r'name="_requestId"\s+value="([^"]+)"'], html))
    if not needs_hcaptcha:
        return pwd_resp
    captcha_token = _solve_paypal_hcaptcha(paypal_cfg, log)
    if not captcha_token:
        raise PayPalHttpError("PayPal 需要 hCaptcha，但未得到验证码 token")
    request_id = _first_match([r'name="_requestId"\s+value="([^"]+)"'], html)
    hash_value = _first_match([r'name="_hash"\s+value="([^"]+)"'], html)
    csrf = _first_match([r'name="_csrf"\s+value="([^"]+)"'], html) or csrf
    return http.post(
        "https://www.paypal.com/signin",
        data={
            "_csrf": csrf,
            "_requestId": request_id,
            "_hash": hash_value,
            "_sessionID": sid,
            "hcaptcha": captcha_token,
            "_adsChallengeType": "visual-challenge",
            "hcaptcha_eval": str(random.randint(200, 600)),
            "hcaptcha_render": str(random.randint(100, 300)),
            "hcaptcha_verification": str(random.randint(5000, 15000)),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
        },
        allow_redirects=False,
        timeout=30,
    )


def _follow_paypal_redirects(http: Any, response: Any, log: LogFn | None) -> Any:
    current = response
    for _ in range(10):
        if current.status_code not in (301, 302, 303, 307, 308):
            break
        location = current.headers.get("Location") or current.headers.get("location") or ""
        if not location:
            break
        location = urljoin("https://www.paypal.com", location)
        _emit(log, f"paypal_http: login redirect {location[:100]}")
        current = http.get(location, allow_redirects=False, timeout=30)
    return current


def _answer_paypal_email_otp_if_required(
    http: Any,
    response: Any,
    paypal_cfg: dict[str, Any],
    ctx_id: str,
    log: LogFn | None,
) -> None:
    current_url = str(getattr(response, "url", "") or "")
    html = response.text or ""
    if "/authflow" not in current_url and "authflow" not in html[:5000]:
        return
    csrf = _first_match([r'"_csrf"\s*:\s*"([^"]+)"', r'name="_csrf"\s+value="([^"]+)"', r'"csrfToken"\s*:\s*"([^"]+)"'], html)
    anw_sid = _first_match([r'"anw_sid"\s*:\s*"([^"]+)"'], html)
    doc_id = _first_match([r'"authflowDocumentId"\s*:\s*"([^"]+)"', r'"documentId"\s*:\s*"([^"]+)"'], html)
    select_resp = http.put(
        "https://www.paypal.com/authflow/challenges/email",
        json={
            "_csrf": csrf,
            "anw_sid": anw_sid,
            "authflowDocumentId": doc_id,
            "action": "SELECT_CHALLENGE",
            "selectedChallengeType": "email",
            "isCheckoutFlow": True,
            "fn_sync_data": _generate_fn_sync_data(),
        },
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.paypal.com",
            "Referer": current_url,
        },
        timeout=30,
    )
    _emit(log, f"paypal_http: paypal otp select status={select_resp.status_code}")
    try:
        select_json = select_resp.json()
        doc_id = select_json.get("authflowDocumentId") or doc_id
        csrf = select_json.get("_csrf") or csrf
    except Exception:
        pass
    otp = _fetch_paypal_otp(paypal_cfg, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("PayPal 2FA OTP 获取失败")
    answer_resp = http.put(
        "https://www.paypal.com/authflow/challenges/email",
        json={
            "_csrf": csrf,
            "anw_sid": anw_sid,
            "authflowDocumentId": doc_id,
            "action": "ANSWER",
            "answer": otp,
            "selectedChallengeType": "email",
            "isCheckoutFlow": True,
            "challengeStartTime": str(int(time.time() * 1000)),
        },
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.paypal.com",
            "Referer": current_url,
        },
        timeout=30,
    )
    _emit(log, f"paypal_http: paypal otp answer status={answer_resp.status_code}")
    http.get(f"https://www.paypal.com/signin/return?flowFrom=anw-stepup&ctxId={ctx_id}", allow_redirects=True, timeout=30)


def _solve_paypal_hcaptcha(paypal_cfg: dict[str, Any], log: LogFn | None) -> str:
    token = str(paypal_cfg.get("hcaptcha_token") or paypal_cfg.get("captcha_token") or "").strip()
    if token:
        return token
    api_key = str(paypal_cfg.get("captcha_api_key") or (paypal_cfg.get("captcha") or {}).get("api_key") or "").strip()
    api_url = str(paypal_cfg.get("captcha_api_url") or (paypal_cfg.get("captcha") or {}).get("api_url") or os.getenv("CTF_CAPTCHA_API_URL") or "").rstrip("/")
    if not api_key or not api_url:
        return ""
    site_key = str(paypal_cfg.get("hcaptcha_site_key") or "bf07db68-5c2e-42e8-8779-ea8384890eea")
    create_resp = std_requests.post(
        f"{api_url}/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type": "HCaptchaTaskProxyless",
                "websiteURL": "https://www.paypal.com/signin",
                "websiteKey": site_key,
                "isEnterprise": True,
                "userAgent": USER_AGENT,
            },
        },
        timeout=30,
    )
    task_id = (create_resp.json() or {}).get("taskId")
    if not task_id:
        return ""
    deadline = time.time() + int(paypal_cfg.get("captcha_timeout") or 120)
    while time.time() < deadline:
        time.sleep(5)
        poll_resp = std_requests.post(f"{api_url}/getTaskResult", json={"clientKey": api_key, "taskId": task_id}, timeout=20)
        payload = poll_resp.json() or {}
        if payload.get("status") == "ready":
            solution = payload.get("solution") or {}
            return str(solution.get("gRecaptchaResponse") or solution.get("token") or "")
        if payload.get("errorId"):
            _emit(log, f"paypal_http: hcaptcha provider error={payload.get('errorDescription')}", level="warning")
            return ""
    return ""


def _fetch_paypal_otp(paypal_cfg: dict[str, Any], timeout: int, log: LogFn | None) -> str:
    static_otp = str(paypal_cfg.get("otp") or paypal_cfg.get("email_otp") or "").strip()
    if static_otp:
        return static_otp
    otp_file = str(paypal_cfg.get("otp_file") or "").strip()
    if otp_file:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(otp_file, "r", encoding="utf-8") as handle:
                    value = handle.read().strip()
                match = re.search(r"\b(\d{6})\b", value)
                if match:
                    return match.group(1)
            except FileNotFoundError:
                pass
            time.sleep(1)
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if smsurl:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = std_requests.get(smsurl, timeout=15)
                text = resp.text or ""
                try:
                    payload = resp.json()
                    text += " " + json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass
                match = re.search(r"\b(\d{6})\b", text)
                if match:
                    return match.group(1)
            except Exception as exc:
                _emit(log, f"paypal_http: smsurl otp poll failed: {exc}", level="warning")
            time.sleep(2)
    try:
        from cf_kv_otp_provider import CloudflareKVOtpProvider
    except Exception as exc:
        _emit(log, f"paypal_http: cf otp provider unavailable: {exc}", level="warning")
        return ""
    target = str(paypal_cfg.get("email") or "").strip()
    if not target:
        return ""
    try:
        return str(CloudflareKVOtpProvider.from_env_or_secrets().wait_for_otp(target, timeout=timeout) or "")
    except Exception as exc:
        _emit(log, f"paypal_http: paypal otp wait failed: {exc}", level="warning")
        return ""


def _generate_fn_sync_data(email_text: str = "", password_text: str = "") -> str:
    def timings(text: str) -> str:
        return ",".join(
            f"Di{random.randint(45, 170)}Ui{random.randint(25, 85)}Dk{random.randint(35, 110)}Uk{random.randint(15, 65)}"
            for _ in text
        )
    payload = {
        "ts1": timings(email_text),
        "ts2": timings(password_text),
        "rDT": str(random.randint(30, 200)),
        "bP": "24",
        "wI": "1920",
        "wO": "1080",
    }
    return quote(json.dumps(payload, separators=(",", ":")))


def _poll_result(
    session: Any,
    pk: str,
    session_id: str,
    runtime: dict[str, Any],
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None,
) -> dict[str, Any]:
    attempts = int(runtime.get("poll_attempts") or 30)
    interval = float(runtime.get("poll_interval_seconds") or 2)
    params = {"key": pk}
    last: dict[str, Any] = {}
    for attempt in range(attempts):
        _checkpoint(check_cancelled)
        time.sleep(interval)
        resp = session.get(f"{STRIPE_API}/v1/payment_pages/{session_id}/poll", params=params, headers=_stripe_headers(), timeout=30)
        if resp.status_code != 200:
            _emit(log, f"paypal_http: poll {attempt + 1}/{attempts} http={resp.status_code}", level="warning")
            continue
        data = resp.json() or {}
        last = data
        state = str(data.get("state") or "unknown")
        payment_status = data.get("payment_object_status") or data.get("payment_status") or "unknown"
        _emit(log, f"paypal_http: poll {attempt + 1}/{attempts} state={state} payment={payment_status}")
        if state in {"succeeded", "failed", "expired", "canceled"}:
            return data
    if last:
        return {**last, "state": str(last.get("state") or "poll_timeout")}
    raise PayPalHttpError("Stripe poll 超时且没有有效响应")


def _extract_redirect_url(payload: Any) -> str:
    if isinstance(payload, dict):
        rtu = payload.get("redirect_to_url")
        if isinstance(rtu, dict) and rtu.get("url"):
            return str(rtu["url"])
        if payload.get("type") == "redirect_to_url" and isinstance(rtu, dict) and rtu.get("url"):
            return str(rtu["url"])
        next_action = payload.get("next_action")
        found = _extract_redirect_url(next_action)
        if found:
            return found
        for value in payload.values():
            found = _extract_redirect_url(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_redirect_url(item)
            if found:
                return found
    return ""


def _find_setup_intent(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        si = payload.get("setup_intent")
        if isinstance(si, dict):
            return si
        for value in payload.values():
            found = _find_setup_intent(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_setup_intent(item)
            if found:
                return found
    return None


def _normalize_billing(billing: dict[str, Any], account: Any) -> dict[str, Any]:
    address = dict(billing.get("address") or {})
    if not address:
        address = {
            "country": billing.get("country") or billing.get("billing_country") or "US",
            "line1": billing.get("line1") or "Driftwood Court",
            "city": billing.get("city") or "Germantown",
            "state": billing.get("state") or "WI",
            "postal_code": billing.get("postal_code") or billing.get("billing_postal") or "53022",
        }
    else:
        address.setdefault("country", billing.get("country") or "US")
        address.setdefault("line1", billing.get("line1") or "Driftwood Court")
        address.setdefault("city", billing.get("city") or "Germantown")
        address.setdefault("state", billing.get("state") or "WI")
        address.setdefault("postal_code", billing.get("postal_code") or billing.get("billing_postal") or "53022")
    return {
        "name": billing.get("name") or billing.get("holder_name") or "John Doe",
        "email": billing.get("email") or getattr(account, "email", "") or "buyer@example.com",
        "address": address,
    }


def _paypal_cookie_header(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(
            f"{item.get('name')}={item.get('value')}"
            for item in value
            if isinstance(item, dict) and item.get("name") and item.get("value")
        )
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("["):
        try:
            return _paypal_cookie_header(json.loads(text))
        except Exception:
            return text
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                return _paypal_cookie_header(data["cookies"])
            if isinstance(data, dict) and isinstance(data.get("cookies_str"), str):
                return data["cookies_str"]
        except Exception:
            return text
    return text


def _seed_paypal_cookies(session: Any, cookies: str) -> None:
    for pair in cookies.split(";"):
        item = pair.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip() and value.strip():
            session.cookies.set(name.strip(), value.strip(), domain=".paypal.com", path="/")


def _query_value(url: str, name: str) -> str:
    try:
        return parse_qs(urlparse(url).query).get(name, [""])[0]
    except Exception:
        return ""


def _first_match(patterns: list[str], text: str, flags: int = 0) -> str:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags)
        if match:
            return match.group(1)
    return ""


def _json_response(resp: Any, label: str) -> Any:
    if resp.status_code < 200 or resp.status_code >= 300:
        raise PayPalHttpError(f"{label} 失败 [{resp.status_code}]: {_short_body(resp)}")
    try:
        return resp.json()
    except Exception as exc:
        raise PayPalHttpError(f"{label} 响应不是 JSON: {_short_body(resp)}") from exc


def _short_body(resp: Any, limit: int = 500) -> str:
    return str(getattr(resp, "text", "") or "")[:limit]


def _falsey(value: Any) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off", "disabled"}


def _emit(log: LogFn | None, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    if log is not None:
        log(message, level, payload)


def _checkpoint(check_cancelled: CheckCancelledFn | None) -> None:
    if check_cancelled is not None:
        check_cancelled()
