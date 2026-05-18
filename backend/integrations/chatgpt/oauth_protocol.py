from __future__ import annotations

import base64
import json
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests as curl_requests

from backend.core.proxy import build_requests_proxy_config

from .fingerprint import impersonate_from_user_agent
from .oauth import AUTH_BASE, AUTHORIZE_URL, OAuthSession, exchange_code
from .sentinel_token import build_sentinel_token
from .utils import (
    FlowState,
    build_browser_headers,
    describe_flow_state,
    extract_flow_state,
    generate_datadog_trace,
    normalize_flow_url,
    seed_oai_device_cookie,
)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = '"Chromium";v="143", "Google Chrome";v="143", "Not A(Brand";v="24"'


class OAuthOtpAdapter:
    def __init__(self, email_service, *, log_fn, timeout_seconds: int = 300):
        self.email_service = email_service
        self.log_fn = log_fn
        self.timeout_seconds = max(30, min(int(timeout_seconds or 300), 3600))
        self.used_codes: set[str] = set()

    def get_code(self, email: str, *, otp_sent_at: float | None = None) -> str:
        self.log_fn(f"OAuth 登录等待邮箱验证码 ({self.timeout_seconds}s): {email}")
        code = self.email_service.get_verification_code(
            email=email,
            timeout=self.timeout_seconds,
            otp_sent_at=otp_sent_at,
            exclude_codes=self.used_codes,
        )
        if not code:
            raise RuntimeError("OAuth 登录未获取到邮箱验证码")
        self.used_codes.add(str(code))
        self.log_fn(f"OAuth 登录验证码获取成功: {code}")
        return str(code)


