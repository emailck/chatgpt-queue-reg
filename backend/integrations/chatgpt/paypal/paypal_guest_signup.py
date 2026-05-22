from __future__ import annotations

import base64
import json
import random
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from .paypal_graphql import graphql_checkoutweb
import requests as std_requests

from .paypal_login import fetch_paypal_otp
from .runtime import (
    CheckCancelledFn,
    LogFn,
    PayPalHttpError,
    card_type,
    emit,
    find_key_recursive,
    first_match,
    USER_AGENT,
    gen_paypal_password,
    generate_fn_sync_data,
    luhn_check_digit,
    phone_country_code,
    query_value,
    short_random_id,
    strip_phone_country_code,
    utc_year,
)


SIGNUP_TERMS_CONTENT_ID = "US:en:f411614ea3eaac38abc54763fcfca00e:compliance.signupTerms"
PP_ORIGIN = "https://www.paypal.com"
PAYPAL_ONBOARDING_APP_ID = "CHECKOUTUINODEWEB_ONBOARDING_LITE"


def paypal_signup_url(http: Any, approve_url: str, ba_token: str, log: LogFn | None) -> str:
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
    emit(log, f"paypal_http: paypal guest approve redirect status={resp.status_code}")
    if location:
        return urljoin("https://www.paypal.com", location)
    return str(getattr(resp, "url", "") or url)


