from __future__ import annotations

import os
import random
import re
import shutil
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .paypal_guest_signup import (
    _confirm_phone_payload,
    _extract_buyer_access_token,
    _extract_phone_confirmation,
    _hermes_url,
    _initiate_phone_payload,
    _signup_payload,
    _signup_response_summary,
)
from .paypal_login import fetch_paypal_otp, solve_paypal_hcaptcha
from .runtime import (
    CheckCancelledFn,
    LogFn,
    PayPalHttpError,
    checkpoint,
    emit,
    first_match,
    gen_paypal_password,
    phone_country_code,
    query_value,
    strip_phone_country_code,
)


def _human_delay(lo: float = 0.5, hi: float = 1.5) -> None:
    time.sleep(lo + random.random() * (hi - lo))


def _type_delay() -> int:
    """Per-character delay in ms for element.type()."""
    return random.randint(60, 160)


def _human_click(page: Any, el: Any, timeout: int = 5000) -> None:
    """Click with mouse move — Playwright moves to element center, then clicks.

    Short timeout keeps a stuck click from blocking the state machine.
    """
    try:
        el.scroll_into_view_if_needed()
    except Exception:
        pass
    _human_delay(0.2, 0.5)
    el.click(timeout=timeout)
    _human_delay(0.3, 0.8)


def _pay_page_click(page: Any, el: Any, log: LogFn | None, label: str) -> bool:
    try:
        _human_click(page, el, timeout=2500)
        return True
    except Exception as exc:
        msg = str(exc)
        emit(log, f"paypal_http: /pay {label} normal click failed: {msg[:90]}", level="warning")
    try:
        el.click(timeout=1500, force=True)
        emit(log, f"paypal_http: /pay {label} force click ok")
        return True
    except Exception as exc:
        emit(log, f"paypal_http: /pay {label} force click failed: {str(exc)[:90]}", level="warning")
    try:
        page.evaluate("(el) => { el.scrollIntoView({block:'center'}); el.click(); }", el)
        emit(log, f"paypal_http: /pay {label} JS click ok")
        return True
    except Exception as exc:
        emit(log, f"paypal_http: /pay {label} JS click failed: {str(exc)[:90]}", level="warning")
    return False


def _human_type(page: Any, el: Any, value: str) -> None:
    """Slow human-like typing using Playwright's trusted input events.

    Used for middle page (/pay) where there's no autocomplete dropdown risk.
    isTrusted:true keystrokes via browser's native input pipeline.
    """
    try:
        el.scroll_into_view_if_needed()
    except Exception:
        pass
    _human_delay(0.2, 0.5)
    el.click()
    _human_delay(0.1, 0.3)
    el.press("Control+a")
    el.press("Backspace")
    _human_delay(0.1, 0.2)
    el.type(value, delay=random.randint(80, 180))
    _human_delay(0.3, 0.8)


def _instant_fill(page: Any, el: Any, value: str) -> None:
    """Instant JS-based fill via native setter for signup form.

    Speed matters here: typing slowly into street address triggers PayPal's
    address autocomplete dropdown which blocks clicks on other fields.
    The signup-only fake verification watcher keeps this page fillable.
    """
    page.evaluate("""([el, value]) => {
        el.scrollIntoView({block:'center'});
        el.focus();
        const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) desc.set.call(el, '');
        el.dispatchEvent(new Event('input', {bubbles: true}));
        if (desc && desc.set) desc.set.call(el, value);
        else el.value = value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur', {bubbles: true}));
    }""", [el, value])
    time.sleep(0.1)


def browser_paypal_checkout(
    approve_url: str,
    ba_token: str,
    proxy_url: str,
    paypal_cfg: dict[str, Any],
    address: dict[str, str],
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Drive the entire PayPal side in Camoufox. Retries with fresh browser on crash."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            emit(log, f"paypal_http: browser retry {attempt + 1}/{max_retries}")
        checkpoint(check_cancelled)
        try:
            return _browser_checkout_once(
                approve_url, ba_token, proxy_url, paypal_cfg, address, log, check_cancelled,
            )
        except PayPalHttpError as exc:
            last_error = exc
            if _requires_payment_proxy_rotation(exc):
                emit(log, f"paypal_http: browser attempt {attempt + 1} requires proxy rotation: {str(exc)[:120]}", level="warning")
                raise
            if _is_retryable_browser_checkout_error(exc):
                emit(log, f"paypal_http: browser attempt {attempt + 1} failed (will retry): {str(exc)[:100]}")
                continue
            raise
        except Exception as exc:
            last_error = exc
            emit(log, f"paypal_http: browser attempt {attempt + 1} failed: {str(exc)[:100]}")
            continue
    raise PayPalHttpError(f"browser checkout {max_retries} 次均失败: {last_error}")


def _requires_payment_proxy_rotation(exc: Exception) -> bool:
    return "payment_proxy_rotation_required" in str(exc or "").lower()



def _is_retryable_browser_checkout_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return any(token in text for token in (
        "crashed",
        "closed",
        "超时",
        "ns_error_net_interrupt",
        "net::err_",
        "navigation timeout",
        "page.goto",
        "tls connect error",
        "connection reset",
        "connection aborted",
        "otp confirmation did not submit",
    ))


def _browser_checkout_once(
    approve_url: str,
    ba_token: str,
    proxy_url: str,
    paypal_cfg: dict[str, Any],
    address: dict[str, str],
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
) -> dict[str, Any]:
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
            headless=True,
            humanize=True,
            persistent_context=True,
            user_data_dir=tmp_profile,
            os="windows",
            screen=Screen(max_width=1920, max_height=1080),
            proxy=cf_proxy,
            geoip=True,
            locale="en-US",
            i_know_what_im_doing=True,
            disable_coop=True,
            config={"showcursor": False},
        ) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            challenge_state = _install_paypal_challenge_watch(page, log)

            emit(log, "paypal_http: browser navigating to approve URL")
            page.goto(approve_url, wait_until="domcontentloaded", timeout=60000)
            checkpoint(check_cancelled)

            _wait_for_signup_page(page, log, check_cancelled, approve_url=approve_url, paypal_cfg=paypal_cfg, challenge_state=challenge_state)
            checkpoint(check_cancelled)

            submitted = False
            for fill_attempt in range(3):
                if _is_post_signup_progress(page):
                    emit(log, "paypal_http: browser already reached checkout review after signup")
                    submitted = True
                    break

                filled_fields = _fill_signup_form(page, phone, password, address, log)
                if not filled_fields:
                    if _is_post_signup_progress(page):
                        emit(log, "paypal_http: browser reached checkout review while checking signup fields")
                        submitted = True
                        break
                    _reload_signup_page(page, log, f"signup fields not ready before submit attempt={fill_attempt + 1}/3")
                    continue

                checkpoint(check_cancelled)
                if _submit_and_handle_otp(page, paypal_cfg, smsurl, filled_fields, address, ba_token, log, check_cancelled):
                    submitted = True
                    break
                if _wait_for_post_signup_progress(page, timeout=8, log=log):
                    submitted = True
                    break
                if fill_attempt < 2:
                    _reload_signup_page(page, log, f"no OTP/navigation after submit attempt={fill_attempt + 1}/3")
            if not submitted:
                raise PayPalHttpError("payment_proxy_rotation_required: browser signup page did not render required fields or OTP/navigation after refresh")
            checkpoint(check_cancelled)

            return_url = _wait_for_stripe_return(page, log, check_cancelled, paypal_cfg=paypal_cfg, challenge_state=challenge_state)

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


def _wait_for_signup_page(
    page: Any,
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
    approve_url: str = "",
    paypal_cfg: dict[str, Any] | None = None,
    challenge_state: dict[str, Any] | None = None,
) -> None:
    """Poll /pay page like the plugin's tick() — every 5s check state and act.

    The /pay SPA may auto-redirect (HAR shows ~140ms) or require manual
    interaction (#startOnboardingFlow → Create Account → email → Continue).
    """
    loaded = False
    flow_state: dict[str, Any] = {}
    deadline = time.time() + 180
    while time.time() < deadline:
        checkpoint(check_cancelled)
        try:
            cur = page.url
        except Exception as exc:
            if "has been closed" in str(exc) or "crashed" in str(exc) or "Target closed" in str(exc):
                raise PayPalHttpError(f"browser tab crashed: {str(exc)[:120]}")
            time.sleep(2)
            continue
        if "/checkoutweb/signup" in cur:
            _normalize_signup_guest_url(page, log)
            emit(log, "paypal_http: browser on signup page")
            time.sleep(2)
            return
        if "/webapps/hermes" in cur:
            emit(log, "paypal_http: browser landed on hermes directly")
            return
        if "chatgpt.com" in cur or "pay.openai.com" in cur:
            emit(log, f"paypal_http: browser already returned to Stripe: {cur[:80]}")
            return
        if "paypal.com" in cur and _check_rsc_redirect(page, log):
            continue
        if "paypal.com" in cur and _check_paypal_error_page(page, log) and approve_url:
            emit(log, "paypal_http: PayPal 'Something went wrong' page — navigating back to approve URL")
            try:
                page.goto(approve_url, wait_until="commit", timeout=15000)
                time.sleep(5)
                loaded = False
            except Exception as exc:
                emit(log, f"paypal_http: goto approve_url failed: {str(exc)[:80]}")
                time.sleep(3)
            continue
        if "paypal.com" in cur and "/checkoutweb/signup" not in cur:
            _remove_paypal_fake_captcha_elements(page)
            _raise_if_paypal_challenge_blocked(page, log, challenge_state)
            if _solve_real_hcaptcha_if_present(page, paypal_cfg or {}, log):
                time.sleep(2)
                continue
        if ("/pay" in cur or "/agreements/approve" in cur) and "paypal.com" in cur:
            if not loaded:
                loaded = True
                emit(log, "paypal_http: browser on /pay page, starting guest flow")
            try:
                if _pay_continue_needs_recovery(page, flow_state):
                    _recover_pay_after_continue_stall(page, flow_state, log, approve_url)
                    continue
                if _pay_continue_waiting_for_progress(page, flow_state):
                    time.sleep(1)
                    continue
                acted = _pay_page_tick(page, log, flow_state)
                if acted:
                    flow_state["entry_wait_started_at"] = 0
                    flow_state["entry_wait_logged_at"] = 0
                    continue
                if _pay_entry_waiting_needs_recovery(page, flow_state):
                    _recover_pay_entry_wait(page, flow_state, log, approve_url)
                    continue
            except _PayContinueStuck as exc:
                raise PayPalHttpError(f"payment_proxy_rotation_required: /pay guest flow stuck: {str(exc)[:180]}") from exc
            except Exception as exc:
                msg = str(exc)
                if "crashed" in msg or "browser has been closed" in msg:
                    raise PayPalHttpError(f"browser tab crashed: {msg[:120]}")
                if "Target closed" in msg or "has been closed" in msg or "Element is not attached" in msg:
                    if _browser_still_alive(page):
                        emit(log, f"paypal_http: /pay tick element stale (page navigating): {msg[:80]}")
                        time.sleep(1)
                        continue
                    raise PayPalHttpError(f"browser tab crashed: {msg[:120]}")
                if "Timeout" in msg and "click" in msg.lower():
                    if _pay_continue_recovery_exhausted(page, flow_state):
                        raise PayPalHttpError(f"payment_proxy_rotation_required: /pay guest flow stuck: {_pay_continue_stuck_message(page, flow_state)}") from exc
                    if _pay_continue_needs_recovery(page, flow_state):
                        _recover_pay_after_continue_stall(page, flow_state, log, approve_url)
                    else:
                        emit(log, "paypal_http: /pay tick: click timed out, continuing guest state machine", level="warning")
                        time.sleep(1)
                    continue
                emit(log, f"paypal_http: /pay tick exception: {msg[:80]}")
                time.sleep(2)
                continue
        time.sleep(2)
    raise PayPalHttpError(f"payment_proxy_rotation_required: browser waiting for signup page timed out (120s), url={page.url[:120]}")


