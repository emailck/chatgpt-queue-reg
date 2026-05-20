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
    CheckCancelledFn,
    LogFn,
    PayPalHttpError,
    checkpoint,
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
    check_cancelled: CheckCancelledFn | None = None,
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
            checkpoint(check_cancelled)

            _wait_for_signup_page(page, log, check_cancelled)
            checkpoint(check_cancelled)

            for fill_attempt in range(3):
                fill_ok = _fill_signup_form(page, phone, password, address, log)
                if fill_ok:
                    break
                emit(log, f"paypal_http: browser form fill incomplete (attempt {fill_attempt + 1}/3), refreshing page")
                page.reload(wait_until="domcontentloaded", timeout=60000)
                time.sleep(5)
                checkpoint(check_cancelled)
                if "/checkoutweb/signup" not in page.url:
                    _wait_for_signup_page(page, log, check_cancelled)
            else:
                raise PayPalHttpError("browser: signup 表单填写 3 次均失败")

            checkpoint(check_cancelled)
            _submit_and_handle_otp(page, paypal_cfg, smsurl, log, check_cancelled)
            checkpoint(check_cancelled)

            return_url = _wait_for_stripe_return(page, log, check_cancelled)

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


def _wait_for_signup_page(page: Any, log: LogFn | None, check_cancelled: CheckCancelledFn | None = None) -> None:
    """Poll /pay page like the plugin's tick() — every 5s check state and act.

    The /pay SPA may auto-redirect (HAR shows ~140ms) or require manual
    interaction (#startOnboardingFlow → Create Account → email → Continue).
    """
    logged = False
    for i in range(24):
        checkpoint(check_cancelled)
        try:
            cur = page.url
        except Exception:
            time.sleep(2)
            continue
        if "/checkoutweb/signup" in cur:
            emit(log, "paypal_http: browser on signup page")
            time.sleep(2)
            return
        if "/webapps/hermes" in cur:
            emit(log, "paypal_http: browser landed on hermes directly")
            return
        if "chatgpt.com" in cur or "pay.openai.com" in cur:
            emit(log, f"paypal_http: browser already returned to Stripe: {cur[:80]}")
            return
        if ("/pay" in cur or "/agreements/approve" in cur) and "paypal.com" in cur:
            if not logged:
                emit(log, f"paypal_http: browser on {cur.split('?')[0].split('/')[-1]} page, starting poll loop")
                logged = True
            try:
                _pay_page_tick(page, log)
            except Exception as exc:
                emit(log, f"paypal_http: /pay tick exception (page navigating?): {str(exc)[:80]}")
                time.sleep(2)
                continue
        time.sleep(5)
    raise PayPalHttpError(f"browser: 等待 signup 页超时 (120s), url={page.url[:120]}")


def _pay_page_tick(page: Any, log: LogFn | None) -> None:
    """Single tick of the /pay page poll loop — mirrors plugin's middle.js tick().

    Priority order:
    1. "Create an Account" button visible → click → fill email → click Continue
    2. Email input visible → fill email → click Continue
    3. Page shows "Open a PayPal account" text → fill email → click Continue
    4. #startOnboardingFlow visible → click it (next tick will see the result)
    """
    result = page.evaluate("""() => {
        function canClick(btn) {
            if (!btn || btn.disabled) return false;
            const s = getComputedStyle(btn);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            const r = btn.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }

        // 1. Create an Account
        const onboardForm = document.querySelector('form[data-testid="xo-onboarding-form"] button[type="submit"]');
        if (canClick(onboardForm)) return 'create_account';
        const createBtns = Array.from(document.querySelectorAll('button'));
        const createBtn = createBtns.find(b => /create an account|create account/i.test((b.innerText||'').trim()));
        if (canClick(createBtn)) return 'create_account';

        // 2. Email input
        const emailSels = ['#onboardingFlowEmail', '#email', 'input[name="login_email"]', 'input[type="email"]'];
        for (const sel of emailSels) {
            const el = document.querySelector(sel);
            if (el && !el.disabled && el.getBoundingClientRect().width > 0) return 'email_visible';
        }

        // 3. Stop texts
        const bodyText = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ');
        if (bodyText.includes('Open a PayPal account') || bodyText.includes('Already have an account')) return 'email_visible';

        // 4. #startOnboardingFlow
        const start = document.querySelector('#startOnboardingFlow');
        if (canClick(start)) return 'start_onboarding';

        return 'waiting';
    }""")

    if result == "create_account":
        page.evaluate("""() => {
            const btn = document.querySelector('form[data-testid="xo-onboarding-form"] button[type="submit"]')
                || Array.from(document.querySelectorAll('button')).find(b => /create an account|create account/i.test((b.innerText||'').trim()));
            if (btn) { btn.scrollIntoView({block:'center'}); btn.click(); }
        }""")
        emit(log, "paypal_http: /pay tick: clicked Create an Account")
        time.sleep(2)
        _fill_pay_email_and_continue(page, log)
    elif result == "email_visible":
        _fill_pay_email_and_continue(page, log)
    elif result == "start_onboarding":
        page.evaluate("() => { const b = document.querySelector('#startOnboardingFlow'); if (b) b.click(); }")
        emit(log, "paypal_http: /pay tick: clicked #startOnboardingFlow")
    else:
        emit(log, "paypal_http: /pay tick: waiting for elements...")