def paypal_guest_signup_authorize(
    http: Any,
    approve_url: str,
    approve_html: str,
    ba_token: str,
    paypal_cfg: dict[str, Any],
    log: LogFn | None,
    authorize_from_hermes_fn,
) -> dict[str, Any]:
    phone_raw = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if not phone_raw or not smsurl:
        raise PayPalHttpError("PayPal guest signup 缺少 phone/smsurl")
    phone_country = str(paypal_cfg.get("phone_country") or paypal_cfg.get("country") or "US").upper()
    phone_country_code_value = str(paypal_cfg.get("phone_country_code") or phone_country_code(phone_country))
    phone_number = strip_phone_country_code(phone_raw, phone_country_code_value)
    country = str(paypal_cfg.get("country") or "US").upper()
    lang = str(paypal_cfg.get("lang") or "en")
    runtime_ctx = paypal_cfg.get("_runtime") if isinstance(paypal_cfg.get("_runtime"), dict) else {}

    signup_url = paypal_signup_url(http, approve_url, ba_token, log)
    signup_resp = http.get(signup_url, allow_redirects=True, timeout=30)
    signup_url = str(signup_resp.url)
    html = signup_resp.text or approve_html
    ec_token = query_value(signup_url, "token") or first_match([r"(EC-[A-Z0-9]{17,})", r"(EC-[A-Z0-9-]{17,})"], html)
    if not ec_token:
        raise PayPalHttpError("PayPal checkoutweb signup 未返回 EC token")
    emit(log, f"paypal_http: paypal guest signup ec={bool(ec_token)}")

    ctx_id = first_match([r'"ctxId"\s*:\s*"([^"]+)"', r'"ctx_id"\s*:\s*"([^"]+)"'], html)

    graphql_checkoutweb(http, _deferred_feature_payload(ec_token, country), referer=signup_url, ec_token=ec_token, country=country, label="paypal DeferredFeature")
    graphql_checkoutweb(http, _griffin_metadata_payload(country, lang), referer=signup_url, ec_token=ec_token, country=country, label="paypal GriffinMetadataQuery")
    graphql_checkoutweb(http, _checkout_session_payload(ec_token), referer=signup_url, ec_token=ec_token, country=country, label="paypal CheckoutSessionDataQuery")

    signup_email = _signup_email(paypal_cfg)
    _otp_challenge_check(http, signup_url=signup_url, signup_html=html, email=signup_email, ec_token=ec_token, ctx_id=ctx_id, country=country, log=log)

    if runtime_ctx.get("paypal_address_autocomplete"):
        _paypal_address_autocomplete(http, signup_url, paypal_cfg, runtime_ctx, country, lang, ec_token)

    init_data = graphql_checkoutweb(
        http,
        _initiate_phone_payload(ec_token, phone_number, phone_country, country, lang),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    phone_state = _extract_phone_confirmation(init_data, require_auth_ids=True)
    otp = fetch_paypal_otp({**paypal_cfg, "otp_file": paypal_cfg.get("otp_file") or "", "smsurl": smsurl}, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("PayPal phone OTP 获取失败")
    confirm_data = graphql_checkoutweb(
        http,
        _confirm_phone_payload(ec_token, phone_state["authId"], phone_state["challengeId"], otp),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    confirm_state = _extract_phone_confirmation(confirm_data, require_auth_ids=False)
    if confirm_state["state"].upper() != "CONFIRMED":
        raise PayPalHttpError(f"PayPal phone confirmation 未通过 state={confirm_state['state']!r}: {confirm_data}")

    if not paypal_cfg.get("signup_password") and not paypal_cfg.get("guest_password") and not paypal_cfg.get("password"):
        paypal_cfg["signup_password"] = gen_paypal_password()
    signup_payload = _signup_payload(ec_token, paypal_cfg, phone_number, phone_country_code_value, country, signup_email)
    signup_data = graphql_checkoutweb(http, signup_payload, referer=signup_url, ec_token=ec_token, country=country, label="paypal SignUpNewMemberMutation")
    access_token = _extract_buyer_access_token(signup_data)
    cookie_count = 0
    try:
        cookie_count = len(list(http.cookies))
    except Exception:
        pass
    cookie_names = []
    try:
        cookie_names = [getattr(c, "name", str(c)) for c in http.cookies][:20]
    except Exception:
        pass
    signup_keys = ""
    try:
        inner = find_key_recursive(signup_data, "onboardAccount") or signup_data
        if isinstance(inner, dict):
            signup_keys = str(list(inner.keys())[:15])
    except Exception:
        pass
    emit(log, f"paypal_http: signup access_token={'present' if access_token else 'EMPTY'} cookies={cookie_count} names={cookie_names} response_keys={signup_keys}")
    if access_token:
        http.headers.update({"x-paypal-internal-euat": access_token})
    else:
        emit(log, "paypal_http: WARNING signup returned no accessToken — authorize may fail as ANONYMOUS", level="warning")

    drop_headers = {"Referer": signup_url, "X-Requested-With": "fetch"}
    if access_token:
        drop_headers["x-paypal-internal-euat"] = access_token
    drop_resp = http.get("https://www.paypal.com/checkoutweb/drop", headers=drop_headers, allow_redirects=True, timeout=30)
    hermes_url = str(drop_resp.url)
    if "/webapps/hermes" not in hermes_url:
        hermes_url = _hermes_url(signup_url, ba_token, ec_token)

    emit(log, "paypal_http: skipping HTTP authorize (non-browser sessions always ANONYMOUS), using browser")
    proxy_url = str(paypal_cfg.get("_proxy_url") or "")
    from .paypal_browser_authorize import browser_authorize_from_hermes

    http_cookies = []
    try:
        for c in http.cookies:
            http_cookies.append({
                "name": getattr(c, "name", ""),
                "value": getattr(c, "value", ""),
                "domain": getattr(c, "domain", ".paypal.com"),
                "path": getattr(c, "path", "/"),
            })
    except Exception:
        pass

    return browser_authorize_from_hermes(
        hermes_url=hermes_url,
        ba_token=ba_token,
        proxy_url=proxy_url,
        log=log,
        signup_email=signup_email,
        signup_password=str(paypal_cfg.get("signup_password") or paypal_cfg.get("guest_password") or paypal_cfg.get("password") or ""),
        http_cookies=http_cookies,
    )


def paypal_guest_signup_authorize_pure_protocol(
    http: Any,
    approve_url: str,
    approve_html: str,
    ba_token: str,
    paypal_cfg: dict[str, Any],
    log: LogFn | None,
    authorize_from_hermes_fn,
    check_cancelled: CheckCancelledFn | None = None,
) -> dict[str, Any]:
    phone_raw = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if not phone_raw:
        raise PayPalHttpError("PayPal pure_protocol 缺少 phone")
    if not ba_token:
        raise PayPalHttpError("PayPal pure_protocol 缺少 ba_token")

    phone_country = str(paypal_cfg.get("phone_country") or paypal_cfg.get("country") or "US").upper()
    phone_country_code_value = str(paypal_cfg.get("phone_country_code") or phone_country_code(phone_country))
    phone_number = strip_phone_country_code(phone_raw, phone_country_code_value)
    number_id = int(paypal_cfg.get("_number_id") or 0)
    country = str(paypal_cfg.get("country") or "US").upper()
    lang = str(paypal_cfg.get("lang") or "en")
    runtime_ctx = paypal_cfg.get("_runtime") if isinstance(paypal_cfg.get("_runtime"), dict) else {}

    signup_url, html, ec_token = _prime_checkout_signup_context(http, approve_url, approve_html, ba_token, country, lang, log)
    emit(log, f"paypal_http: pure_protocol signup ec={bool(ec_token)}")
    ctx_id = first_match([r'"ctxId"\s*:\s*"([^"]+)"', r'"ctx_id"\s*:\s*"([^"]+)"'], html)

    graphql_checkoutweb(http, _deferred_feature_payload(ec_token, country), referer=signup_url, ec_token=ec_token, country=country, label="paypal DeferredFeature")
    graphql_checkoutweb(http, _griffin_metadata_payload(country, lang), referer=signup_url, ec_token=ec_token, country=country, label="paypal GriffinMetadataQuery")
    graphql_checkoutweb(http, _checkout_session_payload(ec_token), referer=signup_url, ec_token=ec_token, country=country, label="paypal CheckoutSessionDataQuery")
    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_client_eligibility_check_success",
            "weasley_api_request_deferred_feature",
            "weasley_experiment_shouldShowOTP",
            "WEASLEY_PAGE_INTERACTIVE_FPTI",
            "WEASLEY_PREPARE_BILLING_PAGE_FPTI",
            "WEASLEY_IS_ADDRESSLESS_FPTI",
            "weasley_payment_request_api_available",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )
    _paypal_fraudnet_warmup(http, ec_token=ec_token, signup_url=signup_url, ba_token=ba_token, log=log)

    signup_email = _pure_protocol_signup_email(paypal_cfg)
    paypal_cfg["signup_email"] = signup_email
    _otp_challenge_check(http, signup_url=signup_url, signup_html=html, email=signup_email, ec_token=ec_token, ctx_id=ctx_id, country=country, log=log)

    if runtime_ctx.get("paypal_address_autocomplete"):
        _paypal_address_autocomplete(http, signup_url, paypal_cfg, runtime_ctx, country, lang, ec_token)

    sms_baseline = _sms_gateway_text(smsurl)
    if sms_baseline:
        emit(log, f"paypal_http: pure_protocol sms baseline captured length={len(sms_baseline)}")

    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_risk_based_phone_confirmation_modal_component_mounted",
            "weasley_initiate_phone_confirmation_start",
            "weasley_api_request_initiate_risk_based_two_factor_phone_confirmation_mutation",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )
    init_data = graphql_checkoutweb(
        http,
        _initiate_phone_payload(ec_token, phone_number, phone_country, country, lang),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    phone_state = _extract_phone_confirmation(init_data, require_auth_ids=True)
    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_api_response_status_200_initiate_risk_based_two_factor_phone_confirmation_mutation",
            "weasley_initiate_phone_confirmation_success",
            "weasley_phone_confirmation_interstitial_component_mounted",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )
    otp = _fetch_phone_otp(paypal_cfg, smsurl, number_id, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log, check_cancelled=check_cancelled, baseline_text=sms_baseline)
    if not otp:
        raise PayPalHttpError("PayPal phone OTP 获取失败")
    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_confirm_phone_confirmation_start",
            "weasley_api_request_confirm_risk_based_two_factor_phone_confirmation_mutation",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )
    confirm_data = graphql_checkoutweb(
        http,
        _confirm_phone_payload(ec_token, phone_state["authId"], phone_state["challengeId"], otp),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    confirm_state = _extract_phone_confirmation(confirm_data, require_auth_ids=False)
    if confirm_state["state"].upper() != "CONFIRMED":
        raise PayPalHttpError(f"PayPal phone confirmation 未通过 state={confirm_state['state']!r}: {confirm_data}")
    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_api_response_status_200_confirm_risk_based_two_factor_phone_confirmation_mutation",
            "weasley_confirm_phone_confirmation_success",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )

    if not paypal_cfg.get("signup_password") and not paypal_cfg.get("guest_password") and not paypal_cfg.get("password"):
        paypal_cfg["signup_password"] = gen_paypal_password()
    signup_payload = _signup_payload(ec_token, paypal_cfg, phone_number, phone_country_code_value, country, signup_email, signup_html=html, lang=lang)
    signup_payload["fn_sync_data"] = _paypal_fn_sync_data(ec_token)
    _paypal_fraudnet_field_events(
        http,
        ec_token=ec_token,
        field_ids=[
            "email",
            "phone",
            "cardNumber",
            "cardExpiry",
            "cardCvv",
            "password",
            "firstName",
            "lastName",
            "billingLine1",
            "billingCity",
            "billingPostalCode",
            "billingState",
        ],
        log=log,
    )
    _paypal_weasley_log(
        http,
        ec_token=ec_token,
        signup_url=signup_url,
        event_names=[
            "weasley_create_account_and_pay_submit",
            "weasley_api_request_sign_up_new_member_mutation",
        ],
        locale_country=country,
        locale_lang=lang,
        log=log,
    )
    signup_data = graphql_checkoutweb(http, signup_payload, referer=signup_url, ec_token=ec_token, country=country, label="paypal SignUpNewMemberMutation")
    access_token = _extract_buyer_access_token(signup_data)
    signup_error = _first_signup_error(signup_data)
    emit(log, f"paypal_http: pure_protocol signup access_token={'present' if access_token else 'EMPTY'} summary={_signup_response_summary(signup_data)}")
    if not access_token:
        raise PayPalHttpError(_pure_protocol_access_token_error(signup_data))
    if signup_error:
        emit(log, f"paypal_http: pure_protocol signup partial error; continue billingLite fallback summary={_signup_response_summary(signup_data)}", level="warning")
    http.headers.update({"x-paypal-internal-euat": access_token})

    drop_resp = http.get(
        "https://www.paypal.com/checkoutweb/drop",
        headers={"Referer": signup_url, "X-Requested-With": "fetch", "x-paypal-internal-euat": access_token},
        allow_redirects=True,
        timeout=30,
    )
    hermes_url = str(drop_resp.url)
    emit(log, f"paypal_http: pure_protocol checkoutweb/drop status={drop_resp.status_code}")
    if "/webapps/hermes" not in hermes_url:
        hermes_url = _hermes_url(signup_url, ba_token, ec_token, reason=_signup_error_reason(signup_error) if signup_error else "")

    emit(log, f"paypal_http: pure_protocol hermes authorize {hermes_url[:120]}")
    return authorize_from_hermes_fn(http, hermes_url, ba_token, log)