def _normalize_signup_guest_url(page: Any, log: LogFn | None) -> bool:
    try:
        cur = page.url
    except Exception:
        return False
    if "/checkoutweb/signup" not in cur:
        return False
    try:
        parts = urlparse(cur)
        params = parse_qs(parts.query, keep_blank_values=True)
        changed = False
        if params.get("ul", [""])[0] != "1":
            params["ul"] = ["1"]
            changed = True
        if params.get("modxo_redirect_reason", [""])[0] != "guest_user":
            params["modxo_redirect_reason"] = ["guest_user"]
            changed = True
        if not changed:
            return False
        target = urlunparse((
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.params,
            urlencode(params, doseq=True),
            parts.fragment,
        ))
        emit(log, f"paypal_http: signup URL normalized to guest_user: {target[:120]}")
        page.goto(target, wait_until="commit", timeout=15000, referer=cur)
        time.sleep(2)
        return True
    except Exception as exc:
        emit(log, f"paypal_http: signup URL normalize failed: {str(exc)[:100]}", level="warning")
        return False


def _browser_still_alive(page: Any) -> bool:
    """Distinguish a genuinely closed browser from a navigating page with stale refs."""
    for attempt in range(5):
        try:
            url = page.url
            if url:
                return True
        except Exception:
            time.sleep(0.5)
            continue
    return False


def _check_paypal_error_page(page: Any, log: LogFn | None) -> bool:
    """Detect PayPal's 'Something went wrong on our end' error page."""
    try:
        return bool(page.evaluate("""() => {
            const text = (document.body && document.body.innerText || '').toLowerCase();
            return text.includes("something went wrong on our end") ||
                   text.includes("we're having some trouble completing your request") ||
                   text.includes("having some trouble completing");
        }"""))
    except Exception:
        return False


def _pay_continue_waiting_for_progress(page: Any, flow_state: dict[str, Any]) -> bool:
    clicked_at = float(flow_state.get("continue_clicked_at") or 0)
    if not clicked_at or time.time() - clicked_at >= 10:
        return False
    return _pay_continue_still_on_entry_page(page)


def _pay_entry_waiting_needs_recovery(page: Any, flow_state: dict[str, Any]) -> bool:
    if not _pay_continue_still_on_entry_page(page):
        flow_state["entry_wait_started_at"] = 0
        return False
    now = time.time()
    started = float(flow_state.get("entry_wait_started_at") or 0)
    if not started:
        flow_state["entry_wait_started_at"] = now
        return False
    recoveries = int(flow_state.get("entry_wait_recoveries") or 0)
    if now - started >= 45 and recoveries >= 2:
        raise _PayContinueStuck(_pay_entry_waiting_stuck_message(page, flow_state))
    return now - started >= 18


def _pay_entry_waiting_stuck_message(page: Any, flow_state: dict[str, Any]) -> str:
    try:
        cur = page.url
    except Exception:
        cur = ""
    recoveries = int(flow_state.get("entry_wait_recoveries") or 0)
    return f"entry page no actionable controls after recoveries={recoveries} url={cur[:120]}"


def _pay_continue_needs_recovery(page: Any, flow_state: dict[str, Any]) -> bool:
    clicked_at = float(flow_state.get("continue_clicked_at") or 0)
    if not clicked_at or time.time() - clicked_at < 10:
        return False
    if _pay_continue_recovery_exhausted(page, flow_state):
        raise _PayContinueStuck(_pay_continue_stuck_message(page, flow_state))
    return _pay_continue_still_on_entry_page(page)


def _pay_continue_recovery_exhausted(page: Any, flow_state: dict[str, Any]) -> bool:
    recoveries = int(flow_state.get("continue_recoveries") or 0)
    return recoveries >= 2 and _pay_continue_still_on_entry_page(page)


def _pay_continue_stuck_message(page: Any, flow_state: dict[str, Any]) -> str:
    try:
        cur = page.url
    except Exception:
        cur = str(flow_state.get("continue_last_url") or "")
    clicks = int(flow_state.get("continue_clicks") or 0)
    total_clicks = int(flow_state.get("continue_total_clicks") or 0)
    recoveries = int(flow_state.get("continue_recoveries") or 0)
    return f"Continue_To_Payment no progress after recoveries={recoveries} clicks={clicks} total_clicks={total_clicks} url={cur[:120]}"


def _pay_continue_still_on_entry_page(page: Any) -> bool:
    try:
        cur = page.url
    except Exception:
        cur = ""
    if "/checkoutweb/signup" in cur or "/webapps/hermes" in cur or "pay.openai.com" in cur or "chatgpt.com" in cur:
        return False
    return "paypal.com" in cur and ("/pay" in cur or "/agreements/approve" in cur)


def _recover_pay_after_continue_stall(page: Any, flow_state: dict[str, Any], log: LogFn | None, approve_url: str = "") -> None:
    recoveries = int(flow_state.get("continue_recoveries") or 0) + 1
    flow_state["continue_recoveries"] = recoveries
    flow_state["continue_clicked_at"] = 0
    flow_state["continue_clicks"] = 0
    flow_state["continue_total_clicks"] = int(flow_state.get("continue_total_clicks") or 0)
    if recoveries == 1 and _goto_paypal_onboard_redirect(page, flow_state, log, approve_url):
        return
    emit(log, f"paypal_http: /pay Continue_To_Payment no progress, refreshing current page recovery={recoveries}/2", level="warning")
    _hard_reload(page, approve_url, log)


def _recover_pay_entry_wait(page: Any, flow_state: dict[str, Any], log: LogFn | None, approve_url: str = "") -> None:
    recoveries = int(flow_state.get("entry_wait_recoveries") or 0) + 1
    flow_state["entry_wait_recoveries"] = recoveries
    flow_state["entry_wait_started_at"] = 0
    if recoveries == 1 and _goto_paypal_onboard_redirect(page, flow_state, log, approve_url):
        return
    emit(log, f"paypal_http: /pay entry page has no actionable controls, refreshing current page recovery={recoveries}/2", level="warning")
    _hard_reload(page, approve_url, log)


def _goto_paypal_onboard_redirect(page: Any, flow_state: dict[str, Any], log: LogFn | None, approve_url: str = "") -> bool:
    target = _build_paypal_onboard_redirect_url(page, flow_state, approve_url)
    if not target:
        return False
    try:
        emit(log, f"paypal_http: /pay Continue_To_Payment stalled; navigating ulOnboardRedirect {target[:120]}", level="warning")
        page.goto(target, wait_until="commit", timeout=20000)
        time.sleep(4)
        return True
    except Exception as exc:
        emit(log, f"paypal_http: /pay ulOnboardRedirect navigation failed: {str(exc)[:100]}", level="warning")
        return False


def _build_paypal_onboard_redirect_url(page: Any, flow_state: dict[str, Any], approve_url: str = "") -> str:
    try:
        cur = page.url
    except Exception:
        cur = ""
    ba_token = query_value(cur, "token") or query_value(cur, "ba_token") or query_value(approve_url, "ba_token")
    if not ba_token:
        return ""
    ssrt = query_value(cur, "ssrt") or query_value(approve_url, "ssrt")
    parsed = urlparse(cur or approve_url or "https://www.paypal.com")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.paypal.com"
    params: dict[str, list[str]] = {
        "ul": ["1"],
        "modxo_redirect_reason": ["guest_user"],
        "ulOnboardRedirect": ["true"],
        "ba_token": [ba_token],
        "locale.x": ["en_US"],
        "country.x": ["US"],
    }
    if ssrt:
        params["ssrt"] = [ssrt]
    ctx_id = query_value(cur, "ctxId") or str(flow_state.get("ctx_id") or "").strip()
    if ctx_id:
        params["ctxId"] = [ctx_id]
    return urlunparse((scheme, netloc, "/agreements/approve", "", urlencode(params, doseq=True), ""))


def _hard_reload(page: Any, approve_url: str, log: LogFn | None) -> None:
    """In-place reload via JS — bypasses Playwright's stuck-navigation wait.

    page.reload() waits for the current navigation to complete before reloading.
    If PayPal SPA is in a 'navigating but never arrives' state (Continue clicked,
    RSC request pending), page.reload() times out. JS reload doesn't wait.
    """
    try:
        page.evaluate("() => { window.location.reload(); }")
        emit(log, "paypal_http: /pay JS reload triggered")
        time.sleep(5)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        time.sleep(2)
        return
    except Exception as exc:
        emit(log, f"paypal_http: /pay JS reload failed: {str(exc)[:80]}")
    if approve_url:
        try:
            page.goto(approve_url, wait_until="commit", timeout=15000)
            time.sleep(5)
            return
        except Exception as exc:
            emit(log, f"paypal_http: /pay goto failed: {str(exc)[:80]}")
    time.sleep(3)


