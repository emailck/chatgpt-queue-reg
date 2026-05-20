from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlparse

from .runtime import (
    CheckCancelledFn,
    LogFn,
    PAY_OPENAI_REFERER,
    PayPalHttpError,
    STRIPE_API,
    STRIPE_VERSION_CUSTOM_CHECKOUT,
    STRIPE_VERSION_FULL,
    checkpoint,
    emit,
    extract_redirect_url,
    find_setup_intent,
    json_response,
    short_body,
    stripe_headers,
)


def stripe_init(session: Any, pk: str, session_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
    url = f"{STRIPE_API}/v1/payment_pages/{session_id}/init"
    data = {
        "key": pk,
        "eid": ctx["eid"],
        "browser_locale": ctx["browser_locale"],
        "browser_timezone": ctx["browser_timezone"],
        "redirect_type": "url",
    }
    resp = session.post(url, data=data, headers=stripe_headers(), timeout=30)
    if resp.status_code == 400 and "parameter_unknown" in short_body(resp):
        resp = session.post(url, data={"key": pk, "eid": ctx["eid"]}, headers=stripe_headers(), timeout=30)
    payload = json_response(resp, "stripe init")
    if not payload.get("init_checksum"):
        raise PayPalHttpError(f"stripe init 未返回 init_checksum: {short_body(resp)}")
    return payload


def merge_init_context(ctx: dict[str, Any], init_resp: dict[str, Any]) -> None:
    eid = init_resp.get("eid")
    if isinstance(eid, str) and eid:
        ctx["address_eid"] = eid
    customer_email = init_resp.get("customer_email")
    if isinstance(customer_email, str) and customer_email:
        ctx["customer_email"] = customer_email


def fetch_allowed_origins(session: Any, pk: str, session_id: str) -> dict[str, Any]:
    resp = session.get(
        f"{STRIPE_API}/v1/payment_pages/allowed_origins",
        params={"key": pk, "session_id": session_id},
        headers=stripe_headers(),
        timeout=30,
    )
    return json_response(resp, "stripe allowed_origins")


def fetch_elements_session(
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
    amount = expected_amount(init_resp)
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
    resp = session.get(url, params=params, headers=stripe_headers(), timeout=30)
    return json_response(resp, "stripe elements session")


def merge_elements_context(ctx: dict[str, Any], elements_resp: dict[str, Any]) -> None:
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


def update_payment_page_address(
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
        resp = session.post(url, data=data, headers=stripe_headers(), timeout=30)
        json_response(resp, "stripe address update")


def create_paypal_payment_method(
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
    resp = session.post(f"{STRIPE_API}/v1/payment_methods", data=data, headers=stripe_headers(), timeout=30)
    payload = json_response(resp, "stripe paypal payment_method")
    pm_id = str(payload.get("id") or "")
    if not pm_id.startswith("pm_"):
        raise PayPalHttpError(f"stripe payment_methods 未返回 pm_: {payload}")
    return pm_id


def confirm_payment(
    session: Any,
    pk: str,
    session_id: str,
    pm_id: str,
    init_resp: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Stripe /confirm.

    Anti-bot fields (passive_captcha_token / passive_captcha_ekey / js_checksum /
    rv_timestamp) must be injected via the runtime dict by the caller; this
    module does not generate them. In a no-browser context, integrate a
    Stripe passive-captcha provider or inject values captured from a real
    browser session.
    """
    data = {
        "guid": ctx["guid"],
        "muid": ctx["muid"],
        "sid": ctx["sid"],
        "eid": ctx["eid"],
        "payment_method": pm_id,
        "expected_amount": expected_amount(init_resp),
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
        headers=stripe_headers(),
        timeout=30,
    )
    return json_response(resp, "stripe confirm")


def expected_amount(init_resp: dict[str, Any]) -> str:
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


def poll_payment_page_redirect(
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
        checkpoint(check_cancelled)
        resp = session.get(f"{STRIPE_API}/v1/payment_pages/{session_id}", params=params, headers=stripe_headers(), timeout=30)
        if resp.status_code == 200:
            payload = resp.json() or {}
            redirect_url = extract_redirect_url(payload)
            if redirect_url:
                return redirect_url
            setup_intent = find_setup_intent(payload) or {}
            last = f"status={payload.get('status')} payment_status={payload.get('payment_status')} setup={setup_intent.get('status')}"
        else:
            last = f"http={resp.status_code} body={short_body(resp)}"
        emit(log, f"paypal_http: waiting redirect ({last})")
        time.sleep(1)
    raise PayPalHttpError(f"等待 Stripe PayPal redirect 超时: {last}")


def resolve_paypal_approve_url(session: Any, redirect_url: str) -> str:
    current = str(redirect_url or "").strip()
    for _ in range(6):
        if _is_paypal_approve_url(current):
            return current
        resp = session.get(current, allow_redirects=False, headers=stripe_headers(), timeout=30)
        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        if not location and _is_paypal_approve_url(str(resp.url)):
            return str(resp.url)
        if not location:
            raise PayPalHttpError(f"pm redirect 未返回 PayPal Location: status={resp.status_code} body={short_body(resp)}")
        current = urljoin(current, location)
    raise PayPalHttpError("pm redirect 跳转层数过多，未到 PayPal approve")


def _is_paypal_approve_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.netloc.endswith("paypal.com") and "/agreements/approve" in parsed.path


def poll_result(
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
        checkpoint(check_cancelled)
        time.sleep(interval)
        resp = session.get(f"{STRIPE_API}/v1/payment_pages/{session_id}/poll", params=params, headers=stripe_headers(), timeout=30)
        if resp.status_code != 200:
            emit(log, f"paypal_http: poll {attempt + 1}/{attempts} http={resp.status_code}", level="warning")
            continue
        data = resp.json() or {}
        last = data
        state = str(data.get("state") or "unknown")
        payment_status = data.get("payment_object_status") or data.get("payment_status") or "unknown"
        emit(log, f"paypal_http: poll {attempt + 1}/{attempts} state={state} payment={payment_status}")
        if state in {"succeeded", "failed", "expired", "canceled"}:
            return data
    if last:
        return {**last, "state": str(last.get("state") or "poll_timeout")}
    raise PayPalHttpError("Stripe poll 超时且没有有效响应")