def _fetch_phone_otp(
    paypal_cfg: dict[str, Any],
    smsurl: str,
    number_id: int,
    *,
    timeout: int,
    log: LogFn | None,
    check_cancelled: CheckCancelledFn | None = None,
    baseline_text: str = "",
) -> str:
    if number_id:
        from backend.core.pools.paypal_number_pool import paypal_number_pool
        return paypal_number_pool.fetch_otp(number_id, expected_length=6, timeout=timeout, check_cancelled=check_cancelled, baseline_text=baseline_text, job_id=int(paypal_cfg.get("_job_id") or 0))
    return _poll_smsurl_otp(smsurl, timeout=timeout, baseline_text=baseline_text, log=log) or fetch_paypal_otp({**paypal_cfg, "otp_file": paypal_cfg.get("otp_file") or "", "smsurl": ""}, timeout=timeout, log=log)


def _sms_gateway_text(smsurl: str) -> str:
    if not smsurl:
        return ""
    try:
        resp = std_requests.get(smsurl, timeout=15)
        text = resp.text or ""
        try:
            payload = resp.json()
            text += " " + json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass
        return text.strip()
    except Exception:
        return ""


def _poll_smsurl_otp(smsurl: str, *, timeout: int, baseline_text: str, log: LogFn | None) -> str:
    if not smsurl:
        return ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        text = _sms_gateway_text(smsurl)
        if baseline_text and text == baseline_text.strip():
            time.sleep(3)
            continue
        parts = text.split("|", 2)
        if len(parts) >= 2 and parts[0].strip().lower() == "no":
            time.sleep(3)
            continue
        search_text = parts[1] if len(parts) >= 2 else text
        for match in re.finditer(r"\b(\d{4,8})\b", search_text):
            token = match.group(1)
            if len(token) == 6:
                return token
        time.sleep(3)
    emit(log, "paypal_http: smsurl otp poll timed out", level="warning")
    return ""


def _prime_checkout_signup_context(
    http: Any,
    approve_url: str,
    approve_html: str,
    ba_token: str,
    country: str,
    lang: str,
    log: LogFn | None,
) -> tuple[str, str, str]:
    redirect_url = paypal_signup_url(http, approve_url, ba_token, log)
    redirect_resp = http.get(redirect_url, allow_redirects=True, timeout=30)
    redirect_html = redirect_resp.text or approve_html
    ec_token = query_value(str(redirect_resp.url), "token") or query_value(redirect_url, "token") or first_match([r"(EC-[A-Z0-9]{17,})", r"(EC-[A-Z0-9-]{17,})"], redirect_html)
    if not ec_token:
        raise PayPalHttpError("PayPal checkoutweb signup 未返回 EC token")

    signup_url = _canonical_signup_url(str(redirect_resp.url), ba_token, ec_token, country, lang)
    if str(redirect_resp.url) != signup_url:
        signup_resp = http.get(signup_url, allow_redirects=False, timeout=30)
        signup_html = signup_resp.text or redirect_html
        emit(log, f"paypal_http: pure_protocol canonical signup status={signup_resp.status_code}")
    else:
        signup_html = redirect_html
    return signup_url, signup_html, ec_token


def _canonical_signup_url(source_url: str, ba_token: str, ec_token: str, country: str, lang: str) -> str:
    parsed = urlparse(source_url)
    q = parse_qs(parsed.query)
    ssrt = q.get("ssrt", [str(int(time.time() * 1000))])[-1]
    return "https://www.paypal.com/checkoutweb/signup?" + urlencode({
        "ssrt": ssrt,
        "ul": "1",
        "modxo_redirect_reason": "guest_user",
        "ba_token": ba_token,
        "locale.x": f"{lang}_{country}",
        "country.x": country,
        "token": ec_token,
        "rcache": "1",
        "cookieBannerVariant": "hidden",
    })