def _fill_pay_email_and_continue(page: Any, log: LogFn | None) -> None:
    """Fill email on /pay page and click Continue/Keep Paying."""
    email = _rand_email()
    filled = page.evaluate("""(email) => {
        const sels = ['#onboardingFlowEmail', '#email', 'input[name="login_email"]', 'input[type="email"]'];
        for (const sel of sels) {
            const el = document.querySelector(sel);
            if (el && !el.disabled && el.getBoundingClientRect().width > 0) {
                const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                if (desc && desc.set) desc.set.call(el, email);
                else el.value = email;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
                return true;
            }
        }
        return false;
    }""", email)
    if filled:
        emit(log, f"paypal_http: /pay tick: filled email={email}")
    time.sleep(1)

    page.evaluate("""() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        const btn = buttons.find(b => {
            const text = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
            const intent = b.getAttribute('data-atomic-wait-intent') || '';
            if (/cancel|back|log in|login/i.test(text)) return false;
            return /submit_email/i.test(intent) ||
                (b.type === 'submit' && /keep paying|continue to payment|continue|next/i.test(text));
        }) || document.querySelector('button.actionContinue[type="submit"]');
        if (btn && !btn.disabled) { btn.scrollIntoView({block:'center'}); btn.click(); }
    }""")
    emit(log, "paypal_http: /pay tick: clicked Continue/Keep Paying")


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
) -> dict[str, str]:
    """Fill all fields on /checkoutweb/signup with React-safe value injection."""
    emit(log, "paypal_http: browser filling signup form")

    _remove_captcha_elements(page)

    diag = page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
        const visible = inputs.filter(el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        });
        return {
            url: location.href.slice(0, 150),
            title: document.title.slice(0, 100),
            totalInputs: inputs.length,
            visibleInputs: visible.length,
            fields: visible.slice(0, 20).map(el => ({
                tag: el.tagName,
                type: el.getAttribute('type') || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                autoComplete: el.autocomplete || '',
            })),
        };
    }""")
    emit(log, f"paypal_http: browser signup page diag: {diag}")

    _set_country_us(page)
    time.sleep(1)

    email = _rand_email()
    card = _generate_visa_card()
    first_name = address.get("first_name") or "Tommy"
    last_name = address.get("last_name") or "Jacobs"
    phone_norm = _normalize_phone(phone)

    if not _wait_for_any_field(page, timeout=10):
        emit(log, "paypal_http: browser signup fields not rendered after 10s", level="warning")
        return False

    fields = [
        ("email", email),
        ("phone", phone_norm),
        ("cardNumber", card["number"]),
        ("expiry", card["expiry"]),
        ("cvv", card["cvv"]),
        ("firstName", first_name),
        ("lastName", last_name),
        ("street", address.get("line1") or "283 Clearview Drive"),
        ("city", address.get("city") or "Smyrna"),
    ]
    for name, value in fields:
        _fill_field_safe(page, name, value, log)

    _set_state(page, address.get("state") or "TN")

    _fill_field_safe(page, "zip", address.get("postal_code") or "37167", log)
    _fill_field_safe(page, "password", password, log)

    mismatches = _verify_fields(page, fields + [("zip", address.get("postal_code") or "37167")], log)
    if mismatches:
        emit(log, f"paypal_http: browser form verification failed: {mismatches}", level="warning")
        return False

    emit(log, f"paypal_http: browser form filled email={email} phone={phone_norm[:4]}...")
    return True


_FIELD_SELECTORS = {
    "email": {"tag": "input", "names": ["email", "login_email"], "selectors": ["#email"], "placeholders": ["email"], "labels": ["email"]},
    "phone": {"tag": "input", "names": ["phone", "phoneNumber", "telephone", "mobilePhone"], "selectors": ["#phone"], "placeholders": ["phone", "mobile"], "labels": ["phone number", "mobile number"]},
    "cardNumber": {"tag": "input", "names": ["cardNumber", "creditCardNumber"], "selectors": ["#cardNumber"], "placeholders": ["card number"], "labels": ["card number"]},
    "expiry": {"tag": "input", "names": ["expirationDate", "expiry", "cardExpiry"], "selectors": ["#cardExpiry"], "placeholders": ["expiration", "mm / yy", "mm/yy"], "labels": ["expiration"]},
    "cvv": {"tag": "input", "names": ["cvv", "cvc", "securityCode", "cardCvv"], "selectors": ["#cardCvv"], "placeholders": ["cvv", "cvc", "security code"], "labels": ["cvv", "cvc", "security code"]},
    "firstName": {"tag": "input", "names": ["firstName", "givenName"], "selectors": ["#firstName"], "placeholders": ["first name"], "labels": ["first name"]},
    "lastName": {"tag": "input", "names": ["lastName", "familyName", "surname"], "selectors": ["#lastName"], "placeholders": ["last name"], "labels": ["last name"]},
    "street": {"tag": "input", "names": ["addressLine1", "streetAddress", "line1", "billingAddressLine1", "billingLine1"], "selectors": ["#billingLine1"], "placeholders": ["street address", "address line 1", "street"], "labels": ["street address", "address"]},
    "city": {"tag": "input", "names": ["city", "locality", "billingLocality", "billingCity"], "selectors": ["#billingCity"], "placeholders": ["city"], "labels": ["city"]},
    "zip": {"tag": "input", "names": ["billingPostalCode", "postalCode", "zipCode", "zip", "postal_code"], "selectors": ["#billingPostalCode", 'input[autocomplete="postal-code"]'], "placeholders": ["zip code", "postal code", "zip", "postal"], "labels": ["zip code", "postal code"]},
    "password": {"tag": "input", "names": ["password", "createPassword", "newPassword"], "selectors": [], "placeholders": ["create password", "password"], "labels": ["create password", "password"]},
}


def _remove_captcha_elements(page: Any) -> None:
    try:
        page.evaluate("""() => {
            const sels = [
                '#captchaComponent', '.captcha-overlay', '.captcha-container',
                '.appChallengeNS', '#g-anomalydetection-div',
                'iframe[src*="recaptcha"]', 'iframe[title*="recaptcha" i]',
            ];
            for (const sel of sels) {
                document.querySelectorAll(sel).forEach(el => el.remove());
            }
            document.querySelectorAll('iframe').forEach(f => {
                if (/recaptcha|captcha|challenge/i.test((f.src||'')+(f.title||''))) f.remove();
            });
            document.documentElement.style.overflow = '';
            document.body.style.overflow = '';
        }""")
    except Exception:
        pass


def _wait_for_any_field(page: Any, timeout: int = 10) -> bool:
    """Wait up to timeout seconds for signup form fields to render."""
    for _ in range(timeout * 2):
        count = page.evaluate("""() => {
            const all = document.querySelectorAll('input');
            let visible = 0;
            for (const el of all) {
                if (el.disabled) continue;
                const type = (el.getAttribute('type') || 'text').toLowerCase();
                if (['hidden','submit','button','checkbox','radio'].includes(type)) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) visible++;
            }
            return visible;
        }""")
        if count >= 5:
            return True
        time.sleep(0.5)
    return False


def _fill_field_safe(page: Any, name: str, value: str, log: LogFn | None) -> None:
    """Find field using plugin's multi-strategy search, then fill with React-safe setter."""
    if not value:
        return
    cfg = _FIELD_SELECTORS.get(name)
    if not cfg:
        return
    selectors = cfg.get("selectors", [])
    names = cfg.get("names", [])
    placeholders = cfg.get("placeholders", [])
    labels = cfg.get("labels", [])
    tag = cfg.get("tag", "input")

    deadline = time.time() + 5
    while time.time() < deadline:
        found = page.evaluate("""([tag, selectors, names, placeholders, labels, value]) => {
            function isFillable(el) {
                if (!el || el.tagName.toLowerCase() !== tag || el.disabled || el.readOnly) return false;
                if (tag === 'input') {
                    const type = (el.getAttribute('type') || 'text').toLowerCase();
                    if (['hidden','submit','button','checkbox','radio'].includes(type)) return false;
                }
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            }
            function tryFill(el) {
                el.scrollIntoView({block:'center'});
                el.focus();
                const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, '');
                el.dispatchEvent(new Event('input', {bubbles:true}));
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur', {bubbles:true}));
                return true;
            }
            // 1. CSS selectors
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (isFillable(el)) return tryFill(el);
            }
            // 2. name attribute
            for (const n of names) {
                const el = document.querySelector(tag + '[name="' + n + '"]');
                if (isFillable(el)) return tryFill(el);
                const byId = document.getElementById(n);
                if (isFillable(byId)) return tryFill(byId);
            }
            // 3. placeholder
            const all = Array.from(document.querySelectorAll(tag)).filter(isFillable);
            for (const p of placeholders) {
                const target = p.toLowerCase();
                const el = all.find(e => (e.placeholder || '').toLowerCase().includes(target));
                if (el) return tryFill(el);
            }
            // 4. aria-label
            for (const p of placeholders.concat(labels)) {
                const target = p.toLowerCase();
                const el = all.find(e => (e.getAttribute('aria-label') || '').toLowerCase().includes(target));
                if (el) return tryFill(el);
            }
            // 5. label text
            for (const text of labels) {
                const target = text.toLowerCase();
                const label = Array.from(document.querySelectorAll('label'))
                    .find(l => l.textContent.trim().toLowerCase().includes(target));
                if (label) {
                    const forId = label.getAttribute('for');
                    if (forId) { const el = document.getElementById(forId); if (isFillable(el)) return tryFill(el); }
                    const inner = label.querySelector(tag);
                    if (isFillable(inner)) return tryFill(inner);
                }
            }
            return false;
        }""", [tag, selectors, names, placeholders, labels, value])
        if found:
            time.sleep(0.3)
            return
        time.sleep(0.5)
    emit(log, f"paypal_http: browser WARNING field '{name}' not found after 30s", level="warning")


