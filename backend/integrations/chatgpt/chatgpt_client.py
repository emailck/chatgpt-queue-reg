"""
ChatGPT 注册客户端模块
使用 curl_cffi 模拟浏览器行为
"""

import uuid
import time
from dataclasses import asdict
from urllib.parse import urlparse
from backend.core.proxy import build_requests_proxy_config

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    print("[FAIL] 需要安装 curl_cffi: pip install curl_cffi")
    import sys

    sys.exit(1)

from .sentinel_token import build_sentinel_token
from .sentinel_browser import get_sentinel_token_via_browser, get_sentinel_token_via_quickjs
from .utils import (
    FlowState,
    build_browser_headers,
    decode_jwt_payload,
    describe_flow_state,
    describe_request_fingerprint,
    extract_flow_state,
    generate_datadog_trace,
    normalize_flow_url,
    random_delay,
    seed_oai_device_cookie,
)
from .fingerprint import normalize_impersonate, random_fingerprint, BrowserFingerprint
from .backend_headers import (
    compute_passkey_capabilities,
    extract_oai_client_versions_from_homepage_html,
)
from .chatgpt_entry_bootstrap import bootstrap_chatgpt_entry


class ChatGPTClient:
    """ChatGPT 注册客户端"""

    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy=None, verbose=True, browser_mode="protocol"):
        """
        初始化 ChatGPT 客户端

        Args:
            proxy: 代理地址
            verbose: 是否输出详细日志
            browser_mode: protocol | headless | headed
        """
        self.proxy = proxy
        self.verbose = verbose
        self.browser_mode = browser_mode or "protocol"
        self.device_id = str(uuid.uuid4())
        self.oai_session_id = str(uuid.uuid4())
        self.oai_client_version = ""
        self.oai_client_build_number = ""
        fp = random_fingerprint()
        self._apply_fingerprint(fp)

        # 创建 session
        self.session = curl_requests.Session(impersonate=self.impersonate)

        if self.proxy:
            self.session.proxies = build_requests_proxy_config(self.proxy)

        # 设置基础 headers
        self.session.headers.update(fp.base_headers())

        # 设置 oai-did cookie
        seed_oai_device_cookie(self.session, self.device_id)
        try:
            self.session.oai_client_version = ""
            self.session.oai_client_build_number = ""
        except Exception:
            pass
        self.last_registration_state = FlowState()
        self.last_stage = ""
        self.last_client_auth_session_id = ""
        self.last_client_auth_session_dump = {}

    def _sync_oai_client_versions(self, html: str | None) -> tuple[str, str]:
        version, build_number = extract_oai_client_versions_from_homepage_html(html)
        if version:
            self.oai_client_version = version
        if build_number:
            self.oai_client_build_number = build_number
        try:
            self.session.oai_client_version = self.oai_client_version
            self.session.oai_client_build_number = self.oai_client_build_number
        except Exception:
            pass
        return version, build_number

    def _apply_fingerprint(self, fp: BrowserFingerprint):
        """统一应用一个 BrowserFingerprint 到当前 session 所有状态。"""
        self._fingerprint = fp
        self.impersonate = normalize_impersonate(fp.impersonate)
        self.chrome_major = fp.chrome_major
        self.chrome_full = fp.chrome_full
        self.ua = fp.user_agent
        self.sec_ch_ua = fp.sec_ch_ua
        self.accept_language = fp.accept_language
        self.sec_ch_ua_platform_version = fp.platform_version
        self.is_firefox = fp.is_firefox
        self.viewport_width = fp.viewport_width
        self.viewport_height = fp.viewport_height

    def _get_sentinel_token(
        self,
        flow: str,
        *,
        page_url: str | None = None,
        browser_attempts: int | None = None,
        allow_http_fallback: bool | None = None,
    ):
        """获取 Sentinel token，返回 (token_str, so_token_str | None)。

        优先级: Camoufox 混合 → Playwright → QuickJS → 纯 Python PoW。
        Camoufox 注入协议层 UA/viewport/cookie 保证指纹一致性。
        """
        strict_browser_flow = flow in {"username_password_create", "oauth_create_account"}
        if browser_attempts is None:
            browser_attempts = 3 if strict_browser_flow else 1
        if allow_http_fallback is None:
            allow_http_fallback = not strict_browser_flow

        # 1. 优先 Camoufox / Playwright（真实浏览器指纹 + Turnstile）
        use_browser = self.browser_mode in ("protocol", "headless", "headed")
        if use_browser:
            for attempt in range(1, browser_attempts + 1):
                if browser_attempts > 1:
                    self._log(f"{flow}: Sentinel Browser 尝试 {attempt}/{browser_attempts}")
                try:
                    token = get_sentinel_token_via_browser(
                        flow=flow,
                        proxy=self.proxy,
                        page_url=page_url,
                        headless=self.browser_mode != "headed",
                        device_id=self.device_id,
                        impersonate=self.impersonate,
                        user_agent=self.ua,
                        viewport_width=self.viewport_width,
                        viewport_height=self.viewport_height,
                        protocol_session=self.session,
                        log_fn=lambda msg: self._log(msg),
                    )
                except Exception as exc:
                    self._log(f"{flow}: Sentinel Browser 异常: {exc}")
                    token = None
                if token:
                    self._log(f"{flow}: 已通过浏览器 SentinelSDK 获取 token")
                    return str(token), getattr(token, "so_token", None) or None
                if attempt < browser_attempts:
                    self._log(f"{flow}: Sentinel Browser 未获取 token，准备重试")
                    time.sleep(min(2 * attempt, 5))
            if not allow_http_fallback:
                self._log(f"{flow}: Sentinel Browser 连续失败，停止降级")
                return None, None
            self._log(f"{flow}: Sentinel Browser 失败，降级到纯协议")
        else:
            self._log(f"{flow}: 跳过浏览器模式 (mode={self.browser_mode})")
            if not allow_http_fallback:
                return None, None

        # 2. QuickJS fallback
        qj_result = None
        try:
            qj_result = get_sentinel_token_via_quickjs(
                flow=flow,
                proxy=self.proxy,
                device_id=self.device_id,
                impersonate=self.impersonate,
                user_agent=self.ua,
                accept_language=self.accept_language,
                viewport_width=self.viewport_width,
                viewport_height=self.viewport_height,
                timeout_ms=12000,
                logger=lambda msg: self._log(msg),
            )
        except Exception as exc:
            self._log(f"{flow}: Sentinel QuickJS 异常: {exc}")
        if qj_result and qj_result.get("token"):
            self._log(
                f"{flow}: Sentinel QuickJS 成功 (fallback)"
                f" (t=OK so={'OK' if qj_result.get('so_token') else 'X'})"
            )
            return qj_result["token"], qj_result.get("so_token") or None

        # 3. 纯 Python PoW（最后手段，无 so）
        token = build_sentinel_token(
            self.session,
            self.device_id,
            flow=flow,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
            platform=self._fingerprint.platform,
            is_firefox=self.is_firefox,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            logger=lambda msg: self._log(msg),
        )
        if token:
            self._log(f"{flow}: 已通过 HTTP PoW 获取 token (无 so)")
        return token, None

    def _log(self, msg):
        """输出日志"""
        if self.verbose:
            print(f"  {msg}")

    def _enter_stage(self, stage: str, detail: str = ""):
        self.last_stage = str(stage or "").strip()
        if self.last_stage:
            message = f"[stage={self.last_stage}]"
            if detail:
                message += f" {detail}"
            self._log(message)

    def _browser_pause(self, low=0.15, high=0.45):
        """在 headed 模式下加入轻微停顿，模拟有头浏览器节奏。"""
        if self.browser_mode == "headed":
            random_delay(low, high)

    def _headers(
        self,
        url,
        *,
        accept,
        referer=None,
        origin=None,
        content_type=None,
        navigation=False,
        fetch_mode=None,
        fetch_dest=None,
        fetch_site=None,
        extra_headers=None,
    ):
        return build_browser_headers(
            url=url,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            chrome_full_version=self.chrome_full,
            sec_ch_ua_platform_version=getattr(self, "sec_ch_ua_platform_version", None),
            platform=self._fingerprint.platform,
            accept=accept,
            accept_language=self.accept_language,
            referer=referer,
            origin=origin,
            content_type=content_type,
            navigation=navigation,
            fetch_mode=fetch_mode,
            fetch_dest=fetch_dest,
            fetch_site=fetch_site,
            headed=self.browser_mode == "headed",
            extra_headers=extra_headers,
        )

    @staticmethod
    def _is_tls_connect_error(exc):
        text = str(exc or "").lower()
        return (
            "curl: (35)" in text
            or "tls connect error" in text
            or "openssl_internal" in text
            or "invalid library (0)" in text
        )

    def _session_request(self, method, url, **kwargs):
        self.impersonate = normalize_impersonate(kwargs.get("impersonate") or self.impersonate)
        kwargs["impersonate"] = self.impersonate
        max_retries = 4
        initial_delay = 1.5
        for attempt in range(1, max_retries + 2):
            try:
                headers = kwargs.get("headers") or {}
                fp = describe_request_fingerprint(
                    user_agent=headers.get('User-Agent') or headers.get('user-agent') or self.ua,
                    sec_ch_ua=headers.get('sec-ch-ua') or headers.get('Sec-Ch-Ua') or self.sec_ch_ua,
                    impersonate=self.impersonate,
                )
                self._log(
                    f"HTTP {str(method).upper()} {str(url)} "
                    f"{fp}"
                )
                response = getattr(self.session, method)(url, **kwargs)
                try:
                    target_url = str(getattr(response, "url", "") or url or "")
                    if urlparse(target_url).netloc.endswith("chatgpt.com"):
                        self._sync_oai_client_versions(getattr(response, "text", ""))
                except Exception:
                    pass
                return response
            except Exception as exc:
                if not self._is_tls_connect_error(exc) or attempt > max_retries:
                    self._log(
                        f"HTTP {str(method).upper()} {str(url)[:160]} 异常: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    response = getattr(exc, "response", None)
                    if response is not None:
                        self._log_response_debug("HTTP 异常响应", response)
                    raise
                host = urlparse(str(url or "")).netloc or str(url or "")[:80]
                delay = initial_delay * (2 ** (attempt - 1))
                self._log(
                    "TLS 连接错误，重置 HTTP session 后重试 "
                    f"{attempt}/{max_retries}，等待 {delay:g}s: {host}"
                )
                try:
                    self.session.close()
                except Exception:
                    pass
                time.sleep(delay)
        raise RuntimeError("unreachable TLS retry state")

    def _session_get(self, url, **kwargs):
        return self._session_request("get", url, **kwargs)

    def _session_post(self, url, **kwargs):
        return self._session_request("post", url, **kwargs)

    def _log_response_debug(self, label: str, response) -> None:
        try:
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
            server = str(headers.get("server") or headers.get("Server") or "")
            cf_ray = str(headers.get("cf-ray") or headers.get("CF-Ray") or "")
            location = str(headers.get("location") or headers.get("Location") or "")
            text = str(getattr(response, "text", "") or "")
            compact = " ".join(text.replace("\r", " ").replace("\n", " ").split())[:800]
            self._log(
                f"{label}: status={getattr(response, 'status_code', '-') } "
                f"url={str(getattr(response, 'url', '') or '')[:160]} "
                f"content_type={content_type[:80] or '-'} server={server[:80] or '-'} "
                f"cf_ray={cf_ray[:80] or '-'} location={location[:160] or '-'}"
            )
            if compact:
                self._log(f"{label}: body={compact}")
        except Exception as exc:
            self._log(f"{label}: 响应调试日志异常: {exc}")

    def _reset_session(self):
        """重置浏览器指纹与会话，用于绕过偶发的 Cloudflare/SPA 中间页。"""
        self.device_id = str(uuid.uuid4())
        self.oai_client_version = ""
        self.oai_client_build_number = ""
        fp = random_fingerprint()
        self._apply_fingerprint(fp)

        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = build_requests_proxy_config(self.proxy)

        self.session.headers.update(fp.base_headers())
        seed_oai_device_cookie(self.session, self.device_id)
        try:
            self.session.oai_client_version = ""
            self.session.oai_client_build_number = ""
        except Exception:
            pass

    def _state_from_url(self, url, method="GET"):
        state = extract_flow_state(
            current_url=normalize_flow_url(url, auth_base=self.AUTH),
            auth_base=self.AUTH,
            default_method=method,
        )
        if method:
            state.method = str(method).upper()
        return state

    def _state_from_payload(self, data, current_url=""):
        return extract_flow_state(
            data=data,
            current_url=current_url,
            auth_base=self.AUTH,
        )

    def _state_signature(self, state: FlowState):
        return (
            state.page_type or "",
            state.method or "",
            state.continue_url or "",
            state.current_url or "",
        )

    def _is_registration_complete_state(self, state: FlowState):
        current_url = (state.current_url or "").lower()
        continue_url = (state.continue_url or "").lower()
        page_type = state.page_type or ""
        return (
            page_type in {"callback", "chatgpt_home", "oauth_callback"}
            or (
                "chatgpt.com" in current_url
                and "redirect_uri" not in current_url
                and page_type != "external_url"
            )
            or (
                "chatgpt.com" in continue_url
                and "redirect_uri" not in continue_url
                and page_type != "external_url"
            )
        )

    def _state_is_password_registration(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return (
            state.page_type in {"create_account_password", "password"}
            and "/create-account/password" in target
        )

    def _state_is_email_otp(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return (
            state.page_type == "email_otp_verification"
            or "email-verification" in target
            or "email-otp" in target
        )

    def _state_is_about_you(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "about_you" or "about-you" in target

    def _state_is_add_phone(self, state: FlowState):
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "add_phone" in target or "add-phone" in target or "phone-verification" in target

    def _state_is_identity_verification(self, state: FlowState):
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "identity_verification" in target or "identity-verification" in target

    def _state_requires_navigation(self, state: FlowState):
        if (state.method or "GET").upper() != "GET":
            return False
        if state.page_type == "external_url" and state.continue_url:
            return True
        if state.continue_url and state.continue_url != state.current_url:
            return True
        return False

    def _follow_flow_state(self, state: FlowState, referer=None):
        """跟随服务端返回的 continue_url，推进注册状态机。"""
        target_url = state.continue_url or state.current_url
        if not target_url:
            return False, "缺少可跟随的 continue_url"

        try:
            self._browser_pause()
            r = self._session_get(
                target_url,
                headers=self._headers(
                    target_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=referer,
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            final_url = str(r.url)
            self._log(f"follow -> {r.status_code} {final_url}")

            content_type = (r.headers.get("content-type", "") or "").lower()
            if "application/json" in content_type:
                try:
                    next_state = self._state_from_payload(
                        r.json(), current_url=final_url
                    )
                except Exception:
                    next_state = self._state_from_url(final_url)
            else:
                next_state = self._state_from_url(final_url)

            self._log(f"follow state -> {describe_flow_state(next_state)}")
            return True, next_state
        except Exception as e:
            self._log(f"跟随 continue_url 失败: {e}")
            return False, str(e)

    def export_identity_state(self) -> dict:
        cookies = []
        for cookie in self.session.cookies.jar:
            cookies.append({
                "name": getattr(cookie, "name", ""),
                "value": getattr(cookie, "value", ""),
                "domain": getattr(cookie, "domain", "") or ".chatgpt.com",
                "path": getattr(cookie, "path", "") or "/",
                "expires": getattr(cookie, "expires", None),
                "secure": bool(getattr(cookie, "secure", False)),
                "httpOnly": bool(getattr(cookie, "has_nonstandard_attr", lambda _name: False)("HttpOnly")),
            })
        return {
            "user_agent": self.ua,
            "browser_fingerprint": asdict(self._fingerprint),
            "cookies": cookies,
            "local_storage": {},
            "device_id": self.device_id,
            "oai_session_id": self.oai_session_id,
            "impersonate": self.impersonate,
            "oai_client_version": self.oai_client_version,
            "oai_client_build_number": self.oai_client_build_number,
        }

    def _get_cookie_value(self, name, domain_hint=None):
        """读取当前会话中的 Cookie。"""
        for cookie in self.session.cookies.jar:
            if cookie.name != name:
                continue
            if domain_hint and domain_hint not in (cookie.domain or ""):
                continue
            return cookie.value
        return ""

    def get_next_auth_session_token(self):
        """获取 ChatGPT next-auth 会话 Cookie。"""
        direct = (
            self._get_cookie_value("__Secure-next-auth.session-token", "chatgpt.com")
            or self._get_cookie_value("__Secure-authjs.session-token", "chatgpt.com")
        )
        if direct:
            return direct
        for prefix in (
            "__Secure-next-auth.session-token.",
            "__Secure-authjs.session-token.",
        ):
            chunks = []
            for cookie in self.session.cookies.jar:
                if not cookie.name.startswith(prefix):
                    continue
                if "chatgpt.com" not in (cookie.domain or ""):
                    continue
                suffix = cookie.name.rsplit(".", 1)[-1]
                try:
                    order = int(suffix)
                except Exception:
                    continue
                chunks.append((order, cookie.value))
            if chunks:
                return "".join(value for _, value in sorted(chunks))
        return ""

    def fetch_chatgpt_session(self, max_attempts=5, retry_delay=1.2):
        """请求 ChatGPT Session 接口并返回原始会话数据。"""
        url = f"{self.BASE}/api/auth/session"
        last_error = ""

        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                self._browser_pause()
                response = self._session_get(
                    url,
                    headers=self._headers(
                        url,
                        accept="application/json",
                        referer=f"{self.BASE}/",
                        fetch_site="same-origin",
                    ),
                    timeout=30,
                )
            except Exception as exc:
                last_error = f"/api/auth/session 请求异常: {exc}"
                if attempt < max_attempts - 1:
                    self._log(
                        f"{last_error}，等待 {retry_delay:.1f}s 后重试 "
                        f"({attempt + 1}/{max_attempts})"
                    )
                    time.sleep(retry_delay)
                    continue
                return False, last_error

            if response.status_code != 200:
                last_error = f"/api/auth/session -> HTTP {response.status_code}"
                if attempt < max_attempts - 1:
                    self._log(
                        f"{last_error}，等待 {retry_delay:.1f}s 后重试 "
                        f"({attempt + 1}/{max_attempts})"
                    )
                    time.sleep(retry_delay)
                    continue
                return False, last_error

            try:
                data = response.json()
            except Exception as exc:
                last_error = f"/api/auth/session 返回非 JSON: {exc}"
                if attempt < max_attempts - 1:
                    self._log(
                        f"{last_error}，等待 {retry_delay:.1f}s 后重试 "
                        f"({attempt + 1}/{max_attempts})"
                    )
                    time.sleep(retry_delay)
                    continue
                return False, last_error

            access_token = str(data.get("accessToken") or "").strip()
            if access_token:
                return True, data

            last_error = "/api/auth/session 未返回 accessToken"
            if attempt < max_attempts - 1:
                self._log(
                    f"{last_error}，等待 {retry_delay:.1f}s 后重试 "
                    f"({attempt + 1}/{max_attempts})"
                )
                try:
                    self._session_get(
                        f"{self.BASE}/",
                        headers=self._headers(
                            f"{self.BASE}/",
                            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            referer=f"{self.BASE}/",
                            navigation=True,
                        ),
                        allow_redirects=True,
                        timeout=30,
                    )
                except Exception:
                    pass
                time.sleep(retry_delay)
                continue

            return False, last_error

        return False, last_error or "/api/auth/session 未返回 accessToken"

    def fetch_backend_me(self, access_token, max_attempts=2, retry_delay=1.0):
        url = f"{self.BASE}/backend-api/me"
        token = str(access_token or "").strip()
        if not token:
            return False, "missing access_token"
        last_error = ""
        for attempt in range(max(1, int(max_attempts or 1))):
            try:
                response = self._session_get(
                    url,
                    headers=self._headers(
                        url,
                        accept="application/json, text/plain, */*",
                        referer=f"{self.BASE}/",
                        content_type="application/json",
                        fetch_site="same-origin",
                        extra_headers={"Authorization": f"Bearer {token}"},
                    ),
                    timeout=30,
                )
            except Exception as exc:
                last_error = f"/backend-api/me 请求异常: {exc}"
                if attempt < max_attempts - 1:
                    time.sleep(retry_delay)
                    continue
                return False, last_error
            if response.status_code == 200:
                try:
                    data = response.json()
                except Exception as exc:
                    return False, f"/backend-api/me 返回非 JSON: {exc}"
                return True, data if isinstance(data, dict) else {}
            last_error = f"/backend-api/me -> HTTP {response.status_code}: {str(response.text or '')[:240]}"
            if attempt < max_attempts - 1 and response.status_code >= 500:
                time.sleep(retry_delay)
                continue
            return False, last_error
        return False, last_error or "/backend-api/me 校验失败"

    def _fetch_session_with_cookie_token(self, max_attempts=3):
        ok, session_or_error = self.fetch_chatgpt_session(max_attempts=max_attempts)
        if not ok:
            return False, session_or_error
        data = dict(session_or_error or {})
        session_cookie = self.get_next_auth_session_token()
        if session_cookie and not str(data.get("sessionToken") or "").strip():
            data["sessionToken"] = session_cookie
        return True, data

    def relogin_existing_user(self, email, password="", otp_provider=None, max_steps=16):
        self._enter_stage("login", f"email={email}")
        if not email:
            return False, "missing email"
        if otp_provider is None and not password:
            return False, "missing otp provider"
        csrf_token = self.get_csrf_token()
        if not csrf_token:
            return False, "获取 CSRF token 失败"
        authorize_url = self.signin(email, csrf_token)
        if not authorize_url:
            return False, "signin/openai 未返回 authorize URL"
        final_url = self.authorize(authorize_url)
        if not final_url:
            return False, "authorize 跳转失败"
        state = self._state_from_url(final_url)
        otp_sent_at = 0.0
        self._log(f"登录状态起点: {describe_flow_state(state)}")
        seen = {}
        for _ in range(max(1, int(max_steps or 1))):
            signature = self._state_signature(state)
            seen[signature] = seen.get(signature, 0) + 1
            if seen[signature] > 3:
                return False, f"登录状态卡住: {describe_flow_state(state)}"
            if self._is_registration_complete_state(state):
                self.last_registration_state = state
                return self._fetch_session_with_cookie_token(max_attempts=3)
            if self._state_is_login_start(state):
                ok, next_state = self.authorize_continue_login(email, state, return_state=True)
                if not ok:
                    return False, f"登录邮箱提交失败: {next_state}"
                state = next_state
                self._log(f"登录状态: {describe_flow_state(state)}")
                continue
            if self._state_is_login_password(state):
                if otp_provider is not None:
                    ok, next_state = self.request_login_email_otp(state, return_state=True)
                    if not ok:
                        return False, f"登录邮箱验证码发送失败: {next_state}"
                    otp_sent_at = time.time()
                    state = next_state
                    self._log(f"登录状态: {describe_flow_state(state)}")
                    continue
                ok, next_state = self.verify_login_password(password, state, return_state=True)
                if not ok:
                    return False, f"登录密码验证失败: {next_state}"
                state = next_state
                self._log(f"登录状态: {describe_flow_state(state)}")
                continue
            if self._state_is_email_otp(state):
                if otp_provider is None:
                    return False, "登录需要邮箱验证码，但未提供邮箱取码服务"
                if not otp_sent_at:
                    otp_sent_at = time.time() - 30
                code = self._get_login_otp_code(otp_provider, email, timeout=300, otp_sent_at=otp_sent_at)
                if not code:
                    return False, "登录邮箱验证码获取失败"
                ok, next_state = self.verify_email_otp(code, return_state=True)
                if not ok:
                    return False, f"登录邮箱验证码验证失败: {next_state}"
                state = next_state
                self._log(f"登录状态: {describe_flow_state(state)}")
                continue
            if self._state_requires_navigation(state):
                ok, next_state = self._follow_flow_state(state, referer=state.current_url or f"{self.AUTH}/log-in")
                if not ok:
                    return False, f"登录跳转失败: {next_state}"
                state = next_state
                self._log(f"登录状态: {describe_flow_state(state)}")
                continue
            if self._state_is_add_phone(state):
                return False, "登录需要手机验证"
            return False, f"未支持的登录状态: {describe_flow_state(state)}"
        return False, "登录状态机超出最大步数"

    def _protocol_sentinel_token(self, flow):
        return build_sentinel_token(
            self.session,
            self.device_id,
            flow=flow,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
            platform=self._fingerprint.platform,
            is_firefox=self.is_firefox,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            logger=lambda msg: self._log(msg),
        )

    def authorize_continue_login(self, email, state, return_state=False):
        url = f"{self.AUTH}/api/accounts/authorize/continue"
        sentinel_token = self._protocol_sentinel_token("authorize_continue")
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{self.AUTH}/log-in",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel_token,
            },
        )
        headers.update(generate_datadog_trace())
        try:
            response = self._session_post(
                url,
                json={"username": {"kind": "email", "value": email}, "screen_hint": "login"},
                headers=headers,
                allow_redirects=False,
                timeout=45,
            )
        except Exception as exc:
            return False, str(exc)
        self._log(f"authorize/continue -> {response.status_code}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:240]}"
        try:
            next_state = self._state_from_payload(response.json(), current_url=str(response.url) or url)
        except Exception as exc:
            return False, f"authorize/continue 返回非 JSON: {exc}"
        return (True, next_state) if return_state else (True, "ok")

    def request_login_email_otp(self, state, return_state=False):
        url = f"{self.AUTH}/api/accounts/email-otp/send"
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{self.AUTH}/log-in/password",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={"oai-device-id": self.device_id},
        )
        headers.update(generate_datadog_trace())
        try:
            response = self._session_get(url, headers=headers, allow_redirects=False, timeout=45)
        except Exception as exc:
            return False, str(exc)
        self._log(f"email-otp/send -> {response.status_code}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:240]}"
        try:
            payload = response.json()
        except Exception:
            payload = {}
        next_state = self._state_from_payload(payload if isinstance(payload, dict) else {}, current_url=str(response.url) or f"{self.AUTH}/email-verification")
        if not self._state_is_email_otp(next_state):
            next_state = self._state_from_url(f"{self.AUTH}/email-verification")
        return (True, next_state) if return_state else (True, "ok")

    @staticmethod
    def _get_login_otp_code(otp_provider, email, *, timeout, otp_sent_at):
        wait_fn = getattr(otp_provider, "wait_for_verification_code", None)
        if callable(wait_fn):
            return wait_fn(email, timeout=timeout, otp_sent_at=otp_sent_at)
        get_fn = getattr(otp_provider, "get_verification_code", None)
        if callable(get_fn):
            return get_fn(email=email, timeout=timeout, otp_sent_at=otp_sent_at)
        return ""

    def verify_login_password(self, password, state, return_state=False):
        url = f"{self.AUTH}/api/accounts/password/verify"
        sentinel_token = self._protocol_sentinel_token("password_verify")
        headers = self._headers(
            url,
            accept="application/json",
            referer=state.current_url or state.continue_url or f"{self.AUTH}/log-in/password",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel_token,
            },
        )
        headers.update(generate_datadog_trace())
        try:
            response = self._session_post(
                url,
                json={"password": password},
                headers=headers,
                allow_redirects=False,
                timeout=45,
            )
        except Exception as exc:
            return False, str(exc)
        self._log(f"password/verify -> {response.status_code}")
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:240]}"
        try:
            next_state = self._state_from_payload(response.json(), current_url=str(response.url) or url)
        except Exception as exc:
            return False, f"password/verify 返回非 JSON: {exc}"
        return (True, next_state) if return_state else (True, "ok")

    @staticmethod
    def _state_is_login_start(state: FlowState):
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return ("log_in" in target or "log-in" in target) and "password" not in target

    @staticmethod
    def _state_is_login_password(state: FlowState):
        target = f"{state.page_type} {state.continue_url} {state.current_url}".lower()
        return "login_password" in target or "log-in/password" in target

    def _callback_landed_enough_for_session(self) -> bool:
        """Check whether the ChatGPT session landed despite a flaky callback response."""
        if self.get_next_auth_session_token():
            return True
        ok, _ = self.fetch_chatgpt_session(max_attempts=1, retry_delay=0)
        return bool(ok)

    def reuse_session_and_get_tokens(self):
        """
        承接前序阶段已建立的 ChatGPT 会话，直接读取 Session / AccessToken。

        Returns:
            tuple[bool, dict|str]: 成功时返回标准化 token/session 数据；失败时返回错误信息。
        """
        self._enter_stage("token_exchange", "reuse session -> /api/auth/session")
        state = self.last_registration_state or FlowState()
        self._log("步骤 1/4: 跟随注册回调 external_url ...")
        if state.page_type == "external_url" or self._state_requires_navigation(state):
            ok, followed_result = self._follow_flow_state(
                state,
                referer=state.current_url or f"{self.AUTH}/about-you",
            )
            if ok:
                followed = followed_result
            elif self._callback_landed_enough_for_session():
                self._log(
                    "注册回调响应中断，但 ChatGPT session 已落地，继续提取 token"
                )
                followed = self._state_from_url(f"{self.BASE}/")
            else:
                return False, f"注册回调落地失败: {str(followed_result or '')}"

            state = followed
            self.last_registration_state = followed
        else:
            self._log("注册回调已落地，跳过额外跟随")

        self._log("步骤 2/4: 检查 __Secure-next-auth.session-token ...")
        session_cookie = ""
        for attempt in range(5):
            session_cookie = self.get_next_auth_session_token()
            if session_cookie:
                break
            self._log(
                f"next-auth session cookie 尚未落地，补一次 ChatGPT 首页触达 "
                f"({attempt + 1}/5)"
            )
            try:
                self._browser_pause(0.2, 0.5)
                self._session_get(
                    f"{self.BASE}/",
                    headers=self._headers(
                        f"{self.BASE}/",
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=state.current_url or f"{self.AUTH}/about-you",
                        navigation=True,
                    ),
                    allow_redirects=True,
                    timeout=30,
                )
            except Exception as exc:
                self._log(f"补触达 ChatGPT 首页异常: {exc}")
            time.sleep(1.0)
        if not session_cookie:
            self._log("未检测到 session-token cookie，继续直访 /api/auth/session 尝试提取 accessToken")

        self._log("步骤 3/4: 请求 ChatGPT /api/auth/session ...")
        ok, session_or_error = self.fetch_chatgpt_session()
        if not ok:
            return False, session_or_error

        session_data = session_or_error
        access_token = str(session_data.get("accessToken") or "").strip()
        session_token = str(
            session_data.get("sessionToken") or session_cookie or ""
        ).strip()
        user = session_data.get("user") or {}
        account = session_data.get("account") or {}
        jwt_payload = decode_jwt_payload(access_token)
        auth_payload = jwt_payload.get("https://api.openai.com/auth") or {}

        account_id = (
            str(account.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_account_id") or "").strip()
        )
        user_id = (
            str(user.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_user_id") or "").strip()
            or str(auth_payload.get("user_id") or "").strip()
        )

        normalized = {
            "access_token": access_token,
            "session_token": session_token,
            "account_id": account_id,
            "user_id": user_id,
            "workspace_id": account_id,
            "expires": session_data.get("expires"),
            "user": user,
            "account": account,
            "auth_provider": session_data.get("authProvider"),
            "raw_session": session_data,
        }

        self._log("步骤 4/4: 已从当前会话中提取 accessToken")
        if account_id:
            self._log(f"Session Account ID: {account_id}")
        if user_id:
            self._log(f"Session User ID: {user_id}")
        return True, normalized

    def visit_homepage(self):
        """访问首页，建立 session"""
        self._log("访问 ChatGPT 首页...")
        url = f"{self.BASE}/"
        self.last_homepage_status = 0
        self.last_homepage_url = ""
        try:
            self._browser_pause()
            r = self._session_get(
                url,
                headers=self._headers(
                    url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            self.last_homepage_status = int(r.status_code or 0)
            self.last_homepage_url = str(r.url or "")
            version_before = self.oai_client_version
            build_before = self.oai_client_build_number
            self._sync_oai_client_versions(getattr(r, "text", ""))
            if (
                self.oai_client_version and self.oai_client_version != version_before
            ) or (
                self.oai_client_build_number
                and self.oai_client_build_number != build_before
            ):
                self._log(
                    "首页 OAI 版本: "
                    f"version={self.oai_client_version}, "
                    f"build={self.oai_client_build_number}"
                )
            self._log(
                f"首页响应: status={self.last_homepage_status} url={self.last_homepage_url[:120]}"
            )
            if 200 <= r.status_code < 400:
                return True
            self._log(
                f"首页非成功状态，继续尝试 CSRF 探测: status={r.status_code} body={r.text[:120]}"
            )
            return False
        except Exception as e:
            self._log(f"访问首页失败: {e}")
            return False

    def get_csrf_token(self):
        """获取 CSRF token"""
        self._log("获取 CSRF token...")
        url = f"{self.BASE}/api/auth/csrf"
        try:
            r = self._session_get(
                url,
                headers=self._headers(
                    url,
                    accept="*/*",
                    referer=f"{self.BASE}/",
                    content_type="application/json",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )

            if r.status_code == 200:
                data = r.json()
                token = data.get("csrfToken", "")
                if token:
                    self._log(f"CSRF token: {token[:20]}...")
                    return token
        except Exception as e:
            self._log(f"获取 CSRF token 失败: {e}")

        return None

    def signin(self, email, csrf_token):
        """
        提交邮箱，获取 authorize URL

        Returns:
            str: authorize URL
        """
        self._log(f"提交邮箱: {email}")
        url = f"{self.BASE}/api/auth/signin/openai"

        params = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": compute_passkey_capabilities(self.ua),
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }

        form_data = {
            "callbackUrl": f"{self.BASE}/",
            "csrfToken": csrf_token,
            "json": "true",
        }

        try:
            self._browser_pause()
            r = self._session_post(
                url,
                params=params,
                data=form_data,
                headers=self._headers(
                    url,
                    accept="*/*",
                    referer=f"{self.BASE}/",
                    origin=self.BASE,
                    content_type="application/x-www-form-urlencoded",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )

            if r.status_code == 200:
                data = r.json()
                authorize_url = data.get("url", "")
                if authorize_url:
                    self._log(f"获取到 authorize URL")
                    return authorize_url
        except Exception as e:
            self._log(f"提交邮箱失败: {e}")

        return None

    def _bootstrap_chatgpt_entry(self, email, csrf_token=""):
        """完成 HAR 对齐的 ChatGPT 入口预热并返回 authorize 最终 URL。"""
        return bootstrap_chatgpt_entry(
            self,
            email,
            self.device_id,
            csrf_token=csrf_token,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
        )

    def authorize(self, url, max_retries=3):
        """
        访问 authorize URL，跟随重定向（带重试机制）
        这是关键步骤，建立 auth.openai.com 的 session

        Returns:
            str: 最终重定向的 URL
        """
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self._log(
                        f"访问 authorize URL... (尝试 {attempt + 1}/{max_retries})"
                    )
                    time.sleep(1)  # 重试前等待
                else:
                    self._log("访问 authorize URL...")

                self._browser_pause()
                r = self._session_get(
                    url,
                    headers=self._headers(
                        url,
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=f"{self.BASE}/",
                        navigation=True,
                    ),
                    allow_redirects=True,
                    timeout=30,
                )

                final_url = str(r.url)
                self._log(f"重定向到: {final_url}")
                return final_url

            except Exception as e:
                error_msg = str(e)
                is_tls_error = (
                    "TLS" in error_msg
                    or "SSL" in error_msg
                    or "curl: (35)" in error_msg
                )

                if is_tls_error and attempt < max_retries - 1:
                    self._log(
                        f"Authorize TLS 错误 (尝试 {attempt + 1}/{max_retries}): {error_msg[:100]}"
                    )
                    continue
                else:
                    self._log(f"Authorize 失败: {e}")
                    return ""

        return ""

    def callback(self, callback_url=None, referer=None):
        """完成注册回调"""
        self._log("执行回调...")
        url = callback_url or f"{self.AUTH}/api/accounts/authorize/callback"
        ok, _ = self._follow_flow_state(
            self._state_from_url(url),
            referer=referer or f"{self.AUTH}/about-you",
        )
        return ok

    def register_user(self, email, password):
        """
        注册用户（邮箱 + 密码）

        Returns:
            tuple: (success, message)
        """
        self._enter_stage("authorize_continue", f"register_user email={email}")
        self._log(f"注册用户: {email}")
        url = f"{self.AUTH}/api/accounts/user/register"

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/create-account/password",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())

        sentinel_token, _sentinel_so_token = self._get_sentinel_token(
            "username_password_create",
            page_url=f"{self.AUTH}/create-account/password",
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token

        payload = {
            "username": email,
            "password": password,
        }

        try:
            self._browser_pause()
            r = self._session_post(url, json=payload, headers=headers, timeout=30)

            if r.status_code == 200:
                data = r.json()
                self._log("注册成功")
                self._log(f"authorize_continue/register_user 响应 URL: {str(r.url)[:120]}")
                return True, "注册成功"
            else:
                try:
                    error_data = r.json()
                    error_msg = error_data.get("error", {}).get("message", r.text[:200])
                except:
                    error_msg = r.text[:200]
                self._log(f"注册失败: {r.status_code} - {error_msg}")
                return False, f"HTTP {r.status_code}: {error_msg}"

        except Exception as e:
            self._log(f"注册异常: {e}")
            return False, str(e)

    def send_email_otp(self, referer=None):
        """触发发送邮箱验证码"""
        self._enter_stage("otp", "send email otp")
        self._log("触发发送验证码...")
        url = f"{self.AUTH}/api/accounts/email-otp/send"

        try:
            self._browser_pause()
            r = self._session_get(
                url,
                headers=self._headers(
                    url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    referer=referer or f"{self.AUTH}/create-account/password",
                    navigation=True,
                    fetch_site="same-origin",
                ),
                allow_redirects=True,
                timeout=30,
            )
            self._log(f"验证码发送状态: {r.status_code}")
            if r.status_code != 200:
                self._log(f"验证码发送失败响应: {r.text[:180]}")
                return False

            try:
                payload = r.json()
            except Exception:
                payload = {}

            if isinstance(payload, dict) and payload:
                next_state = self._state_from_payload(payload, current_url=str(r.url) or url)
                self._log(f"验证码发送响应: {describe_flow_state(next_state)}")
                self._log(f"otp/send 当前 URL: {str(r.url)[:120]}")
            else:
                self._log("验证码发送响应: 非 JSON（按已触发处理）")
            return True
        except Exception as e:
            self._log(f"发送验证码失败: {e}")
            return False

    def verify_email_otp(self, otp_code, return_state=False):
        """
        验证邮箱 OTP 码

        Args:
            otp_code: 6位验证码

        Returns:
            tuple: (success, message)
        """
        self._enter_stage("otp", f"verify email otp code={otp_code}")
        self._log(f"验证 OTP 码: {otp_code}")
        url = f"{self.AUTH}/api/accounts/email-otp/validate"

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/email-verification",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())

        payload = {"code": otp_code}

        try:
            self._browser_pause()
            r = self._session_post(url, json=payload, headers=headers, timeout=30)

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                next_state = self._state_from_payload(
                    data, current_url=str(r.url) or f"{self.AUTH}/about-you"
                )
                self._log(f"验证成功 {describe_flow_state(next_state)}")
                self._log(f"otp/validate 当前 URL: {str(r.url)[:120]}")
                return (True, next_state) if return_state else (True, "验证成功")
            else:
                error_msg = r.text[:200]
                error_code = ""
                try:
                    error_data = r.json() or {}
                    error_info = error_data.get("error") or {}
                    error_code = str(error_info.get("code") or "").strip()
                    error_msg = str(error_info.get("message") or error_msg).strip()
                except Exception:
                    pass
                detail = f"HTTP {r.status_code}"
                if error_code:
                    detail += f": {error_code}"
                if error_msg:
                    detail += f": {error_msg}"
                self._log(f"验证失败: {r.status_code} - {error_msg}")
                return False, detail

        except Exception as e:
            self._log(f"验证异常: {e}")
            return False, str(e)

    def _dump_client_auth_session(self, referer=None):
        """OTP 验证完成后拉取 client auth session dump。

        对应 HAR 中的 GET /api/accounts/client_auth_session_dump。
        浏览器在跳转 about-you 前调用，拉取 client_auth_session /
        checksum / session_id 写入本地缓存。
        """
        self._enter_stage("otp", "dump client auth session")
        self._log("拉取客户端认证 session dump...")
        url = f"{self.AUTH}/api/accounts/client_auth_session_dump"

        headers = self._headers(
            url,
            accept="application/json",
            referer=referer or f"{self.AUTH}/email-verification",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())

        try:
            self._browser_pause()
            r = self._session_get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                try:
                    body = r.json()
                    if isinstance(body, dict):
                        self.last_client_auth_session_dump = body
                        self.last_client_auth_session_id = str(body.get("session_id") or "").strip()
                except Exception:
                    pass
                self._log(f"session dump 成功: session_id={self.last_client_auth_session_id or '-'}")
                return True
            self._log(f"session dump HTTP {r.status_code}（继续流程）")
            return False
        except Exception as e:
            self._log(f"session dump 异常（继续流程）: {e}")
            return False

    def verify_phone(self, phone_provider, return_state=False):
        self._enter_stage("add_phone", "verify phone")
        last_error = ""
        max_attempts = max(1, int(getattr(phone_provider, "max_attempts", 1) or 1))
        for attempt in range(1, max_attempts + 1):
            lease = None
            try:
                self._log(f"手机验证尝试 {attempt}/{max_attempts}: 获取号码")
                lease = phone_provider.acquire_phone()
                phone_provider.prepare_for_sms(lease)
                ok, msg = self.send_phone_otp(lease.phone_number)
                if not ok:
                    last_error = msg
                    phone_provider.mark_failure(lease, msg)
                    continue
                code = phone_provider.wait_for_code(lease)
                if not code:
                    last_error = "接码超时"
                    phone_provider.mark_failure(lease, last_error)
                    continue
                ok, next_state = self.verify_phone_otp(code, return_state=True)
                if ok:
                    phone_provider.mark_success(lease)
                    return (True, next_state) if return_state else (True, "手机验证成功")
                last_error = str(next_state)
                phone_provider.mark_failure(lease, last_error)
            except Exception as exc:
                last_error = str(exc)
                if lease is not None:
                    try:
                        phone_provider.mark_failure(lease, last_error)
                    except Exception:
                        pass
                self._log(f"手机验证异常: {last_error}")
        return False, last_error or "手机验证失败"

    def send_phone_otp(self, phone_number):
        self._enter_stage("add_phone", f"send phone otp {phone_number}")
        url = f"{self.AUTH}/api/accounts/add-phone/send"
        sentinel_token, _sentinel_so_token = self._get_sentinel_token(
            "authorize_continue",
            page_url=f"{self.AUTH}/add-phone",
        )
        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/add-phone",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token
        headers.update(generate_datadog_trace())
        try:
            self._browser_pause()
            r = self._session_post(url, json={"phone_number": phone_number}, headers=headers, timeout=30)
            if r.status_code == 200:
                self._log(f"手机验证码发送成功: {phone_number}")
                return True, "sent"
            self._log_response_debug("手机验证码发送失败响应", r)
            return False, f"HTTP {r.status_code}: {r.text[:240]}"
        except Exception as exc:
            return False, str(exc)

    def verify_phone_otp(self, code, return_state=False):
        self._enter_stage("add_phone", "verify phone otp")
        url = f"{self.AUTH}/api/accounts/phone-otp/validate"
        sentinel_token, _sentinel_so_token = self._get_sentinel_token(
            "authorize_continue",
            page_url=f"{self.AUTH}/phone-verification",
        )
        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/phone-verification",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token
        headers.update(generate_datadog_trace())
        try:
            self._browser_pause()
            r = self._session_post(url, json={"code": str(code or "").strip()}, headers=headers, timeout=30)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                next_state = self._state_from_payload(data, current_url=str(r.url) or self.BASE)
                self._log(f"手机验证码验证成功 {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "手机验证成功")
            self._log_response_debug("手机验证码验证失败响应", r)
            return False, f"HTTP {r.status_code}: {r.text[:240]}"
        except Exception as exc:
            return False, str(exc)

    def create_account(self, first_name, last_name, birthdate, return_state=False):
        """
        完成账号创建（提交姓名和生日）

        Args:
            first_name: 名
            last_name: 姓
            birthdate: 生日 (YYYY-MM-DD)

        Returns:
            tuple: (success, message)
        """
        self._enter_stage("about_you", "register create_account")
        name = f"{first_name} {last_name}"
        self._log(f"完成账号创建: {name}")
        url = f"{self.AUTH}/api/accounts/create_account"

        sentinel_token, sentinel_so_token = self._get_sentinel_token(
            "oauth_create_account",
            page_url=f"{self.AUTH}/about-you",
        )
        if sentinel_token:
            self._log(
                "create_account: 已生成 sentinel token "
                f"so={'OK' if sentinel_so_token else 'X'} len={len(str(sentinel_token or ''))}"
            )
        else:
            self._log("create_account: 未获取 sentinel token，停止提交 about_you")
            return False, "无法获取 sentinel token (oauth_create_account)"

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/about-you",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token
        if sentinel_so_token:
            headers["openai-sentinel-so-token"] = sentinel_so_token
        headers.update(generate_datadog_trace())

        payload = {
            "name": name,
            "birthdate": birthdate,
        }

        try:
            self._browser_pause()
            r = self._session_post(url, json=payload, headers=headers, timeout=30)

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                next_state = self._state_from_payload(
                    data, current_url=str(r.url) or self.BASE
                )
                self._log(f"账号创建成功 {describe_flow_state(next_state)}")
                self._log(f"about_you/create_account 当前 URL: {str(r.url)[:120]}")
                return (True, next_state) if return_state else (True, "账号创建成功")
            else:
                self._log_response_debug("create_account 失败响应", r)
                error_code = ""
                error_msg = r.text[:500]
                try:
                    error_data = r.json() or {}
                    error_info = error_data.get("error") or {}
                    error_code = str(error_info.get("code") or "").strip()
                    error_msg = str(error_info.get("message") or error_msg).strip()
                except Exception:
                    pass

                detail = f"HTTP {r.status_code}"
                if error_code:
                    detail += f": {error_code}"
                elif error_msg:
                    detail += f": {error_msg[:300]}"

                self._log(f"创建失败: {detail} - {error_msg[:500]}")
                return False, detail

        except Exception as e:
            response = getattr(e, "response", None)
            if response is not None:
                self._log_response_debug("create_account 异常响应", response)
            self._log(f"创建异常: {type(e).__name__}: {e}")
            return False, str(e)

    def register_complete_flow(
        self,
        email,
        password,
        first_name,
        last_name,
        birthdate,
        skymail_client,
        stop_before_about_you_submission=False,
        otp_wait_timeout=600,
        otp_resend_wait_timeout=300,
        phone_provider=None,
    ):
        """
        完整的注册流程（基于原版 run_register 方法）

        Args:
            email: 邮箱
            password: 密码
            first_name: 名
            last_name: 姓
            birthdate: 生日
            skymail_client: Skymail 客户端（用于获取验证码）

        Returns:
            tuple: (success, message)
        """
        from urllib.parse import urlparse

        self._log(
            "注册状态机参数: "
            f"stop_before_about_you_submission={'on' if stop_before_about_you_submission else 'off'}, "
            f"otp_wait_timeout={otp_wait_timeout}s, otp_resend_wait_timeout={otp_resend_wait_timeout}s"
        )

        try:
            otp_wait_timeout = max(30, int(otp_wait_timeout or 600))
        except Exception:
            otp_wait_timeout = 600
        try:
            otp_resend_wait_timeout = max(30, int(otp_resend_wait_timeout or 300))
        except Exception:
            otp_resend_wait_timeout = 300

        max_auth_attempts = 3
        final_url = ""
        final_path = ""

        for auth_attempt in range(max_auth_attempts):
            if auth_attempt > 0:
                self._log(f"预授权阶段重试 {auth_attempt + 1}/{max_auth_attempts}...")
                self._reset_session()

            # 1. 接入 ChatGPT 入口预热/登录 helper，内部按 HAR 顺序完成首页、backend-anon、
            # providers、csrf、signin/openai 和 authorize 跳转。
            final_url = self._bootstrap_chatgpt_entry(email)
            if not final_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "ChatGPT 入口预热失败"

            final_path = urlparse(final_url).path
            self._log(f"Authorize → {final_path}")

            # /api/accounts/authorize 实际上常对应 Cloudflare 403 中间页，不要继续走 authorize_continue。
            if "api/accounts/authorize" in final_path or final_path == "/error":
                self._log(
                    f"检测到 Cloudflare/SPA 中间页，准备重试预授权: {final_url[:160]}..."
                )
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, f"预授权被拦截: {final_path}"

            break

        state = self._state_from_url(final_url)
        self._log(f"注册状态起点: {describe_flow_state(state)}")

        register_submitted = False
        otp_verified = False
        account_created = False
        seen_states = {}

        otp_send_attempts = 0

        for _ in range(12):
            signature = self._state_signature(state)
            seen_states[signature] = seen_states.get(signature, 0) + 1
            self._log(
                f"注册状态推进: step={sum(seen_states.values())} "
                f"state={describe_flow_state(state)} seen={seen_states[signature]}"
            )
            if seen_states[signature] > 2:
                return False, f"注册状态卡住: {describe_flow_state(state)}"

            if self._is_registration_complete_state(state):
                self.last_registration_state = state
                self._log("[OK] 注册流程完成")
                return True, "注册成功"

            if self._state_is_password_registration(state):
                self._enter_stage("authorize_continue", describe_flow_state(state))
                self._log("全新注册流程")
                if register_submitted:
                    return False, "注册密码阶段重复进入"
                success, msg = self.register_user(email, password)
                if not success:
                    return False, f"注册失败: {msg}"
                register_submitted = True
                otp_send_attempts += 1
                self._log(f"发送注册验证码: attempt={otp_send_attempts}")
                if not self.send_email_otp(
                    referer=state.current_url or state.continue_url or f"{self.AUTH}/create-account/password"
                ):
                    self._log("发送验证码接口返回失败，继续等待邮箱中的验证码...")
                else:
                    self._log("发送注册验证码成功，进入收码阶段")
                state = self._state_from_url(f"{self.AUTH}/email-verification")
                continue

            if self._state_is_email_otp(state):
                self._enter_stage("otp", describe_flow_state(state))
                self._log("等待邮箱验证码...")
                otp_code = skymail_client.wait_for_verification_code(
                    email, timeout=otp_wait_timeout
                )
                if not otp_code:
                    self._log(
                        "首次等待未收到验证码，尝试重发一次 email-otp/send "
                        f"后再等待 {otp_resend_wait_timeout}s"
                    )
                    otp_send_attempts += 1
                    resend_ok = self.send_email_otp(
                        referer=state.current_url or state.continue_url or f"{self.AUTH}/email-verification"
                    )
                    if resend_ok:
                        self._log(f"重发验证码成功: attempt={otp_send_attempts}")
                    else:
                        self._log(f"重发验证码失败: attempt={otp_send_attempts}")
                    otp_code = skymail_client.wait_for_verification_code(
                        email, timeout=otp_resend_wait_timeout
                    )
                if not otp_code:
                    return False, "未收到验证码"

                success, next_state = self.verify_email_otp(otp_code, return_state=True)
                if not success:
                    return False, f"验证码失败: {next_state}"
                otp_verified = True
                self._dump_client_auth_session(
                    referer=state.current_url or state.continue_url or f"{self.AUTH}/email-verification"
                )
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_about_you(state):
                self._enter_stage("about_you", describe_flow_state(state))
                if stop_before_about_you_submission:
                    self.last_registration_state = state
                    self._log(
                        "注册链路已到 about_you，按 interrupt 流程停止。"
                        "下一步交由 OAuth 新会话提交姓名+生日。"
                    )
                    return True, "pending_about_you_submission"
                if account_created:
                    return False, "填写信息阶段重复进入"
                success, next_state = self.create_account(
                    first_name,
                    last_name,
                    birthdate,
                    return_state=True,
                )
                if not success:
                    return False, f"创建账号失败: {next_state}"
                account_created = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_add_phone(state):
                self._enter_stage("add_phone", describe_flow_state(state))
                if phone_provider is None:
                    return False, "需要手机验证，但 phone_verification_enabled 未开启"
                success, next_state = self.verify_phone(phone_provider, return_state=True)
                if not success:
                    return False, f"手机验证失败: {next_state}"
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_requires_navigation(state):
                if "workspace" in f"{state.continue_url} {state.current_url}".lower() or "consent" in f"{state.continue_url} {state.current_url}".lower():
                    self._enter_stage("workspace_select", describe_flow_state(state))
                elif state.page_type == "external_url":
                    self._enter_stage("token_exchange", describe_flow_state(state))
                success, next_state = self._follow_flow_state(
                    state,
                    referer=state.current_url or f"{self.AUTH}/about-you",
                )
                if not success:
                    return False, f"跳转失败: {next_state}"
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_identity_verification(state):
                detail = describe_flow_state(state)
                self._log(f"identity_verification 直接判失败: {detail}")
                return False, f"identity_verification: {detail}"

            if (
                (not register_submitted)
                and (not otp_verified)
                and (not account_created)
            ):
                self._log(
                    f"未知起始状态，回退为 OTP-first 默认流程: {describe_flow_state(state)}"
                )
                state = self._state_from_url(f"{self.AUTH}/email-verification")
                continue

            return False, f"未支持的注册状态: {describe_flow_state(state)}"

        return False, "注册状态机超出最大步数"