def _signup_response_summary(signup_data: Any) -> str:
    errors = signup_data.get("errors") if isinstance(signup_data, dict) else None
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else errors
        if isinstance(first, dict):
            return str({k: first.get(k) for k in ("message", "name", "code", "path") if first.get(k)})[:500]
        return str(first)[:500]
    onboard = find_key_recursive(signup_data, "onboardAccount")
    if isinstance(onboard, dict):
        return str(list(onboard.keys())[:12])
    return str(signup_data)[:500]


def _pure_protocol_access_token_error(signup_data: Any) -> str:
    text = str(signup_data)[:1200]
    lowered = text.lower()
    if "create_card_account_candidate_validation_error" in lowered or "validate.fi" in lowered:
        return f"paypal_create_card_account_validation_error: PayPal pure_protocol 当前 persona/card 候选被 validate.fi 拒绝: {text}"
    if "authchallenge" in lowered or "captcha" in lowered or "recaptcha" in lowered:
        return "PayPal pure_protocol signup 触发 authchallenge/captcha，未获取 EUAT"
    if "oas" in lowered or "createmember" in lowered:
        return "payment_proxy_rotation_required: PayPal pure_protocol signup OAS/createMember 未获取 EUAT"
    return f"PayPal pure_protocol signup 未返回 EUAT: {text}"


def _paypal_fn_sync_data(ec_token: str, *, source: str = "IWC_LOGIN_APP", include_d: bool = True) -> str:
    now_ms = int(time.time() * 1000)
    screen = {
        "screen": {
            "colorDepth": 24,
            "pixelDepth": 24,
            "height": 900,
            "width": 1440,
            "availHeight": 820,
            "availWidth": 1440,
        },
        "ua": USER_AGENT,
    }
    ts2_parts = [
        ("Di0", random.randint(12_000, 24_000)),
        ("Di1", random.randint(5, 18)),
        ("Di2", random.randint(80, 180)),
        ("Ui0", 24),
        ("Ui1", random.randint(40, 80)),
        ("Ui2", random.randint(45, 95)),
        ("Di3", random.randint(2_000, 5_000)),
        ("Di4", 24),
        ("Di5", random.randint(60, 140)),
        ("Uh", random.randint(2_500, 5_500)),
    ]
    rdt_chunks = []
    base = random.randint(18_000, 56_000)
    for _ in range(20):
        a = max(1000, base + random.randint(-28_000, 28_000))
        b = a + random.randint(-250, 250)
        c = max(1000, a - random.randint(250, 700))
        rdt_chunks.append(f"{a},{b},{c}")
    payload: dict[str, Any] = {
        "SC_VERSION": "2.0.4",
        "syncStatus": "data",
        "f": ec_token,
        "s": source,
        "chk": {
            "ts": now_ms,
            "eteid": [
                random.randint(-12_000_000_000, -1_000_000_000),
                random.randint(1_000_000_000, 9_000_000_000),
                random.randint(1_000_000_000, 9_000_000_000),
                random.randint(-12_000_000_000, -1_000_000_000),
                random.randint(1_000_000_000, 9_000_000_000),
                random.randint(1_000_000_000, 9_000_000_000),
                None,
                None,
            ],
            "tts": random.randint(20, 80),
        },
        "dc": json.dumps(screen, separators=(",", ":")),
        "wv": False,
        "web_integration_type": "WEB_REDIRECT",
        "cookie_enabled": True,
    }
    if include_d:
        payload["d"] = {
            "ts2": "".join(f"{key}:{value}" for key, value in ts2_parts),
            "rDT": ":".join(rdt_chunks) + f":{random.randint(8_000, 28_000)},{random.randint(20, 80)}",
        }
    return quote(json.dumps(payload, separators=(",", ":")))


def _risk_headers(*, referer: str = "https://www.paypal.com/", same_site: bool = True, content_type: str = "application/json") -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": PP_ORIGIN,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-CH-UA": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "Sec-CH-UA-Full-Version-List": '"Chromium";v="146.0.7680.154", "Not-A.Brand";v="24.0.0.0", "Google Chrome";v="146.0.7680.154"',
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-CH-UA-Model": '""',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Arch": '"x86"',
        "Sec-CH-Device-Memory": "8",
        "Sec-Fetch-Site": "same-site" if same_site else "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _session_cookie_dict(http: Any) -> dict[str, str]:
    jar = getattr(http, "cookies", None)
    if jar is None:
        return {}
    try:
        return {str(k): str(v) for k, v in (jar.get_dict() or {}).items()}
    except Exception:
        pass
    out: dict[str, str] = {}
    try:
        for cookie in jar:
            name = getattr(cookie, "name", "")
            value = getattr(cookie, "value", "")
            if name:
                out[str(name)] = str(value)
    except Exception:
        pass
    return out


def _risk_cookie_value(http: Any, *preferred_names: str, fallback_len: int = 96) -> str:
    cookies = _session_cookie_dict(http)
    for name in preferred_names:
        value = cookies.get(name)
        if value:
            return value
    for name, value in cookies.items():
        if name in {"KHcl0EuY7AKSMgfvHl7J5E7hPtK", "sc_f", "ddi"}:
            continue
        value = str(value or "")
        if len(value) >= 60 and re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return value
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    return "".join(random.choices(alphabet, k=fallback_len))


def _browser_env_payload(*, page_url: str, referer: str) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    return {
        "connectionData": {"effectiveType": "4g", "rtt": "50", "downlink": "10"},
        "navigator": {
            "appName": "Netscape",
            "appVersion": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "cookieEnabled": True,
            "language": "en-US",
            "onLine": True,
            "platform": "Win32",
            "product": "Gecko",
            "productSub": "20030107",
            "userAgent": USER_AGENT,
            "vendor": "Google Inc.",
            "vendorSub": "",
        },
        "screen": {"colorDepth": 24, "pixelDepth": 24, "height": 900, "width": 1440, "availHeight": 820, "availWidth": 1440},
        "window": {"outerHeight": 821, "outerWidth": 1440, "innerHeight": 734, "innerWidth": 1440, "devicePixelRatio": 2},
        "referer": referer,
        "URL": page_url,
        "rvr": "3.14.0-FP",
        "tnt": "PP",
        "activeXDefined": False,
        "flashVersion": {"major": 0, "minor": 0, "release": 0},
        "tz": 28800000,
        "tzName": "Asia/Shanghai",
        "dst": True,
        "wit": 2,
        "time": now_ms,
    }


