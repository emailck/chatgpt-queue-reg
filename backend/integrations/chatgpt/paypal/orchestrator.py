from __future__ import annotations

from typing import Any

from .chatgpt_approve import chatgpt_approve, requires_manual_approval
from .paypal_authorize import authorize_paypal_http
from .runtime import (
    CheckCancelledFn,
    DEFAULT_STRIPE_PK,
    LogFn,
    PayPalHttpError,
    USER_AGENT,
    build_runtime_context,
    checkpoint,
    emit,
    extract_redirect_url,
    extract_session_id,
    new_http_session,
    normalize_billing,
)
from .stripe_checkout import (
    confirm_payment,
    create_paypal_payment_method,
    fetch_allowed_origins,
    fetch_elements_session,
    merge_elements_context,
    merge_init_context,
    poll_payment_page_redirect,
    poll_result,
    resolve_paypal_approve_url,
    stripe_init,
    update_payment_page_address,
)


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
    billing = normalize_billing(billing or {}, account)
    runtime = dict(runtime or {})
    stripe = dict(stripe or {})

    session_id = extract_session_id(checkout_session_id, checkout_url)
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

    ctx = build_runtime_context(runtime, session_id, checkout_url)
    http = new_http_session(proxy_url)
    http.headers.update({
        "User-Agent": str(runtime.get("user_agent") or USER_AGENT),
        "Accept-Language": str(runtime.get("accept_language") or "en-US,en;q=0.9"),
    })

    emit(log, "paypal_http: stripe init")
    init_resp = stripe_init(http, stripe_pk, session_id, ctx)
    merge_init_context(ctx, init_resp)
    checkpoint(check_cancelled)

    emit(log, "paypal_http: stripe allowed origins")
    fetch_allowed_origins(http, stripe_pk, session_id)
    checkpoint(check_cancelled)

    if ctx.get("use_elements_session"):
        emit(log, "paypal_http: stripe elements session")
        elements_resp = fetch_elements_session(http, stripe_pk, session_id, ctx, init_resp)
        merge_elements_context(ctx, elements_resp)
        checkpoint(check_cancelled)

    emit(log, "paypal_http: stripe billing address update")
    update_payment_page_address(http, stripe_pk, session_id, ctx, billing)
    checkpoint(check_cancelled)

    emit(log, "paypal_http: create paypal payment_method")
    pm_id = create_paypal_payment_method(http, stripe_pk, session_id, billing, ctx)
    checkpoint(check_cancelled)

    emit(log, "paypal_http: stripe confirm")
    confirm_data = confirm_payment(http, stripe_pk, session_id, pm_id, init_resp, ctx)
    redirect_url = extract_redirect_url(confirm_data)

    if requires_manual_approval(confirm_data):
        emit(log, "paypal_http: chatgpt manual approval required")
        chatgpt_approve(
            account=account,
            session_id=session_id,
            processor_entity=str(runtime.get("processor_entity") or "openai_llc"),
            proxy_url=chatgpt_proxy_url,
            log=log,
        )
        checkpoint(check_cancelled)
        if not redirect_url:
            redirect_url = poll_payment_page_redirect(http, stripe_pk, session_id, ctx, log, check_cancelled)

    if not redirect_url:
        redirect_url = poll_payment_page_redirect(http, stripe_pk, session_id, ctx, log, check_cancelled)
    if not redirect_url:
        raise PayPalHttpError("Stripe confirm/poll 未返回 PayPal redirect_to_url")

    emit(log, "paypal_http: resolve paypal approve url")
    paypal_approve_url = resolve_paypal_approve_url(http, redirect_url)
    checkpoint(check_cancelled)

    paypal_runtime = {**paypal, "_runtime": ctx, "_proxy_url": proxy_url}
    emit(log, "paypal_http: authorize paypal billing agreement")
    paypal_result = authorize_paypal_http(paypal_approve_url, paypal_runtime, proxy_url, log)
    checkpoint(check_cancelled)

    emit(log, "paypal_http: stripe allowed origins after return")
    fetch_allowed_origins(http, stripe_pk, session_id)
    checkpoint(check_cancelled)

    emit(log, "paypal_http: stripe poll result")
    poll_data = poll_result(http, stripe_pk, session_id, runtime, log, check_cancelled)

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
