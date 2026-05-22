from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests as std_requests

from .runtime import (
    LogFn,
    PayPalHttpError,
    USER_AGENT,
    emit,
    first_match,
    generate_fn_sync_data,
)


PAYPAL_HCAPTCHA_SITE_KEY = "bf07db68-5c2e-42e8-8779-ea8384890eea"
YES_CAPTCHA_API_URL = "https://api.yescaptcha.com"


def paypal_full_login_http(
    http: Any,
    approve_html: str,
    approve_url: str,
    paypal_cfg: dict[str, Any],
    csrf: str,
    sid: str,
    flow_id: str,
    ctx_id: str,
    log: LogFn | None,
) -> None:
    email = str(paypal_cfg.get("email") or "").strip()
    password = str(paypal_cfg.get("password") or "").strip()
    if not email or not password:
        raise PayPalHttpError("PayPal full login 缺少 email/password")

    lr_resp = http.post(
        "https://www.paypal.com/signin/load-resource",
        data={"_csrf": csrf, "flowId": flow_id, "intent": "checkout", "_sessionID": sid},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
        },
        timeout=30,
    )
    emit(log, f"paypal_http: login load-resource status={lr_resp.status_code}")
    try:
        csrf = lr_resp.json().get("nonce") or csrf
    except Exception:
        pass

    email_resp = http.post(
        "https://www.paypal.com/signin",
        data={
            "splitLoginContext": "inputEmail",
            "login_email": email,
            "_csrf": csrf,
            "_sessionID": sid,
            "intent": "checkout",
            "flowId": flow_id,
            "ctxId": ctx_id or f"xo_ctx_{flow_id}",
            "fn_sync_data": generate_fn_sync_data(email),
            "locale.x": str(paypal_cfg.get("locale") or "en_US"),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/html, */*",
        },
        timeout=30,
    )
    emit(log, f"paypal_http: login email status={email_resp.status_code}")
    try:
        csrf = email_resp.json().get("nonce") or csrf
    except Exception:
        found = first_match([r'name="_csrf"\s+value="([^"]+)"'], email_resp.text or "")
        csrf = found or csrf

    pwd_resp = http.post(
        "https://www.paypal.com/signin",
        data={
            "splitLoginContext": "inputPassword",
            "login_email": email,
            "login_password": password,
            "_csrf": csrf,
            "_sessionID": sid,
            "intent": "checkout",
            "flowId": flow_id,
            "ctxId": ctx_id or f"xo_ctx_{flow_id}",
            "fn_sync_data": generate_fn_sync_data(email, password),
            "locale.x": str(paypal_cfg.get("locale") or "en_US"),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
            "X-Requested-With": "XMLHttpRequest",
        },
        allow_redirects=False,
        timeout=30,
    )
    emit(log, f"paypal_http: login password status={pwd_resp.status_code}")
    current_resp = _submit_hcaptcha_if_required(http, pwd_resp, paypal_cfg, csrf, sid, approve_url, log)
    current_resp = _follow_redirects(http, current_resp, log)
    _answer_email_otp_if_required(http, current_resp, paypal_cfg, ctx_id, log)


def _submit_hcaptcha_if_required(
    http: Any,
    pwd_resp: Any,
    paypal_cfg: dict[str, Any],
    csrf: str,
    sid: str,
    approve_url: str,
    log: LogFn | None,
) -> Any:
    if pwd_resp.status_code != 200:
        return pwd_resp
    html = pwd_resp.text or ""
    needs_hcaptcha = "hcaptcha" in html.lower() or bool(first_match([r'name="_requestId"\s+value="([^"]+)"'], html))
    if not needs_hcaptcha:
        return pwd_resp
    site_key = _extract_hcaptcha_site_key(html)
    if not site_key:
        emit(log, "paypal_http: hcaptcha marker without sitekey in login response; skipping solver", level="warning")
        return pwd_resp
    challenge_url = str(getattr(pwd_resp, "url", "") or approve_url)
    captcha_token = solve_paypal_hcaptcha(paypal_cfg, log, website_url=challenge_url, site_key=site_key)
    if not captcha_token:
        raise PayPalHttpError("PayPal 需要 hCaptcha，但未得到验证码 token")
    request_id = first_match([r'name="_requestId"\s+value="([^"]+)"'], html)
    hash_value = first_match([r'name="_hash"\s+value="([^"]+)"'], html)
    csrf = first_match([r'name="_csrf"\s+value="([^"]+)"'], html) or csrf
    return http.post(
        "https://www.paypal.com/signin",
        data={
            "_csrf": csrf,
            "_requestId": request_id,
            "_hash": hash_value,
            "_sessionID": sid,
            "hcaptcha": captcha_token,
            "_adsChallengeType": "visual-challenge",
            "hcaptcha_eval": str(random.randint(200, 600)),
            "hcaptcha_render": str(random.randint(100, 300)),
            "hcaptcha_verification": str(random.randint(5000, 15000)),
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.paypal.com",
            "Referer": approve_url,
        },
        allow_redirects=False,
        timeout=30,
    )


def _follow_redirects(http: Any, response: Any, log: LogFn | None) -> Any:
    current = response
    for _ in range(10):
        if current.status_code not in (301, 302, 303, 307, 308):
            break
        location = current.headers.get("Location") or current.headers.get("location") or ""
        if not location:
            break
        location = urljoin("https://www.paypal.com", location)
        emit(log, f"paypal_http: login redirect {location[:100]}")
        current = http.get(location, allow_redirects=False, timeout=30)
    return current


def _answer_email_otp_if_required(
    http: Any,
    response: Any,
    paypal_cfg: dict[str, Any],
    ctx_id: str,
    log: LogFn | None,
) -> None:
    current_url = str(getattr(response, "url", "") or "")
    html = response.text or ""
    if "/authflow" not in current_url and "authflow" not in html[:5000]:
        return
    csrf = first_match([r'"_csrf"\s*:\s*"([^"]+)"', r'name="_csrf"\s+value="([^"]+)"', r'"csrfToken"\s*:\s*"([^"]+)"'], html)
    anw_sid = first_match([r'"anw_sid"\s*:\s*"([^"]+)"'], html)
    doc_id = first_match([r'"authflowDocumentId"\s*:\s*"([^"]+)"', r'"documentId"\s*:\s*"([^"]+)"'], html)
    select_resp = http.put(
        "https://www.paypal.com/authflow/challenges/email",
        json={
            "_csrf": csrf,
            "anw_sid": anw_sid,
            "authflowDocumentId": doc_id,
            "action": "SELECT_CHALLENGE",
            "selectedChallengeType": "email",
            "isCheckoutFlow": True,
            "fn_sync_data": generate_fn_sync_data(),
        },
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.paypal.com",
            "Referer": current_url,
        },
        timeout=30,
    )
    emit(log, f"paypal_http: paypal otp select status={select_resp.status_code}")
    try:
        select_json = select_resp.json()
        doc_id = select_json.get("authflowDocumentId") or doc_id
        csrf = select_json.get("_csrf") or csrf
    except Exception:
        pass
    otp = fetch_paypal_otp(paypal_cfg, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("PayPal 2FA OTP 获取失败")
    answer_resp = http.put(
        "https://www.paypal.com/authflow/challenges/email",
        json={
            "_csrf": csrf,
            "anw_sid": anw_sid,
            "authflowDocumentId": doc_id,
            "action": "ANSWER",
            "answer": otp,
            "selectedChallengeType": "email",
            "isCheckoutFlow": True,
            "challengeStartTime": str(int(time.time() * 1000)),
        },
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.paypal.com",
            "Referer": current_url,
        },
        timeout=30,
    )
    emit(log, f"paypal_http: paypal otp answer status={answer_resp.status_code}")
    http.get(f"https://www.paypal.com/signin/return?flowFrom=anw-stepup&ctxId={ctx_id}", allow_redirects=True, timeout=30)


def _captcha_section(paypal_cfg: dict[str, Any]) -> dict[str, Any]:
    captcha = paypal_cfg.get("captcha") or {}
    return captcha if isinstance(captcha, dict) else {}


def _extract_hcaptcha_site_key(html: str) -> str:
    return first_match([
        r'data-sitekey=["\']([^"\']+)["\']',
        r'["\']sitekey["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']siteKey["\']\s*:\s*["\']([^"\']+)["\']',
        r'["\']websiteKey["\']\s*:\s*["\']([^"\']+)["\']',
    ], html) or ""


def _captcha_int(paypal_cfg: dict[str, Any], key: str, default: int) -> int:
    captcha = _captcha_section(paypal_cfg)
    try:
        return int(paypal_cfg.get(f"captcha_{key}") or captcha.get(key) or default)
    except Exception:
        return default


def _captcha_solution_token(payload: dict[str, Any]) -> str:
    solution = payload.get("solution") or {}
    if isinstance(solution, str):
        return solution.strip()
    if isinstance(solution, dict):
        for key in ("gRecaptchaResponse", "token", "hCaptchaResponse"):
            value = str(solution.get(key) or "").strip()
            if value:
                return value
    return str(payload.get("token") or "").strip()


def solve_paypal_hcaptcha(
    paypal_cfg: dict[str, Any],
    log: LogFn | None,
    website_url: str = "",
    site_key: str = "",
    rqdata: str = "",
) -> str:
    captcha = _captcha_section(paypal_cfg)
    token = str(
        paypal_cfg.get("hcaptcha_token")
        or paypal_cfg.get("captcha_token")
        or captcha.get("token")
        or captcha.get("hcaptcha_token")
        or ""
    ).strip()
    if token:
        return token

    api_key = str(paypal_cfg.get("captcha_api_key") or captcha.get("api_key") or "").strip()
    provider = str(paypal_cfg.get("captcha_provider") or captcha.get("provider") or "").strip().lower()
    api_url = str(
        paypal_cfg.get("captcha_api_url")
        or captcha.get("api_url")
        or os.getenv("CTF_CAPTCHA_API_URL")
        or (YES_CAPTCHA_API_URL if api_key and (not provider or provider == "yescaptcha") else "")
    ).rstrip("/")
    if not api_key or not api_url:
        return ""

    site_key = str(site_key or paypal_cfg.get("hcaptcha_site_key") or captcha.get("hcaptcha_site_key") or PAYPAL_HCAPTCHA_SITE_KEY).strip()
    website_url = str(website_url or paypal_cfg.get("hcaptcha_website_url") or captcha.get("website_url") or "https://www.paypal.com/signin").strip()
    rqdata = str(rqdata or paypal_cfg.get("hcaptcha_rqdata") or captcha.get("rqdata") or captcha.get("hcaptcha_rqdata") or "").strip()
    task: dict[str, Any] = {
        "type": "HCaptchaTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": site_key,
        "isEnterprise": True,
        "userAgent": USER_AGENT,
    }
    if rqdata:
        task["enterprisePayload"] = {"rqdata": rqdata}
    create_resp = std_requests.post(
        f"{api_url}/createTask",
        json={"clientKey": api_key, "task": task},
        timeout=30,
    )
    create_payload = create_resp.json() or {}
    if create_payload.get("errorId"):
        emit(log, f"paypal_http: hcaptcha provider error={create_payload.get('errorCode') or ''} {create_payload.get('errorDescription') or ''}".strip(), level="warning")
        return ""
    task_id = create_payload.get("taskId")
    if not task_id:
        emit(log, f"paypal_http: hcaptcha provider missing taskId: {create_payload}", level="warning")
        return ""

    deadline = time.time() + _captcha_int(paypal_cfg, "timeout", 120)
    poll_interval = max(1, _captcha_int(paypal_cfg, "poll_interval", 5))
    while time.time() < deadline:
        time.sleep(poll_interval)
        poll_resp = std_requests.post(f"{api_url}/getTaskResult", json={"clientKey": api_key, "taskId": task_id}, timeout=20)
        payload = poll_resp.json() or {}
        if payload.get("status") == "ready":
            return _captcha_solution_token(payload)
        if payload.get("errorId"):
            emit(log, f"paypal_http: hcaptcha provider error={payload.get('errorCode') or ''} {payload.get('errorDescription') or ''}".strip(), level="warning")
            return ""
    return ""


def fetch_paypal_otp(paypal_cfg: dict[str, Any], timeout: int, log: LogFn | None) -> str:
    static_otp = str(paypal_cfg.get("otp") or paypal_cfg.get("email_otp") or "").strip()
    if static_otp:
        return static_otp
    otp_file = str(paypal_cfg.get("otp_file") or "").strip()
    if otp_file:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(otp_file, "r", encoding="utf-8") as handle:
                    value = handle.read().strip()
                match = re.search(r"\b(\d{6})\b", value)
                if match:
                    return match.group(1)
            except FileNotFoundError:
                pass
            time.sleep(1)
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if smsurl:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = std_requests.get(smsurl, timeout=15)
                text = resp.text or ""
                try:
                    payload = resp.json()
                    text += " " + json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass
                match = re.search(r"\b(\d{6})\b", text)
                if match:
                    return match.group(1)
            except Exception as exc:
                emit(log, f"paypal_http: smsurl otp poll failed: {exc}", level="warning")
            time.sleep(2)
    try:
        from cf_kv_otp_provider import CloudflareKVOtpProvider
    except Exception as exc:
        emit(log, f"paypal_http: cf otp provider unavailable: {exc}", level="warning")
        return ""
    target = str(paypal_cfg.get("email") or "").strip()
    if not target:
        return ""
    try:
        return str(CloudflareKVOtpProvider.from_env_or_secrets().wait_for_otp(target, timeout=timeout) or "")
    except Exception as exc:
        emit(log, f"paypal_http: paypal otp wait failed: {exc}", level="warning")
        return ""