def _paypal_fraudnet_warmup(http: Any, *, ec_token: str, signup_url: str, ba_token: str, log: LogFn | None) -> None:
    pay_referer = f"{PP_ORIGIN}/pay?token={ba_token}&ul=1" if ba_token else signup_url
    headers = _risk_headers(referer="https://www.paypal.com/")
    get_headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    try:
        resp = http.get(
            f"https://c6.paypal.com/v1/r/d/b/p3?f={quote(ec_token)}&s={quote(PAYPAL_ONBOARDING_APP_ID)}",
            headers=get_headers,
            timeout=20,
        )
        emit(log, f"paypal_http: pure_protocol fraudnet p3 status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol fraudnet p3 skipped: {exc}", level="warning")

    ddi = _risk_cookie_value(http, "ddi", fallback_len=120)
    vf = _risk_cookie_value(http, "KHcl0EuY7AKSMgfvHl7J5E7hPtK", fallback_len=96)
    sc = _risk_cookie_value(http, "sc_f", fallback_len=96)
    p1_payload = {
        **_browser_env_payload(page_url=signup_url, referer=pay_referer),
        "trt": False,
        "lst": {"ddiLst": True, "ddi": ddi, "v": None, "vf": vf},
        "pt1": {"i": "NaN", "pp1": f"{random.randint(4, 12)}.00", "cd1": "1.00", "tb": 1, "sf": "0000", "ph1": f"{random.randint(7000, 14000)}.00"},
        "asynchk": {"ph2": "".join(random.choices("0123456789abcdef", k=64)), "o": ["ua", "colorDepth", "width", "tz", "platform", "plugins"]},
        "hlb": {"wd": True, "chromeWSRT": "n/a", "plgSize": 5, "lgSize": 2, "rtt": 50},
        "pkc": {"uvpa": 2, "cma": 1, "cc": 3, "ht": 3, "pkp": 3},
    }
    try:
        resp = http.post(
            "https://c.paypal.com/v1/r/d/b/p1",
            json={"appId": PAYPAL_ONBOARDING_APP_ID, "correlationId": ec_token, "payload": p1_payload},
            headers=headers,
            timeout=20,
        )
        emit(log, f"paypal_http: pure_protocol fraudnet p1 status={resp.status_code}")
        try:
            data = resp.json() or {}
            if isinstance(data, dict):
                sc = data.get("sc") or sc
        except Exception:
            pass
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol fraudnet p1 skipped: {exc}", level="warning")

    p2_payload = {
        "URL": signup_url,
        "tnt": "PP",
        "data": {
            "plugins": [
                {"mT": [{"t": "application/pdf", "s": "pdf"}, {"t": "text/pdf", "s": "pdf"}], "n": name, "v": "", "fn": "internal-pdf-viewer", "d": "Portable Document Format"}
                for name in ["Chrome PDF Viewer", "Chromium PDF Viewer", "Microsoft Edge PDF Viewer", "PDF Viewer", "WebKit built-in PDF"]
            ],
            "cv": {"h": "//GlaGjwAAAAZJREFUAwCRmNE2FwdlIAAAAABJRU5ErkJggg==", "f": 1, "t": "4.00"},
            "vm": {
                "cores": 16,
                "gpu": {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 650 Ti Direct3D11 vs_5_0 ps_5_0, D3D11-30.0.14.7414)"},
                "jsMem": {"usedJSHeapSize": random.randint(35_000_000, 80_000_000), "totalJSHeapSize": random.randint(90_000_000, 140_000_000), "jsHeapSizeLimit": 4_294_967_296},
                "perfNav": {"navigationStart": int(time.time() * 1000) - random.randint(2500, 6500)},
            },
            "fts": int(time.time() * 1000),
        },
        "sc": {"httpCookie": sc, "sc-lst": sc},
        "pvc": 0,
        "pt2": {"pp2": "5.00", "cd2": "1.00", "cp": 1},
    }
    try:
        resp = http.post(
            "https://c.paypal.com/v1/r/d/b/p2",
            json={"appId": PAYPAL_ONBOARDING_APP_ID, "correlationId": ec_token, "payload": p2_payload},
            headers=headers,
            timeout=20,
        )
        emit(log, f"paypal_http: pure_protocol fraudnet p2 status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol fraudnet p2 skipped: {exc}", level="warning")

    try:
        resp = http.post(
            "https://c.paypal.com/v1/r/d/b/w",
            json={
                "appId": PAYPAL_ONBOARDING_APP_ID,
                "correlationId": ec_token,
                "payload": {"pkc": {"uvpa": 2, "cma": 1, "cc": 3, "ht": 3, "pkp": 3}, "slt": random.randint(25, 450), "uvpat": random.randint(25, 450), "cmat": random.randint(25, 450), "capt": 0},
            },
            headers=headers,
            timeout=20,
        )
        emit(log, f"paypal_http: pure_protocol fraudnet w status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol fraudnet w skipped: {exc}", level="warning")