class ProtocolOAuthClient:
    def __init__(self, config: dict[str, Any] | None = None, *, proxy: str = "", log_fn=None):
        self.config = dict(config or {})
        self.proxy = proxy
        self.log_fn = log_fn or (lambda _msg: None)
        fingerprint = self.config.get("browser_fingerprint") if isinstance(self.config.get("browser_fingerprint"), dict) else {}
        self.user_agent = str(self.config.get("user_agent") or fingerprint.get("user_agent") or DEFAULT_UA)
        self.sec_ch_ua = str(self.config.get("sec_ch_ua") or fingerprint.get("sec_ch_ua") or DEFAULT_SEC_CH_UA)
        self.platform = str(fingerprint.get("platform") or "Windows")
        self.platform_version = str(fingerprint.get("platform_version") or "")
        self.impersonate = str(self.config.get("impersonate") or fingerprint.get("impersonate") or impersonate_from_user_agent(self.user_agent, "chrome142"))
        self.device_id = str(self.config.get("device_id") or fingerprint.get("device_id") or uuid.uuid4())
        self.session = curl_requests.Session(impersonate=self.impersonate)
        proxies = build_requests_proxy_config(proxy)
        if proxies:
            self.session.proxies = proxies
        seed_oai_device_cookie(self.session, self.device_id)
        for cookie in self.config.get("cookies") or []:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if name and value:
                self.session.cookies.set(
                    name,
                    value,
                    domain=str(cookie.get("domain") or ".chatgpt.com"),
                    path=str(cookie.get("path") or "/"),
                )

    def run(
        self,
        oauth: OAuthSession,
        *,
        email: str,
        password: str,
        otp_provider: OAuthOtpAdapter,
        phone_provider=None,
    ) -> dict[str, Any]:
        self._log("OAuth RT: bootstrap oauth session")
        final_url = self._bootstrap_oauth(oauth)
        referer = final_url if final_url.startswith(AUTH_BASE) else f"{AUTH_BASE}/log-in"
        state = self._authorize_continue(email, referer)
        state_started_at = time.time()
        self._log(f"OAuth RT state: {describe_flow_state(state)}")
        seen: dict[tuple[str, str, str], int] = {}

        for _step in range(24):
            signature = (state.page_type, state.method, state.continue_url or state.current_url)
            seen[signature] = seen.get(signature, 0) + 1
            if seen[signature] > 3:
                raise RuntimeError(f"OAuth protocol state stuck: {describe_flow_state(state)}")

            code = _extract_code_from_state(state)
            if code:
                callback_url = state.continue_url or state.current_url
                return exchange_code(oauth, callback_url, user_agent=self.user_agent, proxy=self.proxy)

            if self._is_login_password(state):
                state = self._password_verify(password, state)
                state_started_at = time.time()
                self._log(f"OAuth RT state: {describe_flow_state(state)}")
                continue

            if self._is_email_otp(state):
                state = self._email_otp_validate(email, otp_provider, state)
                state_started_at = time.time()
                self._log(f"OAuth RT state: {describe_flow_state(state)}")
                continue

            if self._requires_navigation(state):
                code, state = self._follow_state(state)
                if code:
                    callback_url = state.continue_url or state.current_url
                    return exchange_code(oauth, callback_url, user_agent=self.user_agent, proxy=self.proxy)
                state_started_at = time.time()
                self._log(f"OAuth RT state: {describe_flow_state(state)}")
                continue

            if self._supports_workspace(state):
                code, state = self._workspace_select(state)
                if code:
                    callback_url = state.continue_url or state.current_url or f"{oauth.redirect_uri}?code={code}&state={oauth.state}"
                    return exchange_code(oauth, callback_url, user_agent=self.user_agent, proxy=self.proxy)
                state_started_at = time.time()
                self._log(f"OAuth RT state: {describe_flow_state(state)}")
                continue

            if self._is_add_phone(state):
                if phone_provider is None:
                    raise RuntimeError(f"phone verification required for OAuth RT: {describe_flow_state(state)}")
                state = self._verify_phone(phone_provider, state)
                state_started_at = time.time()
                self._log(f"OAuth RT state: {describe_flow_state(state)}")
                continue

            raise RuntimeError(f"unsupported OAuth protocol state: {describe_flow_state(state)}")

        raise RuntimeError("OAuth protocol exceeded max steps")

    def _headers(
        self,
        url: str,
        *,
        accept: str,
        referer: str = "",
        origin: str = "",
        content_type: str = "",
        navigation: bool = False,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        return build_browser_headers(
            url=url,
            user_agent=self.user_agent,
            sec_ch_ua=self.sec_ch_ua,
            chrome_full_version=str(self.config.get("chrome_full") or (self.config.get("browser_fingerprint") or {}).get("chrome_full") or _extract_chrome_full_version(self.user_agent)),
            sec_ch_ua_platform_version=self.platform_version,
            platform=self.platform,
            accept=accept,
            referer=referer,
            origin=origin,
            content_type=content_type,
            navigation=navigation,
            extra_headers=extra,
        )

    def _log(self, message: str) -> None:
        try:
            self.log_fn(str(message or ""))
        except Exception:
            pass

    def _bootstrap_oauth(self, oauth: OAuthSession) -> str:
        response = self.session.get(
            AUTHORIZE_URL,
            params=_query_params(oauth.auth_url),
            headers=self._headers(
                AUTHORIZE_URL,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer="https://chatgpt.com/",
                navigation=True,
            ),
            allow_redirects=True,
            timeout=45,
        )
        self._log(f"OAuth RT /oauth/authorize -> {response.status_code}, url={str(response.url)[:120]}")
        if response.status_code >= 400:
            raise RuntimeError(f"oauth bootstrap failed: HTTP {response.status_code} {response.text[:300]}")
        return str(response.url)

    def _sentinel(self, flow: str) -> str:
        token = build_sentinel_token(
            self.session,
            self.device_id,
            flow=flow,
            user_agent=self.user_agent,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
            logger=lambda msg: self._log(str(msg)),
        )
        if not token:
            raise RuntimeError(f"failed to build sentinel token for {flow}")
        return token

    def _authorize_continue(self, email: str, referer: str) -> FlowState:
        url = f"{AUTH_BASE}/api/accounts/authorize/continue"
        headers = self._headers(
            url,
            accept="application/json",
            referer=referer,
            origin=AUTH_BASE,
            content_type="application/json",
            extra={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel("authorize_continue"),
            },
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(
            url,
            json={"username": {"kind": "email", "value": email}, "screen_hint": "login"},
            headers=headers,
            allow_redirects=False,
            timeout=45,
        )
        self._log(f"OAuth RT authorize/continue -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"authorize/continue failed: HTTP {response.status_code} {response.text[:240]}")
        return extract_flow_state(response.json(), current_url=str(response.url))

    def _password_verify(self, password: str, state: FlowState) -> FlowState:
        url = f"{AUTH_BASE}/api/accounts/password/verify"
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{AUTH_BASE}/log-in/password",
            origin=AUTH_BASE,
            content_type="application/json",
            extra={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel("password_verify"),
            },
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(url, json={"password": password}, headers=headers, allow_redirects=False, timeout=45)
        self._log(f"OAuth RT password/verify -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"password/verify failed: HTTP {response.status_code} {response.text[:240]}")
        return extract_flow_state(response.json(), current_url=str(response.url))

    def _email_otp_validate(
        self,
        email: str,
        otp_provider: OAuthOtpAdapter,
        state: FlowState,
    ) -> FlowState:
        url = f"{AUTH_BASE}/api/accounts/email-otp/validate"
        code = otp_provider.get_code(email, otp_sent_at=time.time() - 30)
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{AUTH_BASE}/email-verification",
            origin=AUTH_BASE,
            content_type="application/json",
            extra={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel("email_otp_validate"),
            },
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(url, json={"code": code}, headers=headers, allow_redirects=False, timeout=45)
        self._log(f"OAuth RT email-otp/validate -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"email-otp/validate failed: HTTP {response.status_code} {response.text[:240]}")
        return extract_flow_state(response.json(), current_url=str(response.url))

    def _verify_phone(self, phone_provider, state: FlowState) -> FlowState:
        last_error = ""
        max_attempts = max(1, int(getattr(phone_provider, "max_attempts", 1) or 1))
        for attempt in range(1, max_attempts + 1):
            lease = None
            try:
                self._log(f"OAuth RT 手机验证尝试 {attempt}/{max_attempts}: 获取号码")
                lease = phone_provider.acquire_phone()
                phone_provider.prepare_for_sms(lease)
                self._send_phone_otp(lease.phone_number, state)
                code = phone_provider.wait_for_code(lease)
                if not code:
                    last_error = "接码超时"
                    phone_provider.mark_failure(lease, last_error)
                    continue
                next_state = self._phone_otp_validate(code, state)
                phone_provider.mark_success(lease)
                return next_state
            except Exception as exc:
                last_error = str(exc)
                if lease is not None:
                    try:
                        phone_provider.mark_failure(lease, last_error)
                    except Exception:
                        pass
                self._log(f"OAuth RT 手机验证失败: {last_error}")
        raise RuntimeError(last_error or "OAuth RT 手机验证失败")

    def _send_phone_otp(self, phone_number: str, state: FlowState) -> None:
        url = f"{AUTH_BASE}/api/accounts/add-phone/send"
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{AUTH_BASE}/add-phone",
            origin=AUTH_BASE,
            content_type="application/json",
            extra={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel("authorize_continue"),
            },
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(
            url,
            json={"phone_number": phone_number},
            headers=headers,
            allow_redirects=False,
            timeout=45,
        )
        self._log(f"OAuth RT add-phone/send -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"add-phone/send failed: HTTP {response.status_code} {response.text[:240]}")

    def _phone_otp_validate(self, code: str, state: FlowState) -> FlowState:
        url = f"{AUTH_BASE}/api/accounts/phone-otp/validate"
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{AUTH_BASE}/phone-verification",
            origin=AUTH_BASE,
            content_type="application/json",
            extra={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel("authorize_continue"),
            },
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(
            url,
            json={"code": str(code or "").strip()},
            headers=headers,
            allow_redirects=False,
            timeout=45,
        )
        self._log(f"OAuth RT phone-otp/validate -> {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"phone-otp/validate failed: HTTP {response.status_code} {response.text[:240]}")
        return extract_flow_state(response.json(), current_url=str(response.url))

    def _follow_state(self, state: FlowState, max_hops: int = 16) -> tuple[str, FlowState]:
        current_url = state.continue_url or state.current_url
        referer = state.current_url or ""
        last_url = current_url
        if not current_url:
            return "", state
        for hop in range(max_hops):
            code = _extract_code_from_url(current_url)
            if code:
                return code, _state_from_url(current_url)
            response = self.session.get(
                current_url,
                headers=self._headers(
                    current_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=referer,
                    navigation=True,
                ),
                allow_redirects=False,
                timeout=45,
            )
            last_url = str(response.url)
            self._log(f"OAuth RT follow[{hop + 1}] {response.status_code} {last_url[:120]}")
            code = _extract_code_from_url(last_url)
            if code:
                return code, _state_from_url(last_url)
            if response.status_code in (301, 302, 303, 307, 308):
                location = normalize_flow_url(response.headers.get("Location", ""), auth_base=AUTH_BASE)
                if not location:
                    return "", _state_from_url(last_url)
                code = _extract_code_from_url(location)
                if code:
                    return code, _state_from_url(location)
                referer = last_url
                current_url = location
                continue
            if "application/json" in (response.headers.get("content-type", "").lower()):
                try:
                    return "", extract_flow_state(response.json(), current_url=last_url)
                except Exception:
                    pass
            return "", _state_from_url(last_url)
        return "", _state_from_url(last_url)

    def _workspace_select(self, state: FlowState) -> tuple[str, FlowState]:
        consent_url = state.continue_url or state.current_url or f"{AUTH_BASE}/sign-in-with-chatgpt/codex/consent"
        session_data = self._load_workspace_session_data(consent_url)
        workspace = self._pick_workspace(session_data.get("workspaces") or [])
        if not workspace:
            raise RuntimeError("no workspace found in consent session")
        workspace_id = workspace.get("id")
        self._log(f"OAuth RT select workspace: {workspace_id}")
        url = f"{AUTH_BASE}/api/accounts/workspace/select"
        headers = self._headers(
            url,
            accept="application/json",
            referer=consent_url,
            origin=AUTH_BASE,
            content_type="application/json",
            extra={"oai-device-id": self.device_id},
        )
        headers.update(generate_datadog_trace())
        response = self.session.post(url, json={"workspace_id": workspace_id}, headers=headers, allow_redirects=False, timeout=45)
        self._log(f"OAuth RT workspace/select -> {response.status_code}")
        if response.status_code in (301, 302, 303, 307, 308):
            location = normalize_flow_url(response.headers.get("Location", ""), auth_base=AUTH_BASE)
            return _extract_code_from_url(location) or "", _state_from_url(location)
        if response.status_code != 200:
            raise RuntimeError(f"workspace/select failed: HTTP {response.status_code} {response.text[:240]}")
        next_state = extract_flow_state(response.json(), current_url=str(response.url))
        code = _extract_code_from_state(next_state)
        if code:
            return code, next_state
        if next_state.continue_url:
            return self._follow_state(next_state)
        return "", next_state

    def _load_workspace_session_data(self, consent_url: str) -> dict[str, Any]:
        api_data = self._dump_client_auth_session()
        if api_data.get("workspaces"):
            return api_data
        decoded = self._decode_auth_session_cookie()
        if decoded.get("workspaces"):
            return decoded
        response = self.session.get(
            consent_url,
            headers=self._headers(
                consent_url,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer=f"{AUTH_BASE}/email-verification",
                navigation=True,
            ),
            allow_redirects=False,
            timeout=45,
        )
        if response.status_code == 200:
            parsed = _extract_workspaces_from_html(response.text)
            if parsed.get("workspaces"):
                return parsed
        return api_data or decoded

    def _dump_client_auth_session(self) -> dict[str, Any]:
        url = f"{AUTH_BASE}/api/accounts/client_auth_session_dump"
        headers = self._headers(url, accept="application/json", referer=f"{AUTH_BASE}/email-verification")
        headers.update(generate_datadog_trace())
        try:
            response = self.session.get(url, headers=headers, allow_redirects=False, timeout=45)
        except Exception:
            return {}
        if response.status_code != 200:
            self._log(f"OAuth RT client_auth_session_dump -> {response.status_code}")
            return {}
        data = response.json() if response.content else {}
        session_data = data.get("client_auth_session") if isinstance(data.get("client_auth_session"), dict) else {}
        self._log(
            "OAuth RT client_auth_session_dump -> "
            f"email_verified={session_data.get('email_verified')}, "
            f"workspaces={len(session_data.get('workspaces') or [])}"
        )
        return session_data

    def _decode_auth_session_cookie(self) -> dict[str, Any]:
        for cookie in self.session.cookies.jar:
            if getattr(cookie, "name", "") != "oai-client-auth-session":
                continue
            parsed = _decode_cookie_json(getattr(cookie, "value", ""))
            if parsed:
                return parsed
        return {}

    @staticmethod
    def _pick_workspace(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
        for workspace in workspaces:
            kind = str(workspace.get("kind") or workspace.get("title") or workspace.get("name") or "").lower()
            if "personal" not in kind and workspace.get("id"):
                return workspace
        for workspace in workspaces:
            if workspace.get("id"):
                return workspace
        return {}

    @staticmethod
    def _is_login_password(state: FlowState) -> bool:
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "login_password" in target or "log-in/password" in target

    @staticmethod
    def _is_email_otp(state: FlowState) -> bool:
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "email_otp" in target or "email-verification" in target

    @staticmethod
    def _is_add_phone(state: FlowState) -> bool:
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "add_phone" in target or "add-phone" in target

    @staticmethod
    def _requires_navigation(state: FlowState) -> bool:
        if (state.method or "GET").upper() != "GET":
            return False
        if state.source == "api" and state.current_url and state.page_type not in {"login_password", "email_otp_verification"}:
            return True
        if state.continue_url and state.continue_url != state.current_url:
            return True
        return state.page_type in {"external_url", "callback"}

    def _supports_workspace(self, state: FlowState) -> bool:
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        if any(marker in target for marker in ("consent", "workspace", "organization", "sign-in-with-chatgpt")):
            return True
        return bool(self._decode_auth_session_cookie().get("workspaces"))


def run_protocol_oauth(
    oauth: OAuthSession,
    *,
    email: str,
    password: str,
    otp_provider: OAuthOtpAdapter,
    phone_provider=None,
    config: dict[str, Any] | None = None,
    proxy: str = "",
    log_fn=None,
) -> dict[str, Any]:
    client = ProtocolOAuthClient(config, proxy=proxy, log_fn=log_fn)
    return client.run(oauth, email=email, password=password, otp_provider=otp_provider, phone_provider=phone_provider)


def _query_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    return {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}


def _extract_code_from_url(url: str) -> str:
    if not url or "code=" not in url:
        return ""
    try:
        return (parse_qs(urlparse(url).query).get("code") or [""])[0]
    except Exception:
        return ""


def _extract_code_from_state(state: FlowState) -> str:
    for candidate in (state.continue_url, state.current_url, (state.payload or {}).get("url", "")):
        code = _extract_code_from_url(candidate)
        if code:
            return code
    return ""


def _state_from_url(url: str, *, method: str = "GET") -> FlowState:
    state = extract_flow_state(None, current_url=normalize_flow_url(url, auth_base=AUTH_BASE), default_method=method)
    state.method = str(method or "GET").upper()
    return state


def _decode_cookie_json(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    candidates = [raw]
    if "." in raw:
        candidates.insert(0, raw.split(".", 1)[0])
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        padded = candidate + "=" * (-len(candidate) % 4)
        for decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                parsed = json.loads(decoder(padded).decode("utf-8"))
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _extract_workspaces_from_html(html: str) -> dict[str, Any]:
    if not html or "workspaces" not in html:
        return {}
    normalized = html.replace('\\"', '"')
    start = normalized.find('"workspaces"')
    if start < 0:
        start = normalized.find("workspaces")
    if start < 0:
        return {}
    end = normalized.find('"openai_client_id"', start)
    if end < 0:
        end = min(len(normalized), start + 4000)
    chunk = normalized[start:end]
    ids = re.findall(r'"id"(?:,|:)"([0-9a-fA-F-]{36})"', chunk)
    kinds = re.findall(r'"kind"(?:,|:)"([^"]+)"', chunk)
    workspaces = []
    seen = set()
    for idx, wid in enumerate(ids):
        if wid in seen:
            continue
        seen.add(wid)
        item = {"id": wid}
        if idx < len(kinds):
            item["kind"] = kinds[idx]
        workspaces.append(item)
    return {"workspaces": workspaces} if workspaces else {}


def _extract_chrome_full_version(user_agent: str) -> str:
    match = re.search(r"Chrome/([0-9.]+)", str(user_agent or ""))
    return match.group(1) if match else ""