def _verify_fields(page: Any, fields: list[tuple[str, str]], log: LogFn | None) -> list[str]:
    """Read back field values; return list of field names that are empty or mismatched."""
    mismatches = []
    for name, expected in fields:
        if not expected:
            continue
        cfg = _FIELD_SELECTORS.get(name)
        if not cfg:
            continue
        all_sels = cfg.get("selectors", []) + [f'input[name="{n}"]' for n in cfg.get("names", [])]
        for sel in all_sels:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    actual = (el.input_value() or "").strip()
                    exp_digits = re.sub(r"\D", "", expected)
                    act_digits = re.sub(r"\D", "", actual)
                    if not actual:
                        mismatches.append(name)
                    elif exp_digits and len(exp_digits) >= 3 and exp_digits[-3:] not in act_digits:
                        mismatches.append(name)
                    break
            except Exception:
                continue
    return mismatches


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
    check_cancelled: CheckCancelledFn | None = None,
) -> None:
    emit(log, "paypal_http: browser submitting signup form")
    _click_submit(page)

    for attempt in range(3):
        checkpoint(check_cancelled)
        result = _wait_post_submit(page, timeout=15000)
        emit(log, f"paypal_http: browser post-submit state={result}")
        if result == "otp":
            break
        if result in ("hermes", "navigated"):
            return
        if result == "card_error":
            emit(log, "paypal_http: browser card error, regenerating card")
            card = _generate_visa_card()
            _fill_field_safe(page, "cardNumber", card["number"], log)
            time.sleep(0.5)
            _click_submit(page)
            continue
        if attempt < 2:
            _click_submit(page)

    checkpoint(check_cancelled)
    emit(log, "paypal_http: browser waiting for OTP inputs")
    input_count = _wait_for_otp_inputs(page, timeout=20)
    expected_length = input_count if input_count > 1 else 6
    emit(log, f"paypal_http: browser OTP detected inputs={input_count} expectedLength={expected_length}")

    number_id = int(paypal_cfg.get("_number_id") or 0)
    if number_id:
        from backend.core.pools.paypal_number_pool import paypal_number_pool
        otp = paypal_number_pool.fetch_otp(
            number_id, expected_length=expected_length,
            timeout=int(paypal_cfg.get("otp_timeout") or 90),
            check_cancelled=check_cancelled,
        )
    else:
        otp = _fetch_otp_with_length_check(
            paypal_cfg, smsurl, expected_length,
            timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log,
            check_cancelled=check_cancelled,
        )

    emit(log, "paypal_http: browser filling OTP")
    _fill_otp_cells(page, otp, log)

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