def _paypal_fraudnet_field_events(http: Any, *, ec_token: str, field_ids: list[str], log: LogFn | None) -> None:
    headers = _risk_headers(referer="https://www.paypal.com/", content_type="")
    elapsed = random.randint(700, 1400)
    for field_id in field_ids:
        if field_id in {"password", "cardCvv"}:
            ts = f"Di0:{elapsed}Di1:{random.randint(7, 45)}Di2:{random.randint(80, 420)}Ui0:{random.randint(20, 45)}Ui1:{random.randint(45, 120)}Uh:{random.randint(1200, 6500)}"
        elif field_id == "cardNumber":
            ts = f"Dk91:{elapsed}Di0:{random.randint(120, 320)}Uk91:{random.randint(80, 180)}Uh:{random.randint(1200, 2200)}"
        else:
            ts = f"Dk000:{elapsed}Uk000:{random.randint(4, 13)}Uh:{random.randint(850, 1300)}"
        payload = {"tsobj": {"elid": field_id, "sid": PAYPAL_ONBOARDING_APP_ID, "tst": PAYPAL_ONBOARDING_APP_ID, "wsps": False, "ts": ts, "pf": {"psu": False, "val": False}}}
        try:
            url = "https://c.paypal.com/v1/r/d/b/w?" + urlencode({"f": ec_token, "s": PAYPAL_ONBOARDING_APP_ID, "d": json.dumps(payload, separators=(",", ":"))})
            resp = http.get(url, headers=headers, timeout=20)
            emit(log, f"paypal_http: pure_protocol fraudnet field {field_id} status={resp.status_code}")
        except Exception as exc:
            emit(log, f"paypal_http: pure_protocol fraudnet field {field_id} skipped: {exc}", level="warning")
        elapsed += random.randint(700, 4500)
    try:
        chunks = []
        base = random.randint(8_000, 52_000)
        for _ in range(18):
            a = max(1000, base + random.randint(-28_000, 28_000))
            chunks.append(f"{a},{a - random.randint(80, 260)},{a - random.randint(300, 650)}")
        url = "https://c.paypal.com/v1/r/d/b/w?" + urlencode({"f": ec_token, "s": PAYPAL_ONBOARDING_APP_ID, "d": json.dumps({"rDT": ":".join(chunks) + f":{random.randint(9000, 26000)},{random.randint(20, 80)}"}, separators=(",", ":"))})
        resp = http.get(url, headers=headers, timeout=20)
        emit(log, f"paypal_http: pure_protocol fraudnet rDT status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol fraudnet rDT skipped: {exc}", level="warning")


def _paypal_weasley_log(
    http: Any,
    *,
    ec_token: str,
    signup_url: str,
    event_names: list[str],
    locale_country: str,
    locale_lang: str,
    log: LogFn | None,
) -> None:
    now = int(time.time() * 1000)
    locale = f"{locale_lang}_{locale_country}"
    events = []
    for index, name in enumerate(event_names):
        events.append({
            "level": "info",
            "event": name,
            "payload": {
                "clientCountry": locale_country,
                "clientLocale": locale,
                "clientTimestamp": now + index,
                "timestamp": str(now + index),
                "token": ec_token,
            },
        })
    if not events:
        return
    body = {
        "events": events,
        "meta": {
            "integrationData": {
                "contextId": ec_token,
                "contextType": ec_token,
                "integrationMethod": "FULLPAGE",
                "integrationType": "EC",
            }
        },
        "tracking": [],
        "metrics": [],
    }
    headers = _risk_headers(referer=signup_url, same_site=False)
    headers["X-App-Name"] = "checkoutuinodeweb_weasley"
    try:
        resp = http.post(f"{PP_ORIGIN}/xoplatform/logger/api/logger/", json=body, headers=headers, timeout=20)
        emit(log, f"paypal_http: pure_protocol weasley events={len(events)} status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: pure_protocol weasley skipped: {exc}", level="warning")


def _otp_challenge_check(
    http: Any,
    *,
    signup_url: str,
    signup_html: str,
    email: str,
    ec_token: str,
    ctx_id: str,
    country: str,
    log: LogFn | None,
) -> None:
    """Best-effort idapps/graphql getOtpChallengeOperation probe.

    Matches the HAR flow: PayPal checks whether the email is already registered
    before the phone OTP step. Failure here is non-fatal; we log and continue.
    """
    csrf = first_match([
        r'"csrfNonce"\s*:\s*"([^"]+)"',
        r'name="_csrfNonce"\s+value="([^"]+)"',
        r'"csrf_nonce"\s*:\s*"([^"]+)"',
    ], signup_html)
    if not csrf or not email:
        emit(log, "paypal_http: idapps otp_challenge skipped (no csrfNonce or email)")
        return
    fn_sync = _paypal_fn_sync_data(ec_token, include_d=False)
    payload = {
        "operationName": "getOtpChallengeOperation",
        "query": "",
        "csrfNonce": csrf,
        "variables": {
            "clientInfo": {
                "fnId": ec_token,
                "ctxId": ctx_id or "",
                "rData": quote(json.dumps({"fn_sync_data": fn_sync}, separators=(",", ":"))),
            },
            "credentials": {
                "credentialValue": email,
                "credentialType": "EMAIL",
            },
            "challengeInfo": {"autoSmsOtp": False},
        },
        "fn_sync_data": fn_sync,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "fetch",
        "x-app-name": "checkoutuinodeweb_weasley",
        "paypal-client-context": ec_token,
        "paypal-client-metadata-id": ec_token,
        "x-country": country,
        "x-locale": "en_US",
        "Origin": "https://www.paypal.com",
        "Referer": signup_url,
    }
    try:
        resp = http.post("https://www.paypal.com/idapps/graphql", json=payload, headers=headers, timeout=30)
        emit(log, f"paypal_http: idapps otp_challenge status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: idapps otp_challenge skipped: {exc}", level="warning")


def _signup_email(paypal_cfg: dict[str, Any]) -> str:
    return str(
        paypal_cfg.get("signup_email")
        or paypal_cfg.get("guest_email")
        or paypal_cfg.get("email")
        or f"ctf{uuid.uuid4().hex[:10]}@example.com"
    )


def _pure_protocol_signup_email(paypal_cfg: dict[str, Any]) -> str:
    return str(paypal_cfg.get("signup_email") or paypal_cfg.get("guest_email") or f"{uuid.uuid4().hex[:16]}@gmail.com")


