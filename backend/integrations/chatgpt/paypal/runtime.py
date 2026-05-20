from __future__ import annotations

import json
import random
import re
import time
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests as std_requests

from backend.core.proxy import build_requests_proxy_config

try:
    from curl_cffi.requests import Session as CurlCffiSession
except Exception:
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


def new_http_session(proxy_url: str = "", impersonate: str = "chrome136"):
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


def stripe_headers(referer: str = PAY_OPENAI_REFERER) -> dict[str, str]:
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


def extract_session_id(checkout_session_id: str, checkout_url: str) -> str:
    value = str(checkout_session_id or "").strip()
    if value.startswith("cs_"):
        return value
    text = str(checkout_url or "")
    match = re.search(r"(cs_(?:live|test)_[A-Za-z0-9]+)", text)
    return match.group(1) if match else ""


def gen_fingerprint() -> str:
    return str(uuid.uuid4()) + uuid.uuid4().hex[:5]


def gen_elements_session_id() -> str:
    return "elements_session_" + uuid.uuid4().hex[:24]


def short_random_id(length: int = 13) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def build_runtime_context(runtime: dict[str, Any], session_id: str, checkout_url: str) -> dict[str, Any]:
    """Build the per-session runtime context.

    Anti-bot fields (passive_captcha_token / passive_captcha_ekey / js_checksum /
    rv_timestamp) must be supplied by the caller via the `runtime` dict;
    they cannot be generated server-side. HTTP-only flows require these to be
    captured from a real browser or a Stripe passive-captcha provider.
    """
    version = str(runtime.get("version") or runtime.get("runtime_version") or DEFAULT_STRIPE_RUNTIME_VERSION)
    stripe_js_id = str(runtime.get("stripe_js_id") or runtime.get("client_session_id") or uuid.uuid4())
    elements_session_id = str(runtime.get("elements_session_id") or gen_elements_session_id())
    elements_session_config_id = str(runtime.get("elements_session_config_id") or uuid.uuid4())
    checkout_config_id = str(runtime.get("checkout_config_id") or runtime.get("top_checkout_config_id") or uuid.uuid4())
    pm_checkout_config_id = str(
        runtime.get("payment_method_checkout_config_id")
        or runtime.get("checkout_config_id")
        or checkout_config_id
    )
    return_url = str(runtime.get("return_url") or "")
    if not return_url:
        return_url = hosted_return_url(session_id, runtime)
    return {
        "guid": str(runtime.get("guid") or gen_fingerprint()),
        "muid": str(runtime.get("muid") or gen_fingerprint()),
        "sid": str(runtime.get("sid") or gen_fingerprint()),
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
        "use_elements_session": not falsey(runtime.get("use_elements_session")),
        "poll_attempts": int(runtime.get("poll_attempts") or 30),
        "poll_interval_seconds": float(runtime.get("poll_interval_seconds") or 2),
        "redirect_poll_seconds": float(runtime.get("redirect_poll_seconds") or 60),
        "pre_tax_country": str(runtime.get("pre_tax_country") or "CA"),
        "pre_tax_state": str(runtime.get("pre_tax_state") or "ON"),
        "pre_tax_postal_code": str(runtime.get("pre_tax_postal_code") or "L4W 3Z1"),
        "pre_tax_line1": str(runtime.get("pre_tax_line1") or "5500 Dixie Road"),
        "pre_tax_line2": str(runtime.get("pre_tax_line2") or "Unit G"),
        "pre_tax_city": str(runtime.get("pre_tax_city") or "Mississauga"),
        "paypal_address_autocomplete": not falsey(runtime.get("paypal_address_autocomplete")),
        "return_url_hash": str(runtime.get("return_url_hash") or ""),
    }


def hosted_return_url(session_id: str, runtime: dict[str, Any] | None = None) -> str:
    base = (
        f"{PAY_OPENAI_ORIGIN}/c/pay/{session_id}?"
        + urlencode({"redirect_pm_type": "paypal", "lid": str(uuid.uuid4()), "ui_mode": "hosted"})
    )
    hash_value = str((runtime or {}).get("return_url_hash") or "").lstrip("#")
    if hash_value:
        return f"{base}#{hash_value}"
    return base


def normalize_billing(billing: dict[str, Any], account: Any) -> dict[str, Any]:
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


def paypal_cookie_header(value: Any) -> str:
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
            return paypal_cookie_header(json.loads(text))
        except Exception:
            return text
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                return paypal_cookie_header(data["cookies"])
            if isinstance(data, dict) and isinstance(data.get("cookies_str"), str):
                return data["cookies_str"]
        except Exception:
            return text
    return text


def seed_paypal_cookies(session: Any, cookies: str) -> None:
    for pair in cookies.split(";"):
        item = pair.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip() and value.strip():
            session.cookies.set(name.strip(), value.strip(), domain=".paypal.com", path="/")


def query_value(url: str, name: str) -> str:
    try:
        return parse_qs(urlparse(url).query).get(name, [""])[0]
    except Exception:
        return ""


def first_match(patterns: list[str], text: str, flags: int = 0) -> str:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags)
        if match:
            return match.group(1)
    return ""


def json_response(resp: Any, label: str) -> Any:
    if resp.status_code < 200 or resp.status_code >= 300:
        raise PayPalHttpError(f"{label} 失败 [{resp.status_code}]: {short_body(resp)}")
    try:
        return resp.json()
    except Exception as exc:
        raise PayPalHttpError(f"{label} 响应不是 JSON: {short_body(resp)}") from exc


def short_body(resp: Any, limit: int = 500) -> str:
    return str(getattr(resp, "text", "") or "")[:limit]


def falsey(value: Any) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off", "disabled"}


def emit(log: LogFn | None, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    if log is not None:
        log(message, level, payload)


def checkpoint(check_cancelled: CheckCancelledFn | None) -> None:
    if check_cancelled is not None:
        check_cancelled()


def find_key_recursive(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = find_key_recursive(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_key_recursive(item, key)
            if found is not None:
                return found
    return None


def luhn_check_digit(prefix: str) -> str:
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


def card_type(number: str) -> str:
    if number.startswith("4"):
        return "VISA"
    if number[:2] in {"51", "52", "53", "54", "55"}:
        return "MASTERCARD"
    if number.startswith(("34", "37")):
        return "AMEX"
    return "VISA"


def phone_country_code(country: str) -> str:
    return {"US": "1", "CA": "1"}.get(str(country or "").upper(), "1")


def strip_phone_country_code(phone: str, country_code: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if country_code and digits.startswith(country_code) and len(digits) > 10:
        return digits[len(country_code):]
    return digits


def generate_fn_sync_data(email_text: str = "", password_text: str = "") -> str:
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


def gen_paypal_password() -> str:
    return "Aa" + uuid.uuid4().hex[:10] + "*9"


def extract_redirect_url(payload: Any) -> str:
    if isinstance(payload, dict):
        rtu = payload.get("redirect_to_url")
        if isinstance(rtu, dict) and rtu.get("url"):
            return str(rtu["url"])
        if payload.get("type") == "redirect_to_url" and isinstance(rtu, dict) and rtu.get("url"):
            return str(rtu["url"])
        next_action = payload.get("next_action")
        found = extract_redirect_url(next_action)
        if found:
            return found
        for value in payload.values():
            found = extract_redirect_url(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = extract_redirect_url(item)
            if found:
                return found
    return ""


def find_setup_intent(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        si = payload.get("setup_intent")
        if isinstance(si, dict):
            return si
        for value in payload.values():
            found = find_setup_intent(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_setup_intent(item)
            if found:
                return found
    return None