def _wait_for_otp_inputs(page: Any, timeout: int = 20) -> int:
    """Wait for OTP input fields to appear. Returns the input count."""
    for _ in range(timeout * 2):
        count = _detect_otp_input_count(page)
        if count > 0:
            return count
        time.sleep(0.5)
    return 0


def _detect_otp_input_count(page: Any) -> int:
    """Detect OTP inputs using the same heuristics as the browser plugin."""
    try:
        return page.evaluate("""() => {
            function isSmsCodeInput(input) {
                if (!input || input.disabled || input.readOnly) return false;
                const rect = input.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                const text = [input.name, input.id, input.placeholder, input.autocomplete,
                    input.getAttribute('aria-label'), input.getAttribute('data-testid')].join(' ').toLowerCase();
                if (/phone|mobile|card|cvv|cvc|expiry|expiration|email|password|postal|zip|billing|address|city|state|country/.test(text)) return false;
                if (/otp|code|security|verification|one-time/.test(text)) return true;
                const maxLen = Number(input.getAttribute('maxlength') || input.maxLength || 0);
                const isPayPalCell = input.closest('#ciBasic') && /^(cibasic-|ci-cibasic-|ci-ci-)/.test(text);
                const isOneDigit = /^(ci-|ci_|otp-|otp_|code-|code_)/.test(text) && (maxLen === 0 || maxLen === 1);
                return isPayPalCell || isOneDigit || (/numeric|tel/.test(input.inputMode + ' ' + input.type) && maxLen >= 4 && maxLen <= 8);
            }
            // scoped: #ciBasic multi-digit cells
            const root = document.querySelector('[data-testid="sca-confirm-multi-field"]') || document.getElementById('ciBasic');
            if (root) {
                const scoped = Array.from(root.querySelectorAll('input')).filter(isSmsCodeInput);
                if (scoped.length >= 4) return scoped.length;
            }
            // fallback: single input
            const sels = ['input[autocomplete="one-time-code"]', 'input[name*="otp" i]',
                'input[name*="security" i]', 'input[name*="verification" i]',
                'input[inputmode="numeric"]', 'input[type="tel"]'];
            for (const sel of sels) {
                const el = Array.from(document.querySelectorAll(sel)).find(isSmsCodeInput);
                if (el) return 1;
            }
            return 0;
        }""")
    except Exception:
        return 0


