from __future__ import annotations

from typing import Any

from backend.integrations.chatgpt.payment import _payment_backend_request_context

from .runtime import (
    CHATGPT_APPROVE_URL,
    CHATGPT_SENTINEL_URL,
    LogFn,
    PayPalHttpError,
    emit,
    json_response,
    new_http_session,
)


def requires_manual_approval(payload: Any) -> bool:
    """Structured detection of Stripe confirm responses that need ChatGPT approval.

    Looks at `payment_status`, `status`, `next_action.type`, and recurses into
    `payment_object` / `setup_intent` / `payment_intent`. Avoids substring
    matches that can be triggered by unrelated fields containing the literal
    "requires_approval".
    """
    return _check(payload)


def _check(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    matchset = {"requires_approval", "requires_action"}
    if str(payload.get("payment_status") or "").lower() in matchset:
        return True
    if str(payload.get("status") or "").lower() in matchset:
        return True
    next_action = payload.get("next_action") or {}
    if isinstance(next_action, dict) and str(next_action.get("type") or "").lower() in matchset:
        return True
    for nested_key in ("payment_object", "setup_intent", "payment_intent"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict) and _check(nested):
            return True
    return False


def chatgpt_approve(
    *,
    account: Any,
    session_id: str,
    processor_entity: str,
    proxy_url: str,
    log: LogFn | None,
) -> None:
    headers, impersonate = _payment_backend_request_context(url=CHATGPT_SENTINEL_URL, account=account)
    http = new_http_session(proxy_url, impersonate=impersonate)
    try:
        http.post(CHATGPT_SENTINEL_URL, json={}, headers=headers, timeout=20)
    except Exception as exc:
        emit(log, f"paypal_http: sentinel ping skipped: {exc}", level="warning")

    headers, _ = _payment_backend_request_context(url=CHATGPT_APPROVE_URL, account=account)
    resp = http.post(
        CHATGPT_APPROVE_URL,
        json={"checkout_session_id": session_id, "processor_entity": processor_entity},
        headers=headers,
        timeout=30,
    )
    payload = json_response(resp, "chatgpt checkout approve")
    result = payload.get("result") or payload.get("status")
    if result not in ("approved", "success", True):
        raise PayPalHttpError(f"chatgpt approve 未通过: {payload}")
