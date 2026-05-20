from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .paypal_login import fetch_paypal_otp
from .runtime import (
    LogFn,
    PayPalHttpError,
    emit,
    gen_paypal_password,
    query_value,
)


def browser_paypal_checkout(
    approve_url: str,
    ba_token: str,
    proxy_url: str,
    paypal_cfg: dict[str, Any],
    address: dict[str, str],
    log: LogFn | None,
) -> dict[str, Any]:
    """Drive the entire PayPal side in Camoufox: approve → signup → OTP → hermes Continue.

    HTTP handles the Stripe side (init through confirm) and provides the
    approve_url. This function opens a browser, fills the signup form,
    handles OTP, and waits for hermes review → Continue → Stripe return.
    """
    from camoufox.sync_api import Camoufox
    from browserforge.fingerprints import Screen

    cf_proxy = _build_camoufox_proxy(proxy_url)
    tmp_profile = tempfile.mkdtemp(prefix="paypal_auth_")
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    phone = str(paypal_cfg.get("phone") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    password = str(paypal_cfg.get("signup_password") or gen_paypal_password())

    emit(log, f"paypal_http: browser checkout starting proxy={'yes' if cf_proxy else 'no'}")

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

            emit(log, "paypal_http: browser navigating to approve URL")
            page.goto(approve_url, wait_until="domcontentloaded", timeout=60000)

            _wait_for_signup_page(page, log)

            _fill_signup_form(page, phone, password, address, log)

            _submit_and_handle_otp(page, paypal_cfg, smsurl, log)

            return_url = _wait_for_stripe_return(page, log)

            ec_token = query_value(page.url, "token") or ""
            return {
                "ba_token": ba_token,
                "ec_token": ec_token,
                "return_url": return_url,
                "final_url": return_url,
                "status_code": 200,
            }
    except PayPalHttpError:
        raise
    except Exception as exc:
        raise PayPalHttpError(f"browser checkout 失败: {exc}") from exc
    finally:
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


def _wait_for_signup_page(page: Any, log: LogFn | None) -> None:
    for i in range(30):
        cur = page.url
        if "/checkoutweb/signup" in cur:
            emit(log, f"paypal_http: browser on signup page")
            time.sleep(2)
            return
        if "/webapps/hermes" in cur:
            emit(log, "paypal_http: browser landed on hermes directly (already authed?)")
            return
        time.sleep(1)
    raise PayPalHttpError(f"browser: 等待 signup 页超时, url={page.url[:120]}")


def _rand_email() -> str:
    import random, string
    chars = string.ascii_lowercase + string.digits
    name = "".join(random.choice(chars) for _ in range(16))
    return f"{name}@gmail.com"


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def _fill_signup_form(
    page: Any,
    phone: str,
    password: str,
    address: dict[str, str],
    log: LogFn | None,
) -> None:
    """Fill all fields on /checkoutweb/signup using page.fill() for speed."""
    emit(log, "paypal_http: browser filling signup form")

    _set_country_us(page)
    time.sleep(1)

    email = _rand_email()

    _fill_field(page, "email", email)
    _fill_field(page, "phone", _normalize_phone(phone))

    card = _generate_visa_card()
    _fill_field(page, "cardNumber", card["number"])
    _fill_field(page, "expiry", card["expiry"])
    _fill_field(page, "cvv", card["cvv"])

    first_name = address.get("first_name") or "Tommy"
    last_name = address.get("last_name") or "Jacobs"
    _fill_field(page, "firstName", first_name)
    _fill_field(page, "lastName", last_name)
    _fill_field(page, "street", address.get("line1") or "283 Clearview Drive")
    _fill_field(page, "city", address.get("city") or "Smyrna")

    _set_state(page, address.get("state") or "TN")

    _fill_field(page, "zip", address.get("postal_code") or "37167")
    _fill_field(page, "password", password)

    emit(log, f"paypal_http: browser form filled email={email} phone={_normalize_phone(phone)[:4]}...")


_FIELD_SELECTORS = {
    "email": ['input[name="email"]', "#email"],
    "phone": ['input[name="phone"]', 'input[name="phoneNumber"]', "#phone", 'input[name="mobilePhone"]'],
    "cardNumber": ['input[name="cardNumber"]', "#cardNumber", 'input[name="creditCardNumber"]'],
    "expiry": ['input[name="expirationDate"]', "#cardExpiry", 'input[name="cardExpiry"]'],
    "cvv": ['input[name="cvv"]', "#cardCvv", 'input[name="securityCode"]', 'input[name="cardCvv"]'],
    "firstName": ['input[name="firstName"]', "#firstName"],
    "lastName": ['input[name="lastName"]', "#lastName"],
    "street": ['input[name="addressLine1"]', "#billingLine1", 'input[name="billingLine1"]', 'input[name="streetAddress"]'],
    "city": ['input[name="city"]', "#billingCity", 'input[name="billingCity"]'],
    "zip": ['input[name="billingPostalCode"]', "#billingPostalCode", 'input[name="postalCode"]', 'input[name="zipCode"]'],
    "password": ['input[name="password"]', 'input[name="createPassword"]'],
}


def _fill_field(page: Any, name: str, value: str) -> None:
    if not value:
        return
    for sel in _FIELD_SELECTORS.get(name, []):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.scroll_into_view_if_needed()
                el.fill(value)
                return
        except Exception:
            continue


def _set_country_us(page: Any) -> None:
    for sel in ['#country', 'select[name="country"]']:
        try:
            el = page.query_selector(sel)
            if el:
                page.evaluate("""(el) => {
                    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
                    if (desc && desc.set) desc.set.call(el, 'US');
                    else el.value = 'US';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""", el)
                return
        except Exception:
            continue


def _set_state(page: Any, state_abbr: str) -> None:
    _STATE_MAP = {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
        "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
        "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
        "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
        "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
        "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
        "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
        "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
        "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
        "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
        "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    }
    full_name = _STATE_MAP.get(state_abbr.upper(), state_abbr)

    for sel in ['#billingState', 'select[name="state"]', 'select[name="stateCode"]', 'select[name="billingAdministrativeArea"]']:
        try:
            el = page.query_selector(sel)
            if el:
                page.evaluate("""([el, abbr, full]) => {
                    for (const opt of el.options) {
                        const txt = opt.textContent.trim();
                        const val = opt.value.trim();
                        if (val === abbr || txt === full || val === full || txt === abbr) {
                            const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
                            if (desc && desc.set) desc.set.call(el, opt.value);
                            else el.value = opt.value;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return;
                        }
                    }
                }""", [el, state_abbr.upper(), full_name])
                return
        except Exception:
            continue


def _generate_visa_card() -> dict[str, str]:
    import random
    base = "4147"
    while len(base) < 15:
        base += str(random.randint(0, 9))
    digits = [int(ch) for ch in base]
    total = 0
    parity = (len(digits) + 1) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = str((10 - (total % 10)) % 10)
    number = base + check
    month = str(random.randint(1, 12)).zfill(2)
    year = str(time.localtime().tm_year + 2 + random.randint(0, 3))
    return {
        "number": number,
        "expiry": f"{month}/{year[-2:]}",
        "cvv": str(random.randint(100, 999)),
    }


def _submit_and_handle_otp(
    page: Any,
    paypal_cfg: dict[str, Any],
    smsurl: str,
    log: LogFn | None,
) -> None:
    emit(log, "paypal_http: browser submitting signup form")
    _click_submit(page)

    for attempt in range(3):
        result = _wait_post_submit(page, timeout=15000)
        emit(log, f"paypal_http: browser post-submit state={result}")
        if result == "otp":
            break
        if result in ("hermes", "navigated"):
            return
        if result == "card_error":
            emit(log, "paypal_http: browser card error, regenerating card")
            card = _generate_visa_card()
            _fill_field(page, "cardNumber", card["number"])
            time.sleep(0.5)
            _click_submit(page)
            continue
        if attempt < 2:
            _click_submit(page)

    emit(log, "paypal_http: browser waiting for OTP inputs")
    _wait_for_otp_inputs(page, timeout=20)

    otp = fetch_paypal_otp(paypal_cfg, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("browser: PayPal phone OTP 获取失败")

    emit(log, f"paypal_http: browser filling OTP")
    _fill_otp(page, otp)

    otp_submit = _find_otp_submit(page)
    if otp_submit:
        otp_submit.click()
        emit(log, "paypal_http: browser OTP submitted")


def _click_submit(page: Any) -> None:
    for sel in [
        'button[data-testid="submit-button"]',
        'button[data-testid="hosted-payment-submit-button"]',
        'button.SubmitButton--complete',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.scroll_into_view_if_needed()
                el.click()
                time.sleep(2)
                return
        except Exception:
            continue
    for text in ["Next", "Continue", "Agree", "Pay", "Create an Account", "Create account"]:
        try:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                btn.click()
                time.sleep(2)
                return
        except Exception:
            continue


def _wait_post_submit(page: Any, timeout: int = 15000) -> str:
    start = time.time()
    start_url = page.url
    while (time.time() - start) * 1000 < timeout:
        cur = page.url
        if "/webapps/hermes" in cur:
            return "hermes"
        if cur != start_url and "/checkoutweb/signup" not in cur:
            return "navigated"
        try:
            otp_root = page.query_selector('[data-testid="sca-confirm-multi-field"], #ciBasic')
            if otp_root and otp_root.is_visible():
                return "otp"
        except Exception:
            pass
        try:
            alerts = page.query_selector_all('[role="alert"]')
            for a in alerts:
                text = (a.inner_text() or "").lower()
                if any(kw in text for kw in ("card", "declined", "not accepted", "try another")):
                    return "card_error"
        except Exception:
            pass
        time.sleep(0.5)
    return "timeout"


def _wait_for_otp_inputs(page: Any, timeout: int = 20) -> None:
    for _ in range(timeout * 2):
        try:
            root = page.query_selector('[data-testid="sca-confirm-multi-field"], #ciBasic')
            if root and root.is_visible():
                inputs = root.query_selector_all('input')
                if len(inputs) >= 4:
                    return
        except Exception:
            pass
        time.sleep(0.5)


def _fill_otp(page: Any, otp: str) -> None:
    digits = re.sub(r"\D", "", otp)
    root = page.query_selector('[data-testid="sca-confirm-multi-field"], #ciBasic')
    if not root:
        return
    inputs = root.query_selector_all('input')
    if len(inputs) >= len(digits):
        for i, d in enumerate(digits):
            try:
                page.evaluate("""([el, val]) => {
                    const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    if (desc && desc.set) desc.set.call(el, val);
                    else el.value = val;
                    el.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, inputType:'insertText', data:val}));
                    el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:val}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                }""", [inputs[i], d])
            except Exception:
                pass
        time.sleep(0.5)
    elif inputs:
        try:
            inputs[0].fill(digits)
        except Exception:
            pass


def _find_otp_submit(page: Any) -> Any:
    root = page.query_selector('[data-testid="sca-confirm-multi-field"], #ciBasic')
    if not root:
        root = page
    section = root
    try:
        section = root.query_selector('xpath=ancestor::section') or root
    except Exception:
        pass
    for sel in ['button[type="submit"]']:
        try:
            btn = section.query_selector(sel)
            if btn and btn.is_visible():
                text = (btn.inner_text() or "").lower()
                if "resend" not in text and "close" not in text:
                    return btn
        except Exception:
            pass
    for text in ["Verify", "Submit", "Continue", "Next", "Confirm", "Done"]:
        try:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn and btn.is_visible():
                inner = (btn.inner_text() or "").lower()
                if "resend" not in inner:
                    return btn
        except Exception:
            pass
    return None


def _wait_for_stripe_return(page: Any, log: LogFn | None) -> str:
    """Wait for hermes review → click Continue → wait for Stripe return URL."""
    emit(log, "paypal_http: browser waiting for hermes review / stripe return")

    for _ in range(120):
        cur = page.url
        if "pay.openai.com" in cur or "chatgpt.com" in cur or "checkout.stripe.com" in cur:
            emit(log, f"paypal_http: browser reached stripe return: {cur[:100]}")
            return cur
        if "/webapps/hermes" in cur:
            try:
                for text in ["Continue", "Agree & Continue", "Agree and Continue"]:
                    btn = page.query_selector(f'button:has-text("{text}")')
                    if btn and btn.is_visible():
                        btn.scroll_into_view_if_needed()
                        btn.click()
                        emit(log, f"paypal_http: browser clicked hermes '{text}'")
                        time.sleep(2)
                        break
            except Exception:
                pass
        time.sleep(1)

    raise PayPalHttpError(f"browser: 等待 Stripe return URL 超时, url={page.url[:120]}")