def _paypal_address_autocomplete(
    http: Any,
    signup_url: str,
    paypal_cfg: dict[str, Any],
    runtime_ctx: dict[str, Any],
    country: str,
    lang: str,
    ec_token: str,
) -> None:
    address = _signup_address(paypal_cfg, str(paypal_cfg.get("first_name") or "Jealous"), str(paypal_cfg.get("last_name") or "Lane"), country)
    line1 = str(address.get("line1") or "")
    session_id = str(paypal_cfg.get("address_session_id") or runtime_ctx.get("paypal_address_session_id") or short_random_id())
    location = str(paypal_cfg.get("address_location") or runtime_ctx.get("paypal_address_location") or "43.110,-88.070")
    place_id = str(paypal_cfg.get("address_place_id") or runtime_ctx.get("paypal_address_place_id") or "")
    if len(line1) > 1:
        graphql_checkoutweb(http, _address_autocomplete_payload(line1[:-1], country, lang, session_id, location), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressAutocompleteQuery")
    if line1:
        suggestions = graphql_checkoutweb(http, _address_autocomplete_payload(line1, country, lang, session_id, location), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressAutocompleteQuery")
        if not place_id:
            place_id = str(find_key_recursive(suggestions, "placeId") or "")
    if place_id:
        graphql_checkoutweb(http, _address_place_payload(place_id, lang, session_id), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressFromAutocompletePlaceIdQuery")


def _deferred_feature_payload(ec_token: str, country: str) -> dict[str, Any]:
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


def _griffin_metadata_payload(country: str, lang: str) -> dict[str, Any]:
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


def _checkout_session_payload(ec_token: str) -> dict[str, Any]:
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


def _address_autocomplete_payload(line1: str, country: str, lang: str, session_id: str, location: str) -> dict[str, Any]:
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


def _address_place_payload(place_id: str, lang: str, session_id: str) -> dict[str, Any]:
    return {
        "operationName": "AddressFromAutocompletePlaceIdQuery",
        "variables": {"language": lang, "placeId": place_id, "sessionId": session_id},
        "query": (
            "query AddressFromAutocompletePlaceIdQuery($language: CheckoutContentLanguageCode, $placeId: ID!, "
            "$sessionId: String!) { addressFromAutoCompletePlaceId(language: $language placeId: $placeId "
            "sessionId: $sessionId) { address { line1 line2 city state postalCode country __typename } __typename } }"
        ),
    }


def _initiate_phone_payload(ec_token: str, phone: str, phone_country: str, country: str, lang: str) -> dict[str, Any]:
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


def _confirm_phone_payload(ec_token: str, auth_id: str, challenge_id: str, otp: str) -> dict[str, Any]:
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


_SIGNUP_QUERY = (
    "mutation SignUpNewMemberMutation($bank: BankAccountInput, $billingAddress: AddressInput, "
    "$card: CardInput, $contentIdentifier: String, $country: CountryCodes, "
    "$countrySpecificFirstName: String, $countrySpecificLastName: String, "
    "$crsData: CommonReportingStandardsInput, $currencyConversionType: CheckoutCurrencyConversionType, "
    "$dateOfBirth: DateOfBirth, $email: String!, $firstName: String!, $gender: Gender, "
    "$identityDocument: IdentityDocumentInput, $lastName: String!, $middleName: String, "
    "$marketingOptOut: Boolean, $nationality: CountryCodes, $occupation: Occupation, "
    "$password: String, $phone: PhoneInput!, $placeOfBirth: CountryCodes, "
    "$secondaryIdentityDocument: IdentityDocumentInput, $selectedInstallmentOption: InstallmentsInput, "
    "$shareAddressWithDonatee: Boolean, $shippingAddress: AddressInput, "
    "$supportedThreeDsExperiences: [ThreeDSPaymentExperience], $token: String!, "
    "$residentialAddress: AddressInput, $isSignupIncentiveOptIn: Boolean, "
    "$isSignupIncentiveOptInStretch: Boolean, $legalAgreements: LegalAgreementsInput, "
    "$collectedConsents: [CollectedConsent]) { "
    "onboardAccount: signUpNewMember(bank: $bank billingAddress: $billingAddress card: $card "
    "contentIdentifier: $contentIdentifier countrySpecificFirstName: $countrySpecificFirstName "
    "countrySpecificLastName: $countrySpecificLastName country: $country crsData: $crsData "
    "currencyConversionType: $currencyConversionType dateOfBirth: $dateOfBirth email: $email "
    "firstName: $firstName gender: $gender identityDocument: $identityDocument lastName: $lastName "
    "middleName: $middleName marketingOptOut: $marketingOptOut nationality: $nationality "
    "occupation: $occupation password: $password phone: $phone placeOfBirth: $placeOfBirth "
    "secondaryIdentityDocument: $secondaryIdentityDocument selectedInstallmentOption: $selectedInstallmentOption "
    "shareAddressWithDonatee: $shareAddressWithDonatee shippingAddress: $shippingAddress token: $token "
    "residentialAddress: $residentialAddress isSignupIncentiveOptIn: $isSignupIncentiveOptIn "
    "isSignupIncentiveOptInStretch: $isSignupIncentiveOptInStretch legalAgreements: $legalAgreements "
    "collectedConsents: $collectedConsents) "
    "{ ...buyer flags { is3DSecureRequired __typename } ...fundingOptions paymentContingencies "
    "{ ...threeDomainSecure ...threeDSContingencyData __typename } __typename } } "
    "fragment buyer on CheckoutSession { buyer { auth { accessToken __typename } userId __typename } __typename } "
    "fragment fundingOptions on CheckoutSession { fundingOptions { allPlans { fundingSources { fundingInstrument "
    "{ id __typename } amount { currencyCode currencyValue __typename } __typename } fundingContingencies "
    "{ ... on OpenBankingContingency { encryptedId contingencyReasons contingencyType __typename } __typename } "
    "__typename } fundingInstrument { id lastDigits name nameDescription type __typename } __typename } __typename } "
    "fragment threeDomainSecure on PaymentContingencies { threeDomainSecure(experiences: $supportedThreeDsExperiences) "
    "{ status redirectUrl { href __typename } method parameter experience requestParams { key value __typename } __typename } __typename } "
    "fragment threeDSContingencyData on PaymentContingencies { threeDSContingencyData { name causeName resolution "
    "{ type resolutionName paymentCard { billingAddress { line1 line2 city state country postalCode __typename } "
    "expireYear expireMonth currencyCode cardProductClass id encryptedNumber type number bankIdentificationNumber __typename } "
    "contingencyContext { deviceDataCollectionUrl { href __typename } jwtSpecification { jwtDuration jwtIssuer jwtOrgUnitId type __typename } "
    "authenticationProvider cardBrandProcessed reason referenceId source __typename } __typename } __typename } __typename }"
)


def _signup_payload(
    ec_token: str,
    paypal_cfg: dict[str, Any],
    phone_number: str,
    phone_country_code_value: str,
    country: str,
    email: str,
    *,
    signup_html: str = "",
    lang: str = "en",
) -> dict[str, Any]:
    first_name = str(paypal_cfg.get("first_name") or "Jealous")
    last_name = str(paypal_cfg.get("last_name") or "Lane")
    password = str(paypal_cfg.get("signup_password") or paypal_cfg.get("guest_password") or paypal_cfg.get("password") or gen_paypal_password())
    card = _signup_card(paypal_cfg)
    address = _signup_address(paypal_cfg, first_name, last_name, country)
    return {
        "operationName": "SignUpNewMemberMutation",
        "variables": {
            "card": card,
            "country": country,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "phone": {"countryCode": phone_country_code_value, "number": phone_number, "type": "MOBILE"},
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
            "contentIdentifier": str(paypal_cfg.get("content_identifier") or _extract_content_identifier(signup_html, country, lang)),
            "marketingOptOut": bool(paypal_cfg.get("marketing_opt_out") or False),
            "password": password,
            "crsData": None,
            "legalAgreements": {},
        },
        "query": _SIGNUP_QUERY,
        "fn_sync_data": generate_fn_sync_data(email, password),
    }


def _extract_content_identifier(html: str, country: str, lang: str) -> str:
    for pattern in (
        r'"contentIdentifier"\s*:\s*"([^"]*signupTerms[^"]*)"',
        r'\\"contentIdentifier\\"\s*:\s*\\"([^"\\]*signupTerms[^"\\]*)\\"',
        r'([A-Z]{2}:[a-z]{2}:[0-9a-f]{16,64}:compliance\.signupTerms)',
    ):
        value = first_match([pattern], html, re.I)
        if value:
            return value.replace("\\/", "/")
    fallback = f"{country}:{lang}:compliance.signupTerms"
    if country == "US" and lang == "en":
        return SIGNUP_TERMS_CONTENT_ID
    return fallback


def _signup_card(paypal_cfg: dict[str, Any]) -> dict[str, str]:
    card = paypal_cfg.get("card") if isinstance(paypal_cfg.get("card"), dict) else {}
    number = str(card.get("number") or paypal_cfg.get("card_number") or "").replace(" ", "")
    exp_month = str(card.get("exp_month") or paypal_cfg.get("card_exp_month") or "").zfill(2)
    exp_year = str(card.get("exp_year") or paypal_cfg.get("card_exp_year") or "")
    cvv = str(card.get("cvv") or paypal_cfg.get("card_cvv") or "")
    if not number or not exp_month.strip("0") or not exp_year or not cvv:
        generated = _generate_signup_card()
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
        "type": str(card.get("type") or paypal_cfg.get("card_type") or card_type(number)),
    }


def _generate_signup_card() -> dict[str, str]:
    base = "4147"
    while len(base) < 15:
        base += str(random.randint(0, 9))
    year = utc_year() + 2 + random.randint(0, 3)
    return {
        "number": base + luhn_check_digit(base),
        "exp_month": str(random.randint(1, 12)).zfill(2),
        "exp_year": str(year),
        "cvv": str(random.randint(100, 999)),
    }


def _signup_address(paypal_cfg: dict[str, Any], first_name: str, last_name: str, country: str) -> dict[str, Any]:
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


def _extract_phone_confirmation(payload: Any, *, require_auth_ids: bool) -> dict[str, str]:
    """Pull authId/challengeId/state from initiate or confirm responses.

    `require_auth_ids` should be True for the initiate response (we need both
    ids to call confirm next), False for the confirm response (server returns
    them as null on success; only `state` is meaningful there).
    """
    found = find_key_recursive(payload, "initiateRiskBasedTwoFactorPhoneConfirmation") or find_key_recursive(payload, "confirmRiskBasedTwoFactorPhoneConfirmation")
    if not isinstance(found, dict):
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少确认状态: {payload}")
    auth_id = str(found.get("authId") or "")
    challenge_id = str(found.get("challengeId") or "")
    state = str(found.get("state") or "")
    if require_auth_ids and (not auth_id or not challenge_id):
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少 authId/challengeId: {payload}")
    return {"authId": auth_id, "challengeId": challenge_id, "state": state}


def _extract_buyer_access_token(payload: Any) -> str:
    value = find_key_recursive(payload, "accessToken")
    if value:
        return str(value)
    error = _first_signup_error(payload)
    error_data = error.get("errorData") if isinstance(error, dict) else None
    if isinstance(error_data, dict):
        value = error_data.get("accessToken")
        if value:
            return str(value)
        nested = error_data.get("0")
        if isinstance(nested, dict) and nested.get("accessToken"):
            return str(nested.get("accessToken") or "")
    if isinstance(error_data, list):
        for item in error_data:
            if isinstance(item, dict) and item.get("accessToken"):
                return str(item.get("accessToken") or "")
    return ""


def _first_signup_error(payload: Any) -> dict[str, Any]:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    first = errors[0] if isinstance(errors, list) and errors else None
    return first if isinstance(first, dict) else {}


def _signup_error_reason(error: dict[str, Any]) -> str:
    raw_data = error.get("errorData") if isinstance(error, dict) else None
    code = ""
    if isinstance(raw_data, dict):
        nested = raw_data.get("0")
        if isinstance(nested, dict):
            code = str(nested.get("code") or "")
        code = code or str(raw_data.get("code") or "")
    elif isinstance(raw_data, list) and raw_data and isinstance(raw_data[0], dict):
        code = str(raw_data[0].get("code") or "")
    code = code or str((error.get("checkpoints") or [""])[0] if isinstance(error, dict) else "")
    code = code or str(error.get("message") or "SIGNUP_PARTIAL")
    return base64.b64encode(code.encode("utf-8")).decode("ascii").rstrip("=")


def _hermes_url(signup_url: str, ba_token: str, ec_token: str, reason: str = "") -> str:
    parsed = urlparse(signup_url)
    q = parse_qs(parsed.query)
    q.update({
        "ba_token": [ba_token],
        "token": [ec_token],
        "fromSignupLite": ["true"],
        "addFIContingency": ["noretry"],
        "redirectToHermes": ["true"],
    })
    if reason:
        q.update({"fallback": ["1"], "reason": [reason], "billingLite": ["1"]})
    else:
        q.update({"fallback": ["1"], "reason": ["Q0FSRF9HRU5FUklDX0VSUk9S"]})
    return "https://www.paypal.com/webapps/hermes?" + urlencode({k: v[-1] for k, v in q.items()})
