from __future__ import annotations

import uuid
from typing import Any

from .runtime import json_response


PAYPAL_GRAPHQL_NAMED = "https://www.paypal.com/graphql"
PAYPAL_GRAPHQL_ROOT = "https://www.paypal.com/graphql/"


def _op_name(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("operationName") or "")
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return str(payload[0].get("operationName") or "")
    return ""


def _euat_from_session(http: Any) -> str:
    """Read x-paypal-internal-euat that paypal_guest_signup stashed on the session.

    The guest-signup flow extracts buyer.auth.accessToken from the SignUp
    response and writes it to http.headers under this key. Multiple PayPal
    cookies share the AV894Kt2*-style shape so we can't reliably pick the
    right one by heuristic; the explicit header set after SignUp is the
    source of truth.
    """
    headers = getattr(http, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get("x-paypal-internal-euat") or headers.get("X-Paypal-Internal-Euat") or ""
    except Exception:
        return ""
    return str(value or "")


def graphql_checkoutweb(
    http: Any,
    payload: Any,
    *,
    referer: str,
    ec_token: str,
    country: str = "US",
    locale: str = "en_US",
    label: str | None = None,
) -> Any:
    """POST to graphql?<Op> with the checkoutweb signup-flow headers.

    Sends: paypal-client-context, paypal-client-metadata-id (both = ec_token),
    x-app-name=checkoutuinodeweb_weasley, x-country, x-locale.
    """
    op = _op_name(payload)
    url = f"{PAYPAL_GRAPHQL_NAMED}?{op}" if op else f"{PAYPAL_GRAPHQL_NAMED}/"
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "fetch",
        "x-app-name": "checkoutuinodeweb_weasley",
        "paypal-client-context": ec_token,
        "paypal-client-metadata-id": ec_token,
        "x-country": country,
        "x-locale": locale,
        "Origin": "https://www.paypal.com",
        "Referer": referer,
    }
    resp = http.post(url, json=payload, headers=headers, timeout=30)
    return json_response(resp, label or f"paypal graphql {op or 'checkoutweb'}")


def graphql_authorize(
    http: Any,
    payload: Any,
    *,
    referer: str,
    metadata_id: str | None = None,
    euat: str | None = None,
    label: str | None = None,
) -> Any:
    """POST to graphql/ for the post-hermes authorize mutation.

    Sends: x-app-name=checkoutuinodeweb (no _weasley),
    PAYPAL-CLIENT-METADATA-ID=<random UUID unless given>,
    x-paypal-internal-euat=<euat or extracted from cookies>.
    """
    headers = {
        "Content-Type": "application/json",
        "x-app-name": "checkoutuinodeweb",
        "PAYPAL-CLIENT-METADATA-ID": str(metadata_id or uuid.uuid4()),
        "Origin": "https://www.paypal.com",
        "Referer": referer,
    }
    token = str(euat or "").strip() or _euat_from_session(http)
    if token:
        headers["x-paypal-internal-euat"] = token
    resp = http.post(PAYPAL_GRAPHQL_ROOT, json=payload, headers=headers, timeout=30)
    return json_response(resp, label or "paypal graphql authorize")