def _fetch_otp_with_length_check(
    paypal_cfg: dict[str, Any],
    smsurl: str,
    expected_length: int,
    timeout: int,
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
) -> str:
    """Poll smsurl for OTP, only accept tokens matching expectedLength."""
    import json
    import requests as std_requests

    deadline = time.time() + timeout
    attempts = 0
    while time.time() < deadline:
        checkpoint(check_cancelled)
        attempts += 1
        try:
            resp = std_requests.get(smsurl, timeout=15)
            text = resp.text or ""
            try:
                payload = resp.json()
                text += " " + json.dumps(payload, ensure_ascii=False)
            except Exception:
                pass
            for match in re.finditer(r"\b(\d{4,8})\b", text):
                token = match.group(1)
                if len(token) == expected_length:
                    emit(log, f"paypal_http: OTP received len={len(token)} attempts={attempts}")
                    return token
                else:
                    emit(log, f"paypal_http: OTP ignored len={len(token)} expected={expected_length}", level="warning")
        except Exception as exc:
            emit(log, f"paypal_http: smsurl poll failed: {exc}", level="warning")
        time.sleep(3)

    raise PayPalHttpError(f"OTP 获取超时 ({timeout}s), expected_length={expected_length}")


def _fill_otp_cells(page: Any, otp: str, log: LogFn | None) -> None:
    """Fill OTP using the plugin's fillOtpCell event chain for React compatibility."""
    digits = re.sub(r"\D", "", otp)
    page.evaluate("""([digits]) => {
        function isSmsCodeInput(input) {
            if (!input || input.disabled || input.readOnly) return false;
            const rect = input.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            const text = [input.name, input.id, input.placeholder, input.autocomplete,
                input.getAttribute('aria-label'), input.getAttribute('data-testid')].join(' ').toLowerCase();
            if (/phone|mobile|card|cvv|cvc|expiry|expiration|email|password|postal|zip|billing|address|city|state|country/.test(text)) return false;
            if (/otp|code|security|verification|one-time/.test(text)) return true;
            const maxLen = Number(input.getAttribute('maxlength') || input.maxLength || 0);
            const isPayPalCell = input.closest('#ciBasic') && /^(cibasic-|ci-cibasic-|ci-ci-)/.test(text);
            const isOneDigit = /^(ci-|ci_|otp-|otp_|code-|code_)/.test(text) && (maxLen === 0 || maxLen === 1);
            return isPayPalCell || isOneDigit || (/numeric|tel/.test(input.inputMode + ' ' + input.type) && maxLen >= 4 && maxLen <= 8);
        }
        function setVal(el, val) {
            const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
            if (desc && desc.set) desc.set.call(el, val);
            else el.value = val;
        }
        function fillCell(input, digit) {
            input.scrollIntoView({block:'center'});
            input.focus();
            setVal(input, '');
            input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'deleteContentBackward', data:null}));
            input.dispatchEvent(new KeyboardEvent('keydown', {key:digit, code:'Digit'+digit, bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keypress', {key:digit, code:'Digit'+digit, bubbles:true}));
            setVal(input, digit);
            input.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, cancelable:true, inputType:'insertText', data:digit}));
            input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:digit}));
            input.dispatchEvent(new KeyboardEvent('keyup', {key:digit, code:'Digit'+digit, bubbles:true}));
            input.dispatchEvent(new Event('change', {bubbles:true}));
        }
        const root = document.querySelector('[data-testid="sca-confirm-multi-field"]') || document.getElementById('ciBasic');
        if (root) {
            const inputs = Array.from(root.querySelectorAll('input')).filter(isSmsCodeInput)
                .sort((a,b) => {
                    const ai = Number((a.name||a.id||'').match(/(\\d+)$/)?.[1]||0);
                    const bi = Number((b.name||b.id||'').match(/(\\d+)$/)?.[1]||0);
                    return ai - bi;
                });
            if (inputs.length > 1) {
                const chars = digits.split('');
                for (let i = 0; i < inputs.length && i < chars.length; i++) fillCell(inputs[i], chars[i]);
                if (inputs.length > 0) inputs[Math.min(chars.length, inputs.length)-1].dispatchEvent(new Event('blur', {bubbles:true}));
                return;
            }
        }
        // single input fallback
        const sels = ['input[autocomplete="one-time-code"]', 'input[name*="otp" i]',
            'input[name*="verification" i]', 'input[inputmode="numeric"]', 'input[type="tel"]'];
        for (const sel of sels) {
            const el = Array.from(document.querySelectorAll(sel)).find(isSmsCodeInput);
            if (el) { setVal(el, digits); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return; }
        }
    }""", [digits])
    time.sleep(0.5)
    emit(log, f"paypal_http: browser OTP cells filled len={len(digits)}")


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


def _wait_for_stripe_return(page: Any, log: LogFn | None, check_cancelled: CheckCancelledFn | None = None) -> str:
    """Wait for hermes review → click Continue → wait for Stripe return URL."""
    emit(log, "paypal_http: browser waiting for hermes review / stripe return")

    for _ in range(120):
        checkpoint(check_cancelled)
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