def _check_rsc_redirect(page: Any, log: LogFn | None) -> bool:
    """Detect raw RSC response with onboardingRedirectUrl and navigate to it."""
    try:
        result = page.evaluate("""() => {
            const text = document.body && document.body.innerText || '';
            if (!text.includes('onboardingRedirectUrl')) return '';
            const m = text.match(/"onboardingRedirectUrl":"(https?:\\/\\/[^"]+)"/);
            return m ? m[1] : '';
        }""")
        if result:
            emit(log, f"paypal_http: browser detected RSC redirect, navigating to onboardingRedirectUrl")
            page.goto(result, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            return True
    except Exception:
        pass
    return False


def _pay_page_tick(page: Any, log: LogFn | None, flow_state: dict[str, Any] | None = None) -> bool:
    """Single tick — detect state, do ONE action, return True if acted.

    Mirrors plugin's tick(): each call does at most one click/fill, then returns.
    The 5-second interval between ticks gives PayPal's SPA time to react.
    """
    flow_state = flow_state if flow_state is not None else {}
    state = page.evaluate("""() => {
        function canClick(btn) {
            if (!btn || btn.disabled) return false;
            const s = getComputedStyle(btn);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            const r = btn.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }

        // Priority 1: enter the modern /pay guest state machine with Pay_With_Card.
        const payWithCard = document.querySelector('button[data-atomic-wait-intent="Pay_With_Card"]')
            || document.querySelector('form input[name="formName"][value="createAccountAction"]')?.closest('form')?.querySelector('button[type="submit"]')
            || document.querySelector('form input[name="1_formName"][value="createAccountAction"]')?.closest('form')?.querySelector('button[type="submit"]');
        if (canClick(payWithCard)) return 'pay_with_card';

        // Priority 2: Email filled + Continue_To_Payment visible → click Continue.
        const emailSels = ['#onboardingFlowEmail', '#email', 'input[name="login_email"]', 'input[type="email"]'];
        let emailEl = null;
        for (const sel of emailSels) {
            const el = document.querySelector(sel);
            if (el && !el.disabled && el.getBoundingClientRect().width > 0) { emailEl = el; break; }
        }
        function findKeepPaying() {
            const forms = Array.from(document.querySelectorAll('form'));
            for (const form of forms) {
                const formName = form.querySelector('input[name="formName"], input[name="1_formName"]')?.value || '';
                const btn = form.querySelector('button[type="submit"]');
                if (/createAccount/i.test(formName) && canClick(btn)) return btn;
            }
            const buttons = Array.from(document.querySelectorAll('button'));
            return buttons.find(b => {
                const text = (b.innerText || b.textContent || '').replace(/\\s+/g, ' ').trim();
                const intent = b.getAttribute('data-atomic-wait-intent') || '';
                if (/cancel|back|log in|login/i.test(text)) return false;
                return /submit_email|continue_to_payment/i.test(intent) ||
                    /keep paying|continue to payment|continue|next|sign up|create an account/i.test(text);
            }) || document.querySelector('button[data-testid="continueButton"]');
        }
        if (emailEl && emailEl.value && emailEl.value.includes('@') && canClick(findKeepPaying())) return 'click_continue';

        // Priority 3: Email input visible but empty → fill it.
        if (emailEl) return 'fill_email';

        // Priority 4: fallback create-account buttons, only after atomic guest controls are absent.
        const onboardForm = document.querySelector('form[data-testid="xo-onboarding-form"] button[type="submit"]');
        if (canClick(onboardForm)) return 'create_account';
        const createBtn = Array.from(document.querySelectorAll('button'))
            .find(b => /create an account|create account|sign up/i.test((b.innerText||'').trim()));
        if (canClick(createBtn)) return 'create_account';

        // Priority 5: legacy unified-login fallback.
        const start = document.querySelector('#startOnboardingFlow');
        if (canClick(start)) return 'start_onboarding';

        const bodyText = (document.body && document.body.innerText || '');
        if (bodyText.includes('Open a PayPal account') || bodyText.includes('Already have an account')) return 'fill_email';

        return 'waiting';
    }""")

    if state == "pay_with_card":
        el = page.query_selector('button[data-atomic-wait-intent="Pay_With_Card"]')
        if not el or not el.is_visible():
            for sel in [
                'form:has(input[name="formName"][value="createAccountAction"]) button[type="submit"]',
                'form:has(input[name="1_formName"][value="createAccountAction"]) button[type="submit"]',
            ]:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    break
        if el and el.is_visible() and _pay_page_click(page, el, log, "Pay_With_Card"):
            flow_state["continue_clicks"] = 0
            flow_state["ctx_id"] = query_value(page.url, "ctxId") or flow_state.get("ctx_id") or ""
            emit(log, "paypal_http: /pay tick: clicked Pay_With_Card")
            time.sleep(1.5)
            return True
    elif state == "create_account":
        el = page.query_selector('form[data-testid="xo-onboarding-form"] button[type="submit"]')
        if not el or not el.is_visible():
            for text in ["Create an Account", "Create account"]:
                el = page.query_selector(f'button:has-text("{text}")')
                if el and el.is_visible():
                    break
        if el and el.is_visible():
            before_url = page.url
            if not _pay_page_click(page, el, log, "Create Account"):
                return False
            emit(log, "paypal_http: /pay tick: clicked Create an Account")
            for _ in range(4):
                time.sleep(0.5)
                try:
                    if page.url != before_url:
                        return True
                    btn = page.query_selector('form[data-testid="xo-onboarding-form"] button[type="submit"]')
                    if not btn or not btn.is_visible():
                        return True
                except Exception:
                    return True
            emit(log, "paypal_http: /pay tick: Create an Account not confirmed, retrying")
            return True
    elif state == "start_onboarding":
        clicks = int(flow_state.get("legacy_start_clicks") or 0)
        last_click = float(flow_state.get("legacy_start_last_click") or 0)
        if clicks >= 5 or time.time() - last_click < 6.5:
            return False
        el = page.query_selector('#startOnboardingFlow')
        if el and _pay_page_click(page, el, log, "startOnboardingFlow"):
            flow_state["legacy_start_clicks"] = clicks + 1
            flow_state["legacy_start_last_click"] = time.time()
            emit(log, "paypal_http: /pay tick: clicked #startOnboardingFlow")
            time.sleep(3)
            return True
    elif state == "fill_email":
        email = str(flow_state.get("email") or "") or _rand_email()
        flow_state["email"] = email
        for sel in ['#onboardingFlowEmail', '#email', 'input[name="login_email"]', 'input[type="email"]']:
            el = page.query_selector(sel)
            if el and el.is_visible():
                _human_type(page, el, email)
                emit(log, f"paypal_http: /pay tick: filled email={email}")
                return True
    elif state == "click_continue":
        btn = None
        for sel in [
            'button[data-atomic-wait-intent="Continue_To_Payment"]',
            'form:has(input[name="formName"][value="createAccount"]) button[type="submit"]',
            'form:has(input[name="1_formName"][value="createAccount"]) button[type="submit"]',
            'form[name="beginOnboardingFlow"] button[type="submit"]',
            '#onboardingFlow form button[type="submit"]',
            'button[data-testid="continueButton"]',
            'button[data-atomic-wait-intent*="Continue_To_Payment" i]',
            'button[data-atomic-wait-intent*="submit_email" i]',
            'button.actionContinue',
        ]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                btn = el
                break
        if not btn:
            for text in ["Continue to Payment", "Keep paying", "Continue", "Next"]:
                el = page.query_selector(f'button:has-text("{text}")')
                if el and el.is_visible():
                    btn = el
                    break
        if btn and _pay_page_click(page, btn, log, "Continue_To_Payment"):
            clicks = int(flow_state.get("continue_clicks") or 0) + 1
            total_clicks = int(flow_state.get("continue_total_clicks") or 0) + 1
            flow_state["continue_clicks"] = clicks
            flow_state["continue_total_clicks"] = total_clicks
            flow_state["continue_last_url"] = page.url
            flow_state["ctx_id"] = query_value(page.url, "ctxId") or flow_state.get("ctx_id") or ""
            flow_state["continue_clicked_at"] = time.time()
            emit(log, f"paypal_http: /pay tick: clicked Continue_To_Payment clicks={clicks} total={total_clicks}")
            time.sleep(2)
            if total_clicks >= 6:
                raise _PayContinueStuck(_pay_continue_stuck_message(page, flow_state))
            return True
    return False


class _PayContinueStuck(Exception):
    """Signals that the /pay Continue button click didn't trigger navigation."""
    pass


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
    return _fill_signup_form_fast(page, phone, password, address, log)


def _signup_field_values(phone: str, password: str, address: dict[str, str]) -> dict[str, str]:
    email = _rand_email()
    card = _generate_visa_card()
    return {
        "email": email,
        "phone": _normalize_phone(phone),
        "cardNumber": card["number"],
        "expiry": card["expiry"],
        "cvv": card["cvv"],
        "firstName": address.get("first_name") or "Tommy",
        "lastName": address.get("last_name") or "Jacobs",
        "street": address.get("line1") or "283 Clearview Drive",
        "city": address.get("city") or "Smyrna",
        "state": address.get("state") or "TN",
        "zip": address.get("postal_code") or "37167",
        "password": password,
    }


def _fill_signup_form_fast(
    page: Any,
    phone: str,
    password: str,
    address: dict[str, str],
    log: LogFn | None,
) -> dict[str, str]:
    emit(log, "paypal_http: browser filling signup form fast")

    if "/checkoutweb/signup" in page.url:
        _normalize_signup_guest_url(page, log)
        _remove_signup_fake_captcha_elements(page)

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
    time.sleep(3)

    if not _wait_for_signup_required_fields(page, timeout=15):
        emit(log, "paypal_http: browser signup fields not rendered after 15s", level="warning")
        return {}

    email = _rand_email()
    card = _generate_visa_card()
    first_name = address.get("first_name") or "Tommy"
    last_name = address.get("last_name") or "Jacobs"
    phone_norm = _normalize_phone(phone)

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
        _fill_field_safe_fast(page, name, value, log)

    _set_state(page, address.get("state") or "TN")

    _fill_field_safe_fast(page, "zip", address.get("postal_code") or "37167", log)
    _fill_field_safe_fast(page, "password", password, log)

    filled_fields = {
        "email": email,
        "phone": phone_norm,
        "cardNumber": card["number"],
        "expiry": card["expiry"],
        "cvv": card["cvv"],
        "firstName": first_name,
        "lastName": last_name,
        "street": address.get("line1") or "283 Clearview Drive",
        "city": address.get("city") or "Smyrna",
        "zip": address.get("postal_code") or "37167",
        "password": password,
    }
    missing = _verify_fields(page, list(filled_fields.items()), log)
    if missing:
        emit(log, f"paypal_http: browser signup fields missing after fast fill: {missing}", level="warning")
        return {}

    emit(log, f"paypal_http: browser form fast filled email={email} phone={phone_norm[:4]}...")
    return filled_fields


def _fill_signup_form_humanized(
    page: Any,
    phone: str,
    password: str,
    address: dict[str, str],
    log: LogFn | None,
) -> dict[str, str]:
    """Fill all fields on /checkoutweb/signup with React-safe value injection."""
    emit(log, "paypal_http: browser filling signup form")

    if "/checkoutweb/signup" in page.url:
        _normalize_signup_guest_url(page, log)
        _remove_signup_fake_captcha_elements(page)

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
    time.sleep(3)

    if not _wait_for_signup_required_fields(page, timeout=15):
        emit(log, "paypal_http: browser signup fields not rendered after 15s", level="warning")
        return {}

    email = _rand_email()
    card = _generate_visa_card()
    first_name = address.get("first_name") or "Tommy"
    last_name = address.get("last_name") or "Jacobs"
    phone_norm = _normalize_phone(phone)

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

    filled_fields = {
        "email": email,
        "phone": phone_norm,
        "cardNumber": card["number"],
        "expiry": card["expiry"],
        "cvv": card["cvv"],
        "firstName": first_name,
        "lastName": last_name,
        "street": address.get("line1") or "283 Clearview Drive",
        "city": address.get("city") or "Smyrna",
        "zip": address.get("postal_code") or "37167",
        "password": password,
    }
    missing = _verify_fields(page, list(filled_fields.items()), log)
    if missing:
        emit(log, f"paypal_http: browser signup fields missing after fill: {missing}", level="warning")
        return {}

    emit(log, f"paypal_http: browser form filled email={email} phone={phone_norm[:4]}...")
    return filled_fields


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


def _remove_signup_fake_captcha_elements(page: Any) -> None:
    _remove_paypal_fake_captcha_elements(page)


def _remove_paypal_fake_captcha_elements(page: Any) -> None:
    try:
        page.evaluate("""() => {
            if (window.__ppaf_fake_captcha_watcher) return;
            function allowedPage() {
                const path = location.pathname || '';
                if (path.includes('/auth/validatecaptcha') || path.includes('/checkoutweb/genericerror')) return false;
                return path.includes('/checkoutweb/signup') || path.includes('/webapps/hermes') || path.includes('/agreements/approve') || path.includes('/signin');
            }
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            function skipCaptcha() {
                if (!allowedPage()) return;
                const text = (document.body && document.body.innerText || '');
                if (/Security Challenge|security check|unusual activity|verify your identity|请验证|請驗證|人机验证|人機驗證/i.test(text)) return;
                const sels = [
                    '#captchaComponent', '.captcha-overlay', '.captcha-container',
                    '.appChallengeNS', '#g-anomalydetection-div',
                    'iframe[src*="recaptcha"]', 'iframe[title*="recaptcha" i]',
                    'div[id^="challenge"]',
                ];
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                }
                document.querySelectorAll('iframe').forEach(f => {
                    const hay = (f.src || '') + ' ' + (f.title || '') + ' ' + (f.id || '') + ' ' + (f.className || '');
                    if (/recaptcha|captcha|challenge/i.test(hay)) f.remove();
                });
                document.querySelectorAll('div').forEach(d => {
                    const cs = getComputedStyle(d);
                    const hay = (d.className || '') + ' ' + (d.id || '') + ' ' + (d.getAttribute('data-testid') || '');
                    if (cs.position === 'fixed' && /visible/i.test(cs.visibility) &&
                        parseInt(cs.zIndex || '0') > 1000000 &&
                        /captcha|challenge/i.test(hay)) {
                        d.remove();
                    }
                });
                document.documentElement.style.overflow = '';
                document.body.style.overflow = '';
            }
            skipCaptcha();
            let scheduled = false;
            function schedule() {
                if (scheduled) return;
                scheduled = true;
                setTimeout(() => { scheduled = false; skipCaptcha(); }, 800);
            }
            new MutationObserver(schedule).observe(
                document.body || document.documentElement,
                { childList: true, subtree: true }
            );
            setInterval(schedule, 3000);
            window.__ppaf_fake_captcha_watcher = true;
        }""")
    except Exception:
        pass


_LAST_CHALLENGE_MARKER_LOG_AT = 0.0
_LAST_HCAPTCHA_MARKER_LOG_AT = 0.0
_LAST_HIDDEN_HCAPTCHA_ARTIFACT_LOG_AT = 0.0
_LAST_PASSIVE_HCAPTCHA_CANDIDATE_LOG_AT = 0.0
_PAYPAL_CHALLENGE_FAIL_FAST_SECONDS = 20


def _install_paypal_challenge_watch(page: Any, log: LogFn | None) -> dict[str, Any]:
    state: dict[str, Any] = {"blocked": False, "message": ""}

    def _on_console(msg: Any) -> None:
        try:
            text = str(msg.text() if callable(getattr(msg, "text", None)) else getattr(msg, "text", "") or "")
        except Exception:
            text = ""
        if not text:
            return
        if re.search(r"event_name=slider_timeout|datadome.*?(blocked|denied|captcha_failed)|captcha_failed", text, re.I):
            state["blocked"] = True
            state["message"] = text[:300]
            emit(log, f"paypal_http: challenge console blocked signal: {text[:180]}", level="warning")

    try:
        page.on("console", _on_console)
    except Exception:
        pass
    return state


def _raise_if_paypal_challenge_blocked(page: Any, log: LogFn | None, challenge_state: dict[str, Any] | None = None) -> None:
    global _LAST_CHALLENGE_MARKER_LOG_AT
    if challenge_state and challenge_state.get("blocked"):
        raise PayPalHttpError("payment_proxy_rotation_required: PayPal challenge/DataDome blocked")
    try:
        cur = str(page.url or "")
    except Exception:
        cur = ""
    try:
        info = page.evaluate("""() => {
            const text = (document.body && document.body.innerText || '').slice(0, 2000);
            const html = document.documentElement ? document.documentElement.innerHTML.slice(0, 5000) : '';
            const combined = `${text}\n${html}`;
            const blocked = /event_name=slider_timeout|datadome.*?(blocked|denied|captcha_failed)|captcha_failed|captcha blocked/i.test(combined);
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            const captchaFrames = Array.from(document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="h-captcha"]')).filter(visible);
            const challengeContainers = Array.from(document.querySelectorAll('#captcha-standalone, .captcha-overlay, .captcha-container')).filter((el) => {
                if (!visible(el)) return false;
                const marker = `${el.id || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''} ${(el.innerText || el.textContent || '').slice(0, 500)}`;
                return /hcaptcha|h-captcha|recaptcha|g-recaptcha|security challenge|security check|verify you|人机验证|人機驗證/i.test(marker);
            });
            const textChallenge = /Security Challenge|security check|unusual activity|reCAPTCHA|hCaptcha|请验证|請驗證|人机验证|人機驗證/i.test(text);
            const visibleChallenge = Boolean(captchaFrames.length || (challengeContainers.length && textChallenge));
            return {blocked, visibleChallenge, textChallenge, text: text.replace(/\\s+/g, ' ').slice(0, 220)};
        }""")
    except Exception:
        return
    if not isinstance(info, dict):
        return
    if info.get("blocked"):
        raise PayPalHttpError("payment_proxy_rotation_required: PayPal challenge/DataDome blocked")
    if info.get("visibleChallenge") or info.get("textChallenge") or "/auth/validatecaptcha" in cur:
        now = time.time()
        if challenge_state is not None:
            challenge_state.setdefault("first_visible_at", now)
            challenge_state["last_url"] = cur
            challenge_state["message"] = str(info.get("text") or "")[:300]
        first_visible_at = float((challenge_state or {}).get("first_visible_at") or now)
        if now - _LAST_CHALLENGE_MARKER_LOG_AT > 15:
            _LAST_CHALLENGE_MARKER_LOG_AT = now
            emit(log, f"paypal_http: visible PayPal challenge marker, waiting briefly: {str(info.get('text') or '')[:180]}", level="warning")
        if "/auth/validatecaptcha" in cur or now - first_visible_at >= _PAYPAL_CHALLENGE_FAIL_FAST_SECONDS:
            raise PayPalHttpError(f"payment_proxy_rotation_required: PayPal security challenge did not resolve url={cur[:120]}")


def _log_hcaptcha_marker_without_sitekey(log: LogFn | None, url: str) -> None:
    global _LAST_HCAPTCHA_MARKER_LOG_AT
    now = time.time()
    if now - _LAST_HCAPTCHA_MARKER_LOG_AT > 30:
        _LAST_HCAPTCHA_MARKER_LOG_AT = now
        emit(log, f"paypal_http: visible hCaptcha marker without sitekey, waiting url={url[:80]}", level="warning")


def _log_hidden_hcaptcha_artifact(log: LogFn | None, url: str, count: int, passive_frames: int = 0) -> None:
    global _LAST_HIDDEN_HCAPTCHA_ARTIFACT_LOG_AT
    now = time.time()
    if now - _LAST_HIDDEN_HCAPTCHA_ARTIFACT_LOG_AT > 30:
        _LAST_HIDDEN_HCAPTCHA_ARTIFACT_LOG_AT = now
        emit(log, f"paypal_http: passive captcha artifacts ignored count={count} frames={passive_frames} url={url[:80]}")


def _log_passive_hcaptcha_candidate(log: LogFn | None, url: str, info: dict[str, Any]) -> None:
    global _LAST_PASSIVE_HCAPTCHA_CANDIDATE_LOG_AT
    now = time.time()
    if now - _LAST_PASSIVE_HCAPTCHA_CANDIDATE_LOG_AT > 30:
        _LAST_PASSIVE_HCAPTCHA_CANDIDATE_LOG_AT = now
        emit(log, f"paypal_http: hCaptcha candidate ignored interactive={bool(info.get('interactiveWidget'))} frames={info.get('visibleFrames') or 0} keyed={info.get('visibleKeyed') or 0} containers={info.get('challengeContainers') or 0} text={bool(info.get('visibleTextChallenge'))} url={url[:80]}")


def _solve_real_hcaptcha_if_present(page: Any, paypal_cfg: dict[str, Any], log: LogFn | None) -> bool:
    try:
        cur = str(page.url or "")
    except Exception:
        return False
    if "/checkoutweb/signup" in cur:
        return False
    try:
        info = page.evaluate(r"""() => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                const style = el.getAttribute('style') || '';
                return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0' && !/display\s*:\s*none|visibility\s*:\s*hidden/i.test(style);
            }
            function firstUuid(text) {
                const m = String(text || '').match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
                return m ? m[0] : '';
            }
            function sitekeyFromElement(el) {
                if (!el) return '';
                return el.getAttribute('data-sitekey') || el.getAttribute('data-hcaptcha-sitekey') || el.getAttribute('sitekey') || '';
            }
            function sitekeyFromFrame(frame) {
                const src = frame && frame.src || '';
                try {
                    const u = new URL(src);
                    const value = u.searchParams.get('sitekey') || u.searchParams.get('siteKey') || u.searchParams.get('websiteKey') || '';
                    if (value) return value;
                } catch (_) {}
                return firstUuid(src);
            }
            const allFrames = Array.from(document.querySelectorAll('iframe')).filter(f => /hcaptcha|h-captcha|recaptcha|captcha/i.test(f.src || ''));
            const visibleFrames = allFrames.filter(visible);
            const visibleInteractiveFrames = visibleFrames.filter(f => /hcaptcha\.com|newassets\.hcaptcha\.com|h-captcha|recaptcha|api2\/anchor|api2\/bframe|checkbox|challenge/i.test(f.src || ''));
            const visibleKeyed = Array.from(document.querySelectorAll('[data-sitekey], [data-hcaptcha-sitekey], [sitekey]')).filter(visible);
            const visibleInteractiveKeyed = visibleKeyed.filter(el => {
                const hay = `${el.id || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''} ${el.getAttribute('role') || ''} ${(el.innerText || el.textContent || '').slice(0, 300)}`;
                return /hcaptcha|h-captcha|recaptcha|g-recaptcha|checkbox|challenge|captcha|verify|verification/i.test(hay) || el.querySelector('iframe, textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
            });
            const responseTextareas = Array.from(document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'));
            const visibleTextareas = responseTextareas.filter(visible);
            const challengeContainers = Array.from(document.querySelectorAll('#captcha-standalone, .captcha-overlay, .captcha-container, [data-testid*="captcha"], [class*="captcha"]')).filter(el => {
                if (!visible(el)) return false;
                const hay = `${el.id || ''} ${el.className || ''} ${el.getAttribute('data-testid') || ''} ${(el.innerText || el.textContent || '').slice(0, 500)}`;
                return /hcaptcha|h-captcha|recaptcha|g-recaptcha|security challenge|security check|verify you|verification|captcha/i.test(hay);
            });
            const text = document.body && document.body.innerText || '';
            const visibleTextChallenge = /Security Challenge|security check|unusual activity|reCAPTCHA|hCaptcha|请验证|請驗證|人机验证|人機驗證/i.test(text);
            const onValidateCaptcha = location.href.includes('/auth/validatecaptcha');
            const interactiveWidget = Boolean(onValidateCaptcha || visibleTextareas.length || visibleInteractiveKeyed.length || visibleInteractiveFrames.length || (challengeContainers.length && visibleTextChallenge));
            let sitekey = '';
            for (const el of visibleInteractiveKeyed) {
                sitekey = sitekeyFromElement(el);
                if (sitekey) break;
            }
            if (!sitekey) {
                for (const frame of visibleInteractiveFrames) {
                    sitekey = sitekeyFromFrame(frame);
                    if (sitekey) break;
                }
            }
            const explicitChallenge = Boolean(interactiveWidget);
            if (!explicitChallenge) {
                const hiddenKeyed = Array.from(document.querySelectorAll('[data-sitekey], [data-hcaptcha-sitekey], [sitekey]')).length;
                return {hasCaptcha: false, blocking: false, passiveFrames: visibleFrames.length, hiddenArtifacts: hiddenKeyed + allFrames.length + challengeContainers.length + responseTextareas.length, sitekey: '', rqdata: '', visibleFrames: visibleFrames.length, visibleKeyed: visibleKeyed.length, visibleTextareas: visibleTextareas.length, challengeContainers: challengeContainers.length, visibleTextChallenge, interactiveWidget};
            }
            const blocking = true;
            if (!sitekey && blocking && onValidateCaptcha) {
                for (const el of Array.from(document.querySelectorAll('[data-sitekey], [data-hcaptcha-sitekey], [sitekey]'))) {
                    sitekey = sitekeyFromElement(el);
                    if (sitekey) break;
                }
            }
            if (!sitekey && blocking && onValidateCaptcha) {
                for (const frame of allFrames) {
                    sitekey = sitekeyFromFrame(frame);
                    if (sitekey) break;
                }
            }
            if (!sitekey && location.href.includes('/auth/validatecaptcha')) {
                const html = document.documentElement ? document.documentElement.innerHTML : '';
                const patterns = [
                    /["'](?:sitekey|siteKey|websiteKey)["']\s*[:=]\s*["']([^"']+)["']/i,
                    /data-sitekey=["']([^"']+)["']/i,
                    /hcaptcha[^"']+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i,
                ];
                for (const re of patterns) {
                    const m = html.match(re);
                    if (m) { sitekey = m[1]; break; }
                }
            }
            let rqdata = '';
            for (const frame of visibleFrames) {
                try {
                    const value = new URL(frame.src || '').searchParams.get('rqdata') || '';
                    if (value) { rqdata = value; break; }
                } catch (_) {}
            }
            if (!rqdata && location.href.includes('/auth/validatecaptcha')) {
                const html = document.documentElement ? document.documentElement.innerHTML : '';
                const m = html.match(/["']rqdata["']\s*[:=]\s*["']([^"']+)["']|data-rqdata=["']([^"']+)["']|[?&]rqdata=([^&#"']+)/i);
                if (m) rqdata = decodeURIComponent(m[1] || m[2] || m[3] || '');
            }
            return {hasCaptcha: true, blocking, sitekey, rqdata, visibleFrames: visibleFrames.length, interactiveFrames: visibleInteractiveFrames.length, visibleKeyed: visibleKeyed.length, interactiveKeyed: visibleInteractiveKeyed.length, visibleTextareas: visibleTextareas.length, challengeContainers: challengeContainers.length, visibleTextChallenge, interactiveWidget};
        }""")
    except Exception:
        return False
    if not isinstance(info, dict) or not info.get("hasCaptcha"):
        hidden = int(info.get("hiddenArtifacts") or 0) if isinstance(info, dict) else 0
        passive_frames = int(info.get("passiveFrames") or 0) if isinstance(info, dict) else 0
        if hidden or passive_frames:
            if isinstance(info, dict) and (info.get("visibleFrames") or info.get("visibleKeyed") or info.get("challengeContainers")):
                _log_passive_hcaptcha_candidate(log, cur, info)
            _log_hidden_hcaptcha_artifact(log, cur, hidden, passive_frames)
        return False
    site_key = str(info.get("sitekey") or "").strip()
    if not site_key:
        _log_hcaptcha_marker_without_sitekey(log, cur)
        if "/auth/validatecaptcha" in cur:
            raise PayPalHttpError(f"payment_proxy_rotation_required: PayPal hCaptcha challenge has no sitekey url={cur[:120]}")
        return False
    rqdata = str(info.get("rqdata") or "").strip()
    emit(log, f"paypal_http: browser solving real hCaptcha sitekey={bool(site_key)} rqdata={bool(rqdata)} frames={info.get('visibleFrames') or 0} interactive_frames={info.get('interactiveFrames') or 0} keyed={info.get('visibleKeyed') or 0} interactive_keyed={info.get('interactiveKeyed') or 0} containers={info.get('challengeContainers') or 0} interactive={bool(info.get('interactiveWidget'))} url={cur[:80]}")
    token = solve_paypal_hcaptcha(paypal_cfg, log, website_url=cur, site_key=site_key, rqdata=rqdata)
    if not token:
        raise PayPalHttpError("PayPal browser 需要 hCaptcha，但未得到验证码 token")
    submitted = page.evaluate("""(token) => {
        const names = ['h-captcha-response', 'g-recaptcha-response', 'hcaptcha_response', 'captcha_response'];
        for (const name of names) {
            let el = document.querySelector(`textarea[name="${name}"], input[name="${name}"]`);
            if (!el) {
                el = document.createElement(name.includes('response') ? 'textarea' : 'input');
                el.name = name;
                el.style.display = 'none';
                document.body.appendChild(el);
            }
            el.value = token;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        }
        window.hcaptchaToken = token;
        window.grecaptchaToken = token;
        const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]'));
        const btn = buttons.find(b => {
            const s = getComputedStyle(b);
            const r = b.getBoundingClientRect();
            if (b.disabled || s.display === 'none' || s.visibility === 'hidden' || r.width === 0 || r.height === 0) return false;
            const text = ((b.innerText || b.value || b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
            return /verify|continue|submit|next/i.test(text);
        });
        if (btn) { btn.click(); return true; }
        const form = document.querySelector('form');
        if (form) {
            if (typeof form.requestSubmit === 'function') form.requestSubmit();
            else form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
            return true;
        }
        return false;
    }""", token)
    emit(log, f"paypal_http: browser hCaptcha token injected submitted={bool(submitted)}")
    return True


def _wait_for_signup_required_fields(page: Any, timeout: int = 10) -> bool:
    """Wait until enough PayPal signup fields are visible before filling."""
    for _ in range(timeout * 2):
        info = _signup_required_field_info(page)
        if info.get("ready"):
            return True
        time.sleep(0.5)
    return False


def _signup_required_field_info(page: Any) -> dict[str, Any]:
    try:
        return page.evaluate("""() => {
            function visible(sel) {
                const el = document.querySelector(sel);
                if (!el || el.disabled) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            const checks = {
                email: visible('#email, input[name="email"], input[name="login_email"], input[type="email"]'),
                phone: visible('#phone, input[name="phone"], input[name="phoneNumber"], input[type="tel"]'),
                card: visible('#cardNumber, input[name="cardnumber"], input[name="cardNumber"]'),
                expiry: visible('#cardExpiry, input[name="exp-date"], input[name="cardExpiry"]'),
                cvv: visible('#cardCvv, input[name="cvv"], input[name="cardCvv"]'),
                firstName: visible('#firstName, input[name="fname"], input[name="firstName"]'),
                lastName: visible('#lastName, input[name="lname"], input[name="lastName"]'),
                street: visible('#billingLine1, input[name="billingLine1"]'),
                city: visible('#billingCity, input[name="billingCity"]'),
                state: visible('#billingState, select[name="billingState"]'),
                zip: visible('#billingPostalCode, input[name="billingPostalCode"]'),
                password: visible('#password, input[name="password"]'),
            };
            const present = Object.entries(checks).filter(([, ok]) => ok).map(([key]) => key);
            return {ready: present.length >= 11 && checks.email && checks.card && checks.password, present};
        }""") or {}
    except Exception:
        return {"ready": False, "present": []}


def _reload_signup_page(page: Any, log: LogFn | None, reason: str) -> None:
    info = _signup_required_field_info(page)
    emit(log, f"paypal_http: signup fields not ready, refreshing signup page: {reason}, present={info.get('present')}", level="warning")
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        emit(log, f"paypal_http: signup page reload failed: {str(exc)[:100]}", level="warning")
    time.sleep(3)
    if "/checkoutweb/signup" in page.url:
        _normalize_signup_guest_url(page, log)
        _remove_signup_fake_captcha_elements(page)


def _find_field(page: Any, name: str) -> Any:
    """Find a field using multi-strategy search matching plugin's findField."""
    cfg = _FIELD_SELECTORS.get(name)
    if not cfg:
        return None
    for sel in cfg.get("selectors", []):
        el = page.query_selector(sel)
        if el and el.is_visible() and el.is_enabled():
            return el
    for n in cfg.get("names", []):
        tag = cfg.get("tag", "input")
        el = page.query_selector(f'{tag}[name="{n}"]')
        if el and el.is_visible() and el.is_enabled():
            return el
        el = page.query_selector(f'#{n}')
        if el and el.is_visible() and el.is_enabled():
            return el
    for p in cfg.get("placeholders", []):
        el = page.query_selector(f'{cfg.get("tag", "input")}[placeholder*="{p}" i]')
        if el and el.is_visible() and el.is_enabled():
            return el
    for label_text in cfg.get("labels", []):
        el = page.query_selector(f'label:has-text("{label_text}") + input, label:has-text("{label_text}") input')
        if el and el.is_visible() and el.is_enabled():
            return el
    return None


def _fill_field_safe_fast(page: Any, name: str, value: str, log: LogFn | None) -> None:
    if not value:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        el = _find_field(page, name)
        if el:
            try:
                _instant_fill(page, el, value)
                time.sleep(0.05)
                return
            except Exception:
                time.sleep(0.2)
                continue
        time.sleep(0.2)
    emit(log, f"paypal_http: browser WARNING field '{name}' not found", level="warning")


def _fill_field_safe(page: Any, name: str, value: str, log: LogFn | None) -> None:
    """Find field, type value with human-like keystrokes. Retries on DOM detach."""
    if not value:
        return
    deadline = time.time() + 8
    while time.time() < deadline:
        el = _find_field(page, name)
        if el:
            try:
                _instant_fill(page, el, value)
                _human_delay(0.3, 0.8)
                return
            except Exception:
                time.sleep(1)
                continue
        time.sleep(0.5)
    emit(log, f"paypal_http: browser WARNING field '{name}' not found", level="warning")


def _wait_signup_frontend_settled(page: Any, log: LogFn | None) -> None:
    for _ in range(20):
        try:
            info = page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]'));
                const visible = buttons.filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                });
                const disabled = visible.filter(el => el.disabled || el.getAttribute('aria-disabled') === 'true').length;
                const busy = Boolean(document.querySelector('[aria-busy="true"], [data-testid*="spinner" i], [class*="spinner" i]'));
                return {visible: visible.length, disabled, busy};
            }""") or {}
            if not info.get("busy") and int(info.get("disabled") or 0) == 0:
                return
        except Exception:
            return
        time.sleep(0.25)
    emit(log, "paypal_http: signup frontend still settling before submit", level="warning")



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
    filled_fields: dict[str, str],
    address: dict[str, str],
    ba_token: str,
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
) -> bool:
    number_id = int(paypal_cfg.get("_number_id") or 0)
    job_id = int(paypal_cfg.get("_job_id") or 0)
    otp_baseline = ""
    if number_id:
        from backend.core.pools.paypal_number_pool import paypal_number_pool
        otp_baseline = paypal_number_pool.begin_otp_session(number_id, job_id=job_id)
    otp_state = _install_otp_graphql_watch(page, log)
    emit(log, "paypal_http: browser submitting signup form")
    _click_submit(page)

    checkpoint(check_cancelled)
    result = _wait_post_submit(page, timeout=60000)
    emit(log, f"paypal_http: browser post-submit state={result}")
    if result in ("hermes", "navigated"):
        return True
    if result == "card_error":
        raise PayPalHttpError("browser: card declined after signup submit")
    if result != "otp":
        emit(log, "paypal_http: browser post-submit timeout, will refresh signup page and refill", level="warning")
        return False

    checkpoint(check_cancelled)
    emit(log, "paypal_http: browser waiting for OTP inputs")
    input_count = _wait_for_otp_inputs(page, timeout=20)
    expected_length = input_count if input_count > 1 else 6
    emit(log, f"paypal_http: browser OTP detected inputs={input_count} expectedLength={expected_length}")

    if number_id:
        from backend.core.pools.paypal_number_pool import paypal_number_pool
        otp = paypal_number_pool.fetch_otp(
            number_id, expected_length=expected_length,
            timeout=int(paypal_cfg.get("otp_timeout") or 90),
            check_cancelled=check_cancelled,
            baseline_text=otp_baseline,
            job_id=job_id,
        )
    else:
        otp = _fetch_otp_with_length_check(
            paypal_cfg, smsurl, expected_length,
            timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log,
            check_cancelled=check_cancelled,
        )

    emit(log, "paypal_http: browser filling OTP")
    _fill_otp_cells(page, otp, log)

    emit(log, "paypal_http: browser OTP filled; waiting for auto-submit/navigation")
    if _wait_after_otp(page, log, otp_state):
        return True
    return _complete_signup_with_graphql_fallback(
        page, paypal_cfg, filled_fields, address, ba_token, otp, otp_state, log, check_cancelled
    )


def _wait_after_otp(page: Any, log: LogFn | None, otp_state: dict[str, Any] | None = None) -> bool:
    """After OTP fill, wait for navigation AWAY from /checkoutweb/signup via event-driven URL match."""
    emit(log, "paypal_http: browser waiting for OTP to process (URL change)...")
    deadline = time.time() + 30
    last_confirm = last_signup = 0
    while time.time() < deadline:
        if _is_post_signup_progress(page):
            try:
                emit(log, f"paypal_http: browser OTP processed, reached {page.url[:120]}")
            except Exception:
                emit(log, "paypal_http: browser OTP processed")
            return True
        if otp_state:
            confirm_seen = int(otp_state.get("confirm") or 0)
            signup_seen = int(otp_state.get("signup") or 0)
            if confirm_seen != last_confirm or signup_seen != last_signup:
                last_confirm, last_signup = confirm_seen, signup_seen
                emit(log, f"paypal_http: OTP graphql progress confirm={confirm_seen} signup={signup_seen}")
        time.sleep(0.5)
    try:
        cur = page.url
    except Exception:
        cur = "?"
    emit(log, f"paypal_http: browser OTP wait timed out (no navigation), url={cur[:120]}", level="warning")
    return False


def _install_otp_graphql_watch(page: Any, log: LogFn | None) -> dict[str, Any]:
    state: dict[str, Any] = {"initiate": 0, "confirm": 0, "signup": 0, "last_url": "", "initiate_data": None, "confirm_data": None, "signup_data": None}

    def _on_request(req: Any) -> None:
        try:
            url = str(req.url or "")
        except Exception:
            return
        if "InitiateRiskBasedTwoFactorPhoneConfirmationMutation" in url:
            state["initiate"] = int(state.get("initiate") or 0) + 1
            state["last_url"] = url
            emit(log, "paypal_http: observed OTP initiate GraphQL request")
        elif "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation" in url:
            state["confirm"] = int(state.get("confirm") or 0) + 1
            state["last_url"] = url
            emit(log, "paypal_http: observed OTP confirm GraphQL request")
        elif "SignUpNewMemberMutation" in url:
            state["signup"] = int(state.get("signup") or 0) + 1
            state["last_url"] = url
            emit(log, "paypal_http: observed signup GraphQL request after OTP")

    def _on_response(resp: Any) -> None:
        try:
            url = str(resp.url or "")
        except Exception:
            return
        key = ""
        if "InitiateRiskBasedTwoFactorPhoneConfirmationMutation" in url:
            key = "initiate_data"
        elif "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation" in url:
            key = "confirm_data"
        elif "SignUpNewMemberMutation" in url:
            key = "signup_data"
        if not key:
            return
        try:
            state[key] = resp.json()
        except Exception:
            return

    try:
        page.on("request", _on_request)
    except Exception:
        pass
    try:
        page.on("response", _on_response)
    except Exception:
        pass
    return state


def _complete_signup_with_graphql_fallback(
    page: Any,
    paypal_cfg: dict[str, Any],
    filled_fields: dict[str, str],
    address: dict[str, str],
    ba_token: str,
    otp: str,
    otp_state: dict[str, Any],
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
) -> bool:
    checkpoint(check_cancelled)
    if _is_post_signup_progress(page):
        return True
    emit(log, "paypal_http: browser OTP auto-submit stalled; using GraphQL signup fallback", level="warning")

    try:
        cur = str(page.url or "")
    except Exception:
        cur = ""
    ec_token = query_value(cur, "token") or first_match([r"(EC-[A-Z0-9-]{17,})"], cur)
    if not ec_token:
        raise PayPalHttpError("browser: GraphQL OTP fallback missing EC token")

    country = str(paypal_cfg.get("country") or address.get("country") or "US").upper()
    lang = str(paypal_cfg.get("lang") or "en")
    phone_country = str(paypal_cfg.get("phone_country") or country).upper()
    phone_country_code_value = str(paypal_cfg.get("phone_country_code") or phone_country_code(phone_country))
    phone_number = strip_phone_country_code(str(filled_fields.get("phone") or paypal_cfg.get("phone") or ""), phone_country_code_value)
    fallback_cfg = _paypal_signup_fallback_config(paypal_cfg, filled_fields, address, country)

    init_payload = otp_state.get("initiate_data")
    if not init_payload:
        emit(log, "paypal_http: fallback initiating phone confirmation again", level="warning")
        init_payload = _browser_graphql_checkoutweb(
            page,
            _initiate_phone_payload(ec_token, phone_number, phone_country, country, lang),
            referer=cur,
            ec_token=ec_token,
            country=country,
        )
    phone_state = _extract_phone_confirmation(init_payload, require_auth_ids=True)

    confirm_data = otp_state.get("confirm_data")
    if not confirm_data:
        confirm_data = _browser_graphql_checkoutweb(
            page,
            _confirm_phone_payload(ec_token, phone_state["authId"], phone_state["challengeId"], otp),
            referer=cur,
            ec_token=ec_token,
            country=country,
        )
    confirm_state = _extract_phone_confirmation(confirm_data, require_auth_ids=False)
    if confirm_state["state"].upper() != "CONFIRMED":
        raise PayPalHttpError(f"browser: GraphQL OTP fallback confirmation failed state={confirm_state['state']!r}")

    checkpoint(check_cancelled)
    signup_data = otp_state.get("signup_data")
    if not signup_data:
        signup_data = _browser_graphql_checkoutweb(
            page,
            _signup_payload(ec_token, fallback_cfg, phone_number, phone_country_code_value, country, str(filled_fields.get("email") or "")),
            referer=cur,
            ec_token=ec_token,
            country=country,
        )
    access_token = _extract_buyer_access_token(signup_data)
    emit(log, f"paypal_http: GraphQL signup fallback access_token={'present' if access_token else 'EMPTY'}")
    if not access_token:
        raise PayPalHttpError(f"payment_proxy_rotation_required: GraphQL signup fallback returned no EUAT: {_signup_response_summary(signup_data)}")
    _remember_paypal_fallback_euat(page, access_token, log)

    drop_url = _browser_checkoutweb_drop(page, cur, access_token, log)
    hermes_url = drop_url if "/webapps/hermes" in drop_url else _hermes_url(cur, ba_token, ec_token)
    emit(log, f"paypal_http: GraphQL signup fallback navigating Hermes {hermes_url[:120]}")
    page.goto(hermes_url, wait_until="domcontentloaded", timeout=60000)
    return _wait_for_post_signup_progress(page, timeout=20, log=log)



def _paypal_signup_fallback_config(
    paypal_cfg: dict[str, Any],
    filled_fields: dict[str, str],
    address: dict[str, str],
    country: str,
) -> dict[str, Any]:
    exp_month, exp_year = _split_expiry(str(filled_fields.get("expiry") or ""))
    return {
        **paypal_cfg,
        "signup_email": str(filled_fields.get("email") or paypal_cfg.get("signup_email") or ""),
        "signup_password": str(filled_fields.get("password") or paypal_cfg.get("signup_password") or paypal_cfg.get("password") or ""),
        "first_name": str(filled_fields.get("firstName") or address.get("first_name") or paypal_cfg.get("first_name") or ""),
        "last_name": str(filled_fields.get("lastName") or address.get("last_name") or paypal_cfg.get("last_name") or ""),
        "card": {
            "number": str(filled_fields.get("cardNumber") or ""),
            "exp_month": exp_month,
            "exp_year": exp_year,
            "cvv": str(filled_fields.get("cvv") or ""),
        },
        "card_number": str(filled_fields.get("cardNumber") or ""),
        "card_exp_month": exp_month,
        "card_exp_year": exp_year,
        "card_cvv": str(filled_fields.get("cvv") or ""),
        "billing": {
            "line1": str(filled_fields.get("street") or address.get("line1") or ""),
            "city": str(filled_fields.get("city") or address.get("city") or ""),
            "state": str(address.get("state") or paypal_cfg.get("billing_state") or "TN"),
            "postal_code": str(filled_fields.get("zip") or address.get("postal_code") or ""),
            "country": country,
        },
    }



def _split_expiry(expiry: str) -> tuple[str, str]:
    digits = re.findall(r"\d+", expiry or "")
    if len(digits) >= 2:
        month = digits[0].zfill(2)
        year = digits[1]
        if len(year) == 2:
            year = "20" + year
        return month, year
    return "", ""



def _browser_graphql_checkoutweb(page: Any, payload: Any, *, referer: str, ec_token: str, country: str) -> Any:
    op_name = getattr(payload, "get", lambda _k, _d="": "")("operationName", "")
    try:
        result = page.evaluate("""async ([payload, referer, ecToken, country]) => {
            const op = payload && !Array.isArray(payload) ? (payload.operationName || '') : '';
            const url = op ? `https://www.paypal.com/graphql?${encodeURIComponent(op)}` : 'https://www.paypal.com/graphql/';
            try {
                const resp = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    referrer: referer,
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'fetch',
                        'x-app-name': 'checkoutuinodeweb_weasley',
                        'paypal-client-context': ecToken,
                        'paypal-client-metadata-id': ecToken,
                        'x-country': country,
                        'x-locale': 'en_US',
                    },
                    body: JSON.stringify(payload),
                });
                const text = await resp.text();
                let parsed = null;
                try { parsed = JSON.parse(text); } catch (_) {}
                return {ok: resp.ok, status: resp.status, body: parsed, text: text.slice(0, 1000)};
            } catch (err) {
                return {ok:false, networkError:true, error:String(err && (err.message || err) || err), status:0, text:''};
            }
        }""", [payload, referer, ec_token, country])
    except Exception as exc:
        raise PayPalHttpError(f"payment_proxy_rotation_required: browser paypal graphql {op_name} evaluate failed: {str(exc)[:180]}") from exc
    if isinstance(result, dict) and result.get("networkError"):
        raise PayPalHttpError(f"payment_proxy_rotation_required: browser paypal graphql {op_name} network error: {str(result.get('error') or '')[:180]}")
    if not isinstance(result, dict) or not result.get("ok"):
        raise PayPalHttpError(f"browser paypal graphql {op_name} failed: {str(result)[:500]}")
    return result.get("body")



def _remember_paypal_fallback_euat(page: Any, access_token: str, log: LogFn | None) -> None:
    try:
        page.evaluate("""token => {
            try { sessionStorage.setItem('paypal_graphql_fallback_euat', token || ''); } catch (_) {}
        }""", access_token)
    except Exception as exc:
        emit(log, f"paypal_http: fallback EUAT remember failed: {str(exc)[:120]}", level="warning")



def _browser_checkoutweb_drop(page: Any, referer: str, access_token: str, log: LogFn | None) -> str:
    try:
        result = page.evaluate("""async ([referer, accessToken]) => {
            const headers = {'X-Requested-With': 'fetch'};
            if (accessToken) headers['x-paypal-internal-euat'] = accessToken;
            const resp = await fetch('https://www.paypal.com/checkoutweb/drop', {
                method: 'GET',
                credentials: 'include',
                redirect: 'follow',
                referrer: referer,
                headers,
            });
            return {ok: resp.ok, status: resp.status, url: resp.url || ''};
        }""", [referer, access_token])
    except Exception as exc:
        emit(log, f"paypal_http: checkoutweb/drop fallback fetch failed: {str(exc)[:120]}", level="warning")
        return ""
    if isinstance(result, dict):
        emit(log, f"paypal_http: checkoutweb/drop fallback status={result.get('status')} url={str(result.get('url') or '')[:120]}")
        return str(result.get("url") or "")
    return ""



def _is_post_signup_progress(page: Any) -> bool:
    try:
        cur = str(page.url or "").lower()
    except Exception:
        cur = ""
    if "/webapps/hermes" in cur or "pay.openai.com" in cur or "chatgpt.com" in cur or "/agreements/approve" in cur:
        return True
    try:
        title = str(page.title() or "").lower()
    except Exception:
        title = ""
    return "paypal checkout" in title and "review" in title


def _wait_for_post_signup_progress(page: Any, timeout: int, log: LogFn | None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_post_signup_progress(page):
            try:
                emit(log, f"paypal_http: browser reached checkout review url={page.url[:120]}")
            except Exception:
                emit(log, "paypal_http: browser reached checkout review")
            return True
        time.sleep(0.5)
    return False


def _click_submit(page: Any) -> None:
    for sel in [
        'button[data-testid="submit-button"]',
        'button[data-testid="hosted-payment-submit-button"]',
        'button.SubmitButton--complete',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                _human_click(page, el)
                return
        except Exception:
            continue
    for text in ["Next", "Continue", "Agree", "Pay", "Create an Account", "Create account"]:
        try:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn and btn.is_visible():
                _human_click(page, btn)
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
            err_text = page.evaluate("""() => {
                const sels = ['[role="alert"]', '[aria-live="assertive"]', '[aria-live="polite"]',
                    '[data-testid*="error" i]', '[id*="error" i]', '.notification-critical',
                    '.vx_alert-critical'];
                const seen = new Set();
                let collected = '';
                for (const sel of sels) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (seen.has(el)) continue;
                        seen.add(el);
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        if (r.width === 0 || r.height === 0 || s.display === 'none' || s.visibility === 'hidden') continue;
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (text.length >= 5) collected += text + ' ';
                    }
                }
                return collected.slice(0, 500);
            }""")
            if err_text:
                low = err_text.lower()
                if any(kw in low for kw in (
                    "weren't able to add this card", "wasn't able to add this card", "try a different card",
                    "declined", "not accepted", "try another", "invalid card", "card was declined",
                    "check all the details", "无法", "拒绝", "换一张",
                )):
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
    page.evaluate("""async ([digits]) => {
        const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
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
        async function fillCell(input, digit) {
            input.scrollIntoView({block:'center', inline:'center'});
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
            await sleep(250);
        }
        const root = document.querySelector('[data-testid="sca-confirm-multi-field"]') || document.getElementById('ciBasic');
        if (root) {
            const inputs = Array.from(root.querySelectorAll('#ciBasic input[name^="ciBasic-"], #ciBasic input[id^="ci-ciBasic-"], input[name^="ciBasic-"], input[id^="ci-ciBasic-"], input'))
                .filter(isSmsCodeInput)
                .sort((a,b) => {
                    const ai = Number((a.name||a.id||'').match(/(\\d+)$/)?.[1]||0);
                    const bi = Number((b.name||b.id||'').match(/(\\d+)$/)?.[1]||0);
                    return ai - bi;
                });
            if (inputs.length > 1) {
                const chars = digits.slice(0, inputs.length).split('');
                for (let i = 0; i < inputs.length && i < chars.length; i++) await fillCell(inputs[i], chars[i]);
                inputs[Math.min(chars.length, inputs.length)-1]?.dispatchEvent(new Event('blur', {bubbles:true}));
                await sleep(500);
                return;
            }
        }
        const sels = ['input[autocomplete="one-time-code"]', 'input[name*="otp" i]',
            'input[name*="security" i]', 'input[name*="verification" i]', 'input[id*="otp" i]',
            'input[id*="security" i]', 'input[id*="verification" i]', 'input[inputmode="numeric"]', 'input[type="tel"]'];
        for (const sel of sels) {
            const el = Array.from(document.querySelectorAll(sel)).find(isSmsCodeInput);
            if (el) {
                el.scrollIntoView({block:'center'});
                el.focus();
                setVal(el, digits);
                el.dispatchEvent(new InputEvent('input',{bubbles:true, inputType:'insertText', data:digits}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
                el.dispatchEvent(new Event('blur',{bubbles:true}));
                await sleep(500);
                return;
            }
        }
    }""", [digits])
    time.sleep(0.5)
    emit(log, f"paypal_http: browser OTP cells filled len={len(digits)}")


def _is_paypal_billing_fallback_url(url: str) -> bool:
    low = str(url or "").lower()
    return (
        "paypal.com" in low
        and (
            "/pay/billing" in low
            or ("/webapps/hermes" in low and any(token in low for token in ("fallback=1", "fromsignuplite=true", "billinglite=1")))
        )
    )


def _paypal_authorize_from_billing_runtime(page: Any, log: LogFn | None) -> bool:
    try:
        already_logged_missing = bool(page.evaluate("""() => {
            try { return sessionStorage.getItem('paypal_runtime_authorize_missing_logged') === '1'; } catch (_) { return false; }
        }"""))
    except Exception:
        already_logged_missing = False
    try:
        result = page.evaluate(r"""async () => {
            const html = document.documentElement ? document.documentElement.innerHTML : '';
            const text = html.replace(/&quot;/g, '"');
            function pick(patterns) {
                for (const src of patterns) {
                    const m = text.match(new RegExp(src, 'i')) || location.href.match(new RegExp(src, 'i'));
                    if (m && m[1]) return m[1];
                }
                return '';
            }
            const ecToken = pick([
                '\\\\"ecToken\\\\"\\\\s*:\\\\s*\\\\"(EC-[A-Z0-9-]+)\\\\"',
                '"ecToken"\\s*:\\s*"(EC-[A-Z0-9-]+)"',
                '[?&]token=(EC-[A-Z0-9-]+)'
            ]);
            const clientMetadataId = pick([
                '\\\\"clientMetadataId\\\\"\\\\s*:\\\\s*\\\\"([0-9a-f-]{36})\\\\"',
                '"clientMetadataId"\\s*:\\s*"([0-9a-f-]{36})"'
            ]);
            const euat = pick([
                '\\\\"x-paypal-internal-euat\\\\"\\\\s*:\\\\s*\\\\"([^"\\\\]+)\\\\"',
                '"x-paypal-internal-euat"\\s*:\\s*"([^"]+)"',
                '\\\\"accessToken\\\\"\\\\s*:\\\\s*\\\\"([^"\\\\]+)\\\\"',
                '"accessToken"\\s*:\\s*"([^"]+)"'
            ]) || (() => {
                try { return sessionStorage.getItem('paypal_graphql_fallback_euat') || ''; } catch (_) { return ''; }
            })();
            const nsid = pick([
                '\\\\"PayPal-Nsid\\\\"\\\\s*:\\\\s*\\\\"([^"\\\\]+)\\\\"',
                '"PayPal-Nsid"\\s*:\\s*"([^"]+)"'
            ]);
            if (!ecToken || !clientMetadataId || !euat) {
                try { sessionStorage.setItem('paypal_runtime_authorize_missing_logged', '1'); } catch (_) {}
                return {ok:false, error:'missing_runtime_fields', hasEc:!!ecToken, hasCmid:!!clientMetadataId, euatLen:euat.length};
            }
            const headers = {
                accept: '*/*',
                'content-type': 'application/json',
                'x-app-name': 'checkoutuinodeweb',
                'x-requested-with': 'fetch',
                'paypal-client-metadata-id': clientMetadataId,
                'x-paypal-internal-euat': euat,
            };
            if (nsid) headers['PayPal-Nsid'] = nsid;
            const query = `mutation authorize($billingAgreementId: String!, $addressId: String, $fundingPreference: billingFundingPreferenceInput, $legalAgreements: billingLegalAgreementsInput) {
  billing {
    authorize(
      billingAgreementId: $billingAgreementId
      addressId: $addressId
      fundingPreference: $fundingPreference
      legalAgreements: $legalAgreements
    ) {
      billingAgreementToken
      paymentAction
      returnURL { href __typename }
      buyer { userId __typename }
      __typename
    }
    __typename
  }
}`;
            const resp = await fetch('/graphql/', {
                method: 'POST',
                credentials: 'include',
                headers,
                body: JSON.stringify([{
                    operationName: 'authorize',
                    variables: {
                        billingAgreementId: ecToken,
                        fundingPreference: {balancePreference: 'OPT_OUT'},
                        legalAgreements: {},
                    },
                    query,
                }]),
            });
            const body = await resp.text();
            let parsed = null;
            try { parsed = JSON.parse(body); } catch (_) {}
            const returnUrl = Array.isArray(parsed) ? (parsed[0]?.data?.billing?.authorize?.returnURL?.href || '') : '';
            if (returnUrl) {
                try { location.href = returnUrl; } catch (_) {}
                return {ok:true, status:resp.status, returnUrl};
            }
            return {ok:false, status:resp.status, error:body.slice(0, 500)};
        }""")
    except Exception as exc:
        emit(log, f"paypal_http: billing runtime authorize exception: {str(exc)[:120]}", level="warning")
        return False
    if isinstance(result, dict) and result.get("ok"):
        emit(log, f"paypal_http: billing runtime authorize ok return={str(result.get('returnUrl') or '')[:120]}")
        return True
    if isinstance(result, dict):
        if result.get("error") != "missing_runtime_fields" or not already_logged_missing:
            emit(log, f"paypal_http: billing runtime authorize skipped/failed: {str(result)[:300]}", level="warning")
    return False


def _wait_for_stripe_return(
    page: Any,
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
    paypal_cfg: dict[str, Any] | None = None,
    challenge_state: dict[str, Any] | None = None,
) -> str:
    """Wait for hermes review → click Continue → wait for Stripe return URL."""
    emit(log, "paypal_http: browser waiting for hermes review / stripe return")
    wait_started_at = time.time()
    last_stuck_diag_at = 0.0
    last_rescue_refresh_at = 0.0
    rescue_refreshes = 0
    stripe_redirect_refreshes = 0
    last_stripe_redirect_url = ""
    last_click_signature = ""
    repeated_clicks = 0

    for _ in range(180):
        checkpoint(check_cancelled)
        try:
            cur = page.url
        except Exception as exc:
            if "has been closed" in str(exc) or "crashed" in str(exc) or "Target closed" in str(exc):
                raise PayPalHttpError(f"browser tab crashed: {str(exc)[:120]}")
            time.sleep(2)
            continue
        if "pay.openai.com" in cur or "chatgpt.com" in cur or "checkout.stripe.com" in cur:
            emit(log, f"paypal_http: browser reached stripe return: {cur[:100]}")
            return cur
        if _is_stripe_pm_redirect_success(cur):
            if cur != last_stripe_redirect_url:
                last_stripe_redirect_url = cur
                stripe_redirect_refreshes = 0
            if _stripe_pm_redirect_needs_retry(page) and stripe_redirect_refreshes < 3:
                stripe_redirect_refreshes += 1
                if _retry_stripe_pm_redirect(page, log, cur, stripe_redirect_refreshes):
                    time.sleep(5)
                    continue
            if stripe_redirect_refreshes >= 3:
                emit(log, f"paypal_http: stripe pm-redirect success reached but browser redirect failed after retries: {cur[:140]}", level="warning")
                return cur
        cur_lower = cur.lower()
        if "paypal.com/checkoutweb/genericerror" in cur_lower and "restricted_user" in cur_lower:
            raise PayPalHttpError(f"payment_proxy_rotation_required: paypal_number_restricted_user after otp url={cur[:160]}")
        if _is_paypal_checkout_signin_risk(cur):
            raise PayPalHttpError(f"payment_proxy_rotation_required: PayPal checkout redirected to signin after Hermes url={cur[:160]}")
        if "paypal.com" in cur and "/checkoutweb/signup" in cur:
            raise PayPalHttpError("browser: still on PayPal signup while waiting for Stripe return")
        try:
            if "paypal.com" in cur and "/checkoutweb/signup" not in cur:
                _remove_paypal_fake_captcha_elements(page)
                _raise_if_paypal_challenge_blocked(page, log, challenge_state)
                if _solve_real_hcaptcha_if_present(page, paypal_cfg or {}, log):
                    time.sleep(2)
                    continue
            _check_rsc_redirect(page, log)
            clicked_review_button = False
            for text in ["Agree and Continue", "Agree & Continue", "Agree", "Continue", "Confirm"]:
                btn = page.query_selector(f'button:has-text("{text}")')
                if btn and btn.is_visible():
                    page_name = cur.split('?')[0].split('/')[-1]
                    signature = f"{page_name}:{text}:{cur[:120]}"
                    if signature == last_click_signature:
                        repeated_clicks += 1
                    else:
                        last_click_signature = signature
                        repeated_clicks = 1
                    if repeated_clicks > 5:
                        _log_hermes_stuck_state(page, log, cur)
                        raise PayPalHttpError(f"payment_proxy_rotation_required: PayPal review button stuck text={text} page={page_name} clicks={repeated_clicks} url={cur[:120]}")
                    _human_click(page, btn)
                    emit(log, f"paypal_http: browser clicked '{text}' on {page_name} repeat={repeated_clicks}")
                    clicked_review_button = True
                    time.sleep(2)
                    break
            if clicked_review_button:
                continue
            if _is_paypal_billing_fallback_url(cur) and _paypal_authorize_from_billing_runtime(page, log):
                time.sleep(3)
                continue
        except Exception:
            pass
        now = time.time()
        if _hermes_refresh_rescue_allowed(cur, page) and rescue_refreshes < 2 and now - wait_started_at >= 25 and now - last_rescue_refresh_at >= 35:
            rescue_refreshes += 1
            last_rescue_refresh_at = now
            if _refresh_hermes_review_page(page, log, cur, rescue_refreshes):
                time.sleep(5)
                continue
        if now - wait_started_at >= 15 and now - last_stuck_diag_at >= 30:
            last_stuck_diag_at = now
            _log_hermes_stuck_state(page, log, cur)
        time.sleep(1)

    raise PayPalHttpError(f"browser: 等待 Stripe return URL 超时, url={page.url[:120]}")


def _is_paypal_checkout_signin_risk(cur: str) -> bool:
    try:
        parsed = urlparse(str(cur or ""))
    except Exception:
        return False
    if not parsed.netloc.lower().endswith("paypal.com"):
        return False
    if parsed.path.lower() != "/signin":
        return False
    params = parse_qs(parsed.query, keep_blank_values=True)
    return str((params.get("intent") or [""])[0]).lower() == "checkout"


def _is_stripe_pm_redirect_success(cur: str) -> bool:
    try:
        parsed = urlparse(str(cur or ""))
    except Exception:
        return False
    if parsed.netloc.lower() != "pm-redirects.stripe.com":
        return False
    if not parsed.path.startswith("/return/"):
        return False
    params = parse_qs(parsed.query, keep_blank_values=True)
    return str((params.get("status") or [""])[0]).lower() == "success"


def _stripe_pm_redirect_needs_retry(page: Any) -> bool:
    try:
        info = page.evaluate(r"""() => {
            const title = document.title || '';
            const text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
            const tryAgain = Array.from(document.querySelectorAll('button, input[type="submit"]')).some(el => /try again/i.test(el.innerText || el.value || el.textContent || ''));
            return {title, text: text.slice(0, 500), tryAgain};
        }""")
    except Exception:
        return True
    if not isinstance(info, dict):
        return True
    title = str(info.get("title") or "").lower()
    text = str(info.get("text") or "").lower()
    return bool(
        "problem loading page" in title
        or "secure connection failed" in text
        or "pr_end_of_file_error" in text
        or "try again" in text
        or info.get("tryAgain")
    )


def _retry_stripe_pm_redirect(page: Any, log: LogFn | None, cur: str, attempt: int) -> bool:
    try:
        emit(log, f"paypal_http: stripe pm-redirect success page failed; refreshing redirect retry={attempt}/3 url={cur[:140]}", level="warning")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        return True
    except Exception as exc:
        emit(log, f"paypal_http: stripe pm-redirect reload failed: {str(exc)[:100]}", level="warning")
        try:
            page.goto(cur, wait_until="domcontentloaded", timeout=30000)
            emit(log, f"paypal_http: stripe pm-redirect goto current url retry={attempt}/3")
            return True
        except Exception as goto_exc:
            emit(log, f"paypal_http: stripe pm-redirect goto failed: {str(goto_exc)[:100]}", level="warning")
            return False


def _hermes_refresh_rescue_allowed(cur: str, page: Any) -> bool:
    lower = str(cur or "").lower()
    if "paypal.com" not in lower:
        return False
    if any(token in lower for token in ("/checkoutweb/signup", "/checkoutweb/genericerror", "/auth/validatecaptcha")):
        return False
    if any(token in lower for token in ("pay.openai.com", "chatgpt.com", "checkout.stripe.com")):
        return False
    if not any(token in lower for token in ("/webapps/hermes", "/agreements/approve", "billinglite", "xoonboarding")):
        return False
    try:
        info = page.evaluate(r"""() => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            const text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
            const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]')).filter(visible).length;
            const interactiveChallenge = /Security Challenge|security check|unusual activity|verify your identity|请验证|請驗證|人机验证|人機驗證/i.test(text)
                || Array.from(document.querySelectorAll('#captcha-standalone, .captcha-overlay, .captcha-container')).some(visible);
            return {text: text.slice(0, 260), buttons, interactiveChallenge};
        }""")
    except Exception:
        return True
    if not isinstance(info, dict):
        return True
    text = str(info.get("text") or "")
    if info.get("interactiveChallenge"):
        return False
    if int(info.get("buttons") or 0) > 0:
        return False
    return "please enable js" in text.lower() or "disable any ad blocker" in text.lower() or not text


def _refresh_hermes_review_page(page: Any, log: LogFn | None, cur: str, attempt: int) -> bool:
    try:
        emit(log, f"paypal_http: hermes review stuck; refreshing page rescue={attempt}/2 url={cur[:120]}", level="warning")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        return True
    except Exception as exc:
        emit(log, f"paypal_http: hermes refresh failed: {str(exc)[:100]}", level="warning")
        try:
            page.goto(cur, wait_until="domcontentloaded", timeout=30000)
            emit(log, f"paypal_http: hermes goto current url rescue={attempt}/2")
            return True
        except Exception as goto_exc:
            emit(log, f"paypal_http: hermes goto current url failed: {str(goto_exc)[:100]}", level="warning")
            return False


def _log_hermes_stuck_state(page: Any, log: LogFn | None, cur: str) -> None:
    try:
        info = page.evaluate(r"""() => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 10 && r.height > 10 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]'))
                .filter(visible)
                .slice(0, 8)
                .map(el => ((el.innerText || el.value || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim().slice(0, 80))
                .filter(Boolean);
            const text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
            const frames = Array.from(document.querySelectorAll('iframe')).filter(f => /captcha|hcaptcha|recaptcha|challenge|auth/i.test(f.src || '') && visible(f)).length;
            const containers = Array.from(document.querySelectorAll('#captcha-standalone, .captcha-overlay, .captcha-container, [data-testid*="captcha"], [class*="captcha"], [class*="challenge"]')).filter(visible).length;
            const challengeText = /Security Challenge|security check|unusual activity|reCAPTCHA|hCaptcha|verification|verify your identity|请验证|請驗證|人机验证|人機驗證/i.test(text);
            return {
                title: document.title || '',
                buttons,
                frames,
                containers,
                challengeText,
                text: text.slice(0, 240),
            };
        }""")
    except Exception as exc:
        emit(log, f"paypal_http: hermes stuck diagnostic failed: {str(exc)[:120]}", level="warning")
        return
    if not isinstance(info, dict):
        return
    text = str(info.get("text") or "")
    if "restricted_user" in cur.lower() or "account is limited" in text.lower() or "your account is limited" in text.lower():
        raise PayPalHttpError(f"payment_proxy_rotation_required: paypal_number_restricted_user after otp url={cur[:160]}")
    emit(
        log,
        "paypal_http: hermes appears stuck; page diagnostics",
        level="warning",
        payload={
            "url": cur[:180],
            "title": str(info.get("title") or "")[:120],
            "buttons": info.get("buttons") or [],
            "captcha_frames": info.get("frames") or 0,
            "captcha_containers": info.get("containers") or 0,
            "challenge_text": bool(info.get("challengeText")),
            "text": str(info.get("text") or "")[:240],
        },
    )
