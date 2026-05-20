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
    """Extract x-paypal-internal-euat from session cookies.

    PayPal sets it under randomised cookie names like AV894Kt2TSumQQrJwe-8mzmyREO.
    The value is a base64-like opaque token. We grab the first cookie value that
    looks like one (length and charset heuristic).
    """
    jar = getattr(http, "cookies", None)
    if jar is None:
        return ""
    try:
        items = list(jar)
    except Exception:
        try:
            items = list(jar.items())
        except Exception:
            return ""
    for item in items:
        name = getattr(item, "name", None) or (item[0] if isinstance(item, tuple) else "")
        value = getattr(item, "value", None) or (item[1] if isinstance(item, tuple) else "")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if len(name) >= 27 and name[0].isalpha() and name.replace("_", "").isalnum():
            if len(value) >= 60 and "-" in value or "_" in value:
                return value
    return ""


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
