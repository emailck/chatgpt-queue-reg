"""ChatGPT TOTP MFA setup helpers.

This module is intentionally small and self-contained so the stage code can
call it as if it were an external MFA service module.  The default transport
hits the same ChatGPT backend-api endpoints captured in the HAR, but the base
URLs are configurable so a separate MFA server can be swapped in later.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.integrations.chatgpt.backend_headers import build_backend_headers


@dataclass(slots=True)
class MfaEnrollment:
    secret: str = ""
    session_id: str = ""
    factor_id: str = ""
    qr_code_secret_url: str = ""
    raw: dict[str, Any] | None = None


class ChatGPTTotpMfaAdapter:
    def __init__(self, *, secret: str, factor_id: str = "", log_fn=None, timeout_seconds: int = 30) -> None:
        self.secret = str(secret or "").strip()
        self.factor_id = str(factor_id or "").strip()
        self.log_fn = log_fn or (lambda _msg: None)
        self.timeout_seconds = max(1, min(int(timeout_seconds or 30), 3600))

    def has_secret(self) -> bool:
        return bool(self.secret)

    def get_code(self, email: str, *, otp_sent_at: float | None = None) -> str:
        if not self.secret:
            raise RuntimeError("missing TOTP secret")
        self._log(f"OAuth 登录等待 TOTP 验证码 ({self.timeout_seconds}s): {email}")
        code = generate_totp_code(self.secret)
        self._log(f"OAuth 登录 TOTP 验证码生成成功: {code}")
        return code

    def get_factor_id(self) -> str:
        return self.factor_id

    def get_secret(self) -> str:
        return self.secret

    def _log(self, message: str) -> None:
        try:
            self.log_fn(str(message or ""))
        except Exception:
            pass


class TwoFAuthMfaAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        pat: str,
        account_id: str,
        factor_id: str = "",
        log_fn=None,
        timeout_seconds: int = 30,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = _normalize_twofauth_base_url(base_url)
        self.api_base_url = f"{self.base_url}/api/v1"
        self.pat = str(pat or "").strip()
        self.account_id = str(account_id or "").strip()
        self.factor_id = str(factor_id or "").strip()
        self.log_fn = log_fn or (lambda _msg: None)
        self.timeout_seconds = max(1, min(int(timeout_seconds or 30), 3600))
        self.verify_tls = bool(verify_tls)
        if not self.base_url:
            raise RuntimeError("twofauth_base_url 未配置")
        if not self.pat:
            raise RuntimeError("twofauth_pat 未配置")
        if not self.account_id:
            raise RuntimeError("twofauth_account_id 未配置")
        self.session = requests.Session()

    def get_code(self, email: str, *, otp_sent_at: float | None = None) -> str:
        self._log(f"OAuth 登录等待 TOTP 验证码 (2FAuth, {self.timeout_seconds}s): {email}")
        code = self._fetch_otp()
        if not code:
            raise RuntimeError("2FAuth 未返回验证码")
        self._log(f"OAuth 登录 TOTP 验证码获取成功: len={len(code)}")
        return code

    def get_factor_id(self) -> str:
        return self.factor_id

    def get_twofauth_account_id(self) -> str:
        return self.account_id

    def _fetch_otp(self) -> str:
        url = f"{self.api_base_url}/twofaccounts/{self.account_id}/otp"
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "Accept": "application/json",
        }
        resp = self.session.get(url, headers=headers, timeout=30, verify=self.verify_tls)
        self._log(f"2FAuth otp -> {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"2FAuth otp failed: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"2FAuth otp returned non-json: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("2FAuth otp returned non-object JSON")
        code = str(data.get("password") or "").strip()
        return code

    def _log(self, message: str) -> None:
        try:
            self.log_fn(str(message or ""))
        except Exception:
            pass


class TwoFAuthClient:
    def __init__(
        self,
        *,
        base_url: str,
        pat: str,
        verify_tls: bool = True,
        log_fn=None,
    ) -> None:
        self.base_url = _normalize_twofauth_base_url(base_url)
        self.api_base_url = f"{self.base_url}/api/v1"
        self.pat = str(pat or "").strip()
        self.verify_tls = bool(verify_tls)
        self.log_fn = log_fn or (lambda _msg: None)
        self.session = requests.Session()
        if not self.base_url:
            raise RuntimeError("twofauth_base_url 未配置")
        if not self.pat:
            raise RuntimeError("twofauth_pat 未配置")

    def preview_uri(self, uri: str) -> dict[str, Any]:
        return self._request("POST", "/twofaccounts/preview", json={"uri": str(uri or "").strip()})

    def create_account(self, uri: str) -> dict[str, Any]:
        return self._request("POST", "/twofaccounts", json={"uri": str(uri or "").strip()})

    def get_account(self, account_id: str) -> dict[str, Any]:
        return self._request("GET", f"/twofaccounts/{str(account_id or '').strip()}")

    def get_otp(self, account_id: str) -> dict[str, Any]:
        return self._request("GET", f"/twofaccounts/{str(account_id or '').strip()}/otp")

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.api_base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {self.pat}")
        headers.setdefault("Accept", "application/json")
        if "json" in kwargs:
            headers.setdefault("Content-Type", "application/json")
        resp = self.session.request(method, url, headers=headers, timeout=30, verify=self.verify_tls, **kwargs)
        self._log(f"2FAuth {method} {path} -> {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"2FAuth {method} {path} failed: HTTP {resp.status_code} {resp.text[:300]}")
        if not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"2FAuth {method} {path} returned non-json: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"2FAuth {method} {path} returned non-object JSON")
        return data

    def _log(self, message: str) -> None:
        try:
            self.log_fn(str(message or ""))
        except Exception:
            pass


class ChatGPTMfaClient:
    def __init__(
        self,
        *,
        access_token: str,
        cookies: list[dict[str, Any]] | None = None,
        user_agent: str = "",
        oai_session_id: str = "",
        oai_client_version: str = "",
        oai_client_build_number: str = "",
        device_id: str = "",
        chatgpt_account_id: str = "",
        proxy_url: str = "",
        api_base_url: str = "https://chatgpt.com",
        auth_base_url: str = "https://auth.openai.com",
        verify_tls: bool = True,
        log_fn=None,
    ) -> None:
        self.access_token = str(access_token or "").strip()
        self.user_agent = str(user_agent or "").strip() or "Mozilla/5.0"
        self.oai_session_id = str(oai_session_id or "").strip() or str(uuid.uuid4())
        self.oai_client_version = str(oai_client_version or "").strip()
        self.oai_client_build_number = str(oai_client_build_number or "").strip()
        self.device_id = str(device_id or "").strip() or str(uuid.uuid4())
        self.chatgpt_account_id = str(chatgpt_account_id or "").strip()
        self.api_base_url = str(api_base_url or "https://chatgpt.com").rstrip("/")
        self.auth_base_url = str(auth_base_url or "https://auth.openai.com").rstrip("/")
        self.verify_tls = bool(verify_tls)
        self.log_fn = log_fn or (lambda _msg: None)
        self.session = requests.Session()
        proxies = build_requests_proxy_config(proxy_url)
        if proxies:
            self.session.proxies.update(proxies)
        for cookie in cookies or []:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "").strip()
            if not name or not value:
                continue
            self.session.cookies.set(
                name,
                value,
                domain=str(cookie.get("domain") or ".chatgpt.com"),
                path=str(cookie.get("path") or "/"),
            )

    def get_mfa_info(self, *, factor_type: str = "totp", action: str = "enable") -> dict[str, Any]:
        url = f"{self.api_base_url}/backend-api/accounts/mfa_info"
        headers = self._headers(
            url,
            accept="*/*",
            referer=f"{self.api_base_url}/?action={action}&factor={factor_type}",
        )
        resp = self.session.get(url, headers=headers, timeout=30, verify=self.verify_tls)
        return self._expect_json(resp, label="mfa_info")

    def enroll_totp(self, *, factor_type: str = "totp") -> MfaEnrollment:
        url = f"{self.api_base_url}/backend-api/accounts/mfa/enroll"
        headers = self._headers(
            url,
            accept="*/*",
            origin=self.api_base_url,
            referer=f"{self.api_base_url}/?action=enable&factor={factor_type}",
            content_type="application/json",
        )
        resp = self.session.post(
            url,
            json={"factor_type": factor_type},
            headers=headers,
            timeout=30,
            verify=self.verify_tls,
        )
        data = self._expect_json(resp, label="mfa/enroll")
        secret = str(data.get("secret") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        factor = data.get("factor") if isinstance(data.get("factor"), dict) else {}
        factor_id = str(factor.get("id") or "").strip()
        qr_url = str(data.get("qr_code_secret_url") or "").strip()
        if not qr_url and secret:
            qr_url = build_otpauth_url(secret, email=str(data.get("email") or ""), issuer="ChatGPT")
        return MfaEnrollment(
            secret=secret,
            session_id=session_id,
            factor_id=factor_id,
            qr_code_secret_url=qr_url,
            raw=data,
        )

    def activate_totp_enrollment(self, *, session_id: str, code: str, factor_type: str = "totp") -> dict[str, Any]:
        url = f"{self.api_base_url}/backend-api/accounts/mfa/user/activate_enrollment"
        headers = self._headers(
            url,
            accept="*/*",
            origin=self.api_base_url,
            referer=self.api_base_url + "/",
            content_type="application/json",
        )
        resp = self.session.post(
            url,
            json={"code": str(code or "").strip(), "factor_type": factor_type, "session_id": session_id},
            headers=headers,
            timeout=30,
            verify=self.verify_tls,
        )
        return self._expect_json(resp, label="mfa/user/activate_enrollment")

    def verify_totp_login(self, *, factor_id: str, code: str, factor_type: str = "totp") -> dict[str, Any]:
        url = f"{self.auth_base_url}/api/accounts/mfa/verify"
        headers = self._headers(
            url,
            accept="*/*",
            origin=self.auth_base_url,
            referer=self.auth_base_url + "/",
            content_type="application/json",
        )
        resp = self.session.post(
            url,
            json={"id": str(factor_id or "").strip(), "type": factor_type, "code": str(code or "").strip()},
            headers=headers,
            timeout=30,
            verify=self.verify_tls,
        )
        return self._expect_json(resp, label="mfa/verify")

    def _headers(
        self,
        url: str,
        *,
        accept: str,
        referer: str = "",
        origin: str = "",
        content_type: str = "",
    ) -> dict[str, str]:
        return build_backend_headers(
            url=url,
            user_agent=self.user_agent,
            accept=accept,
            referer=referer,
            origin=origin,
            content_type=content_type,
            device_id=self.device_id,
            oai_session_id=self.oai_session_id,
            access_token=self.access_token,
            chatgpt_account_id=self.chatgpt_account_id,
            oai_client_version=self.oai_client_version,
            oai_client_build_number=self.oai_client_build_number,
            extra_headers={
                "priority": "u=1, i",
            },
        )

    def _expect_json(self, resp: requests.Response, *, label: str) -> dict[str, Any]:
        text = resp.text or ""
        self._log(f"{label} -> {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"{label} failed: HTTP {resp.status_code} {text[:300]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"{label} returned non-json: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label} returned non-object JSON")
        return data

    def _log(self, message: str) -> None:
        try:
            self.log_fn(str(message or ""))
        except Exception:
            pass


def normalize_base32_secret(secret: str) -> str:
    text = str(secret or "").strip().replace(" ", "").upper()
    padding = (-len(text)) % 8
    return text + ("=" * padding)


def generate_totp_code(secret: str, *, for_time: float | None = None, period: int = 30, digits: int = 6) -> str:
    timestamp = int(time.time() if for_time is None else float(for_time))
    counter = timestamp // max(1, int(period or 30))
    key = base64.b32decode(normalize_base32_secret(secret), casefold=True)
    msg = struct.pack(">Q", int(counter))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset: offset + 4])[0] & 0x7FFFFFFF
    code_mod = 10 ** max(1, int(digits or 6))
    return str(code_int % code_mod).zfill(max(1, int(digits or 6)))


def build_otpauth_url(secret: str, *, email: str = "", issuer: str = "ChatGPT") -> str:
    secret = str(secret or "").strip()
    if not secret:
        return ""
    label = issuer if not email else f"{issuer}:{email}"
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"


def extract_secret_from_otpauth(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "otpauth":
        return ""
    query = parse_qs(parsed.query)
    return str((query.get("secret") or [""])[0] or "").strip()


def create_twofauth_adapter_from_uri(
    uri: str,
    *,
    factor_id: str = "",
    config: dict[str, Any] | None = None,
    log_fn=None,
    timeout_seconds: int = 30,
):
    base_url = _config_value(
        config,
        "twofauth_base_url",
        "workpool.chatgpt_mfa_setup.twofauth_base_url",
        "TWOFAUTH_BASE_URL",
        default="https://2fa.oai-gpt.com",
    )
    pat = _config_value(
        config,
        "twofauth_pat",
        "workpool.chatgpt_mfa_setup.twofauth_pat",
        "TWOFAUTH_PAT",
    )
    client = TwoFAuthClient(base_url=base_url, pat=pat, log_fn=log_fn)
    preview_before_create = _config_bool(
        config,
        "twofauth_preview_before_create",
        "workpool.chatgpt_mfa_setup.twofauth_preview_before_create",
        default=True,
    )
    if preview_before_create:
        client.preview_uri(uri)
    created = client.create_account(uri)
    account_id = str(created.get("id") or "").strip()
    if not account_id:
        raise RuntimeError("2FAuth create_account did not return id")
    return TwoFAuthMfaAdapter(
        base_url=base_url,
        pat=pat,
        account_id=account_id,
        factor_id=factor_id,
        log_fn=log_fn,
        timeout_seconds=timeout_seconds,
    )


def build_totp_adapter_from_metadata(
    metadata: dict[str, Any] | None,
    *,
    config: dict[str, Any] | None = None,
    log_fn=None,
    timeout_seconds: int = 30,
):
    mfa = metadata.get("mfa") if isinstance(metadata, dict) and isinstance(metadata.get("mfa"), dict) else {}
    provider = _normalize_provider(str(mfa.get("provider") or "").strip())
    if not provider:
        provider = _normalize_provider(
            _config_value(
                config,
                "mfa_code_provider",
                "workpool.chatgpt_mfa_setup.mfa_code_provider",
                default="",
            )
        )
    factor_id = str(mfa.get("factor_id") or mfa.get("native_default_factor_id") or "").strip()
    if provider == "twofauth":
        twofauth_account_id = _extract_twofauth_account_id(mfa)
        base_url = _config_value(
            config,
            "twofauth_base_url",
            "workpool.chatgpt_mfa_setup.twofauth_base_url",
            "TWOFAUTH_BASE_URL",
            default="https://2fa.oai-gpt.com",
        )
        pat = _config_value(
            config,
            "twofauth_pat",
            "workpool.chatgpt_mfa_setup.twofauth_pat",
            "TWOFAUTH_PAT",
        )
        if twofauth_account_id and base_url and pat:
            return TwoFAuthMfaAdapter(
                base_url=base_url,
                pat=pat,
                account_id=twofauth_account_id,
                factor_id=factor_id,
                log_fn=log_fn,
                timeout_seconds=timeout_seconds,
            )
    secret = str(mfa.get("secret") or "").strip()
    if not secret:
        secret = extract_secret_from_otpauth(str(mfa.get("qr_code_secret_url") or "").strip())
    if not secret:
        return None
    return ChatGPTTotpMfaAdapter(
        secret=secret,
        factor_id=factor_id,
        log_fn=log_fn,
        timeout_seconds=timeout_seconds,
    )


def _extract_twofauth_account_id(mfa: dict[str, Any]) -> str:
    direct = str(mfa.get("twofauth_account_id") or "").strip()
    if direct:
        return direct
    nested = mfa.get("twofauth") if isinstance(mfa.get("twofauth"), dict) else {}
    if isinstance(nested, dict):
        direct = str(nested.get("account_id") or nested.get("id") or "").strip()
        if direct:
            return direct
    return ""


def _normalize_provider(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"2fauth", "twofauth", "external"}:
        return "twofauth"
    if text in {"local", "native", "totp"}:
        return "local"
    return text


def _normalize_twofauth_base_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/api/v1"):
        text = text[: -len("/api/v1")]
    return text.rstrip("/")


def _config_value(config: dict[str, Any] | None, *keys: str, default: str = "") -> str:
    for key in keys:
        if isinstance(config, dict):
            raw = config.get(key)
            if raw not in (None, ""):
                return str(raw).strip()
        try:
            raw = settings.get(key, "")
        except Exception:
            raw = ""
        if raw not in (None, ""):
            return str(raw).strip()
    return str(default or "").strip()


def _config_bool(config: dict[str, Any] | None, *keys: str, default: bool = False) -> bool:
    raw = _config_value(config, *keys, default="1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}
