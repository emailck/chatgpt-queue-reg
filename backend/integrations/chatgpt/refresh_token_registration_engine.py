"""
ChatGPT Refresh Token 注册引擎。

主链路采用两段式推进：
1. `ChatGPTClient.register_complete_flow()` 负责把注册状态机推进到 about_you
2. `OAuthClient.login_and_get_tokens()` 承接前序会话继续完成 about_you / workspace / token
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from backend.core.legacy_shim import TaskInterruption

from .chatgpt_client import ChatGPTClient
from .oauth import OAuthManager
from .oauth_client import OAuthClient
from .utils import (
    generate_random_birthday,
    generate_random_name,
    generate_random_password,
)

logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    """注册结果。"""

    success: bool
    email: str = ""
    password: str = ""
    account_id: str = ""
    workspace_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""
    error_message: str = ""
    logs: list | None = None
    metadata: dict | None = None
    source: str = "register"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "workspace_id": self.workspace_id,
            "access_token": self.access_token[:20] + "..." if self.access_token else "",
            "refresh_token": self.refresh_token[:20] + "..." if self.refresh_token else "",
            "id_token": self.id_token[:20] + "..." if self.id_token else "",
            "session_token": self.session_token[:20] + "..." if self.session_token else "",
            "error_message": self.error_message,
            "logs": self.logs or [],
            "metadata": self.metadata or {},
            "source": self.source,
        }


@dataclass
class SignupFormResult:
    """保留旧结构，兼容外部引用。"""

    success: bool
    page_type: str = ""
    is_existing_account: bool = False
    response_data: Dict[str, Any] | None = None
    error_message: str = ""


class EmailServiceAdapter:
    """将现有 email_service 适配给 ChatGPTClient / OAuthClient 状态机。"""

    def __init__(self, email_service, email: str, log_fn: Callable[[str], None]):
        self.email_service = email_service
        self.email = email
        self.log_fn = log_fn
        self._used_codes: set[str] = set()
        self._last_code: str = ""
        self._last_code_at: float = 0.0
        self._last_success_code: str = ""
        self._last_success_code_at: float = 0.0

    @property
    def last_code(self) -> str:
        return self._last_success_code or self._last_code

    def _remember_code(self, code: str, *, successful: bool = False) -> None:
        code = str(code or "").strip()
        if not code:
            return
        now = time.time()
        self._last_code = code
        self._last_code_at = now
        self._used_codes.add(code)
        if successful:
            self._last_success_code = code
            self._last_success_code_at = now

    def remember_successful_code(self, code: str) -> None:
        self._remember_code(code, successful=True)

    def get_recent_code(
        self,
        max_age_seconds: int = 180,
        *,
        prefer_successful: bool = True,
    ) -> str:
        now = time.time()
        if (
            prefer_successful
            and self._last_success_code
            and now - self._last_success_code_at <= max_age_seconds
        ):
            return self._last_success_code
        if self._last_code and now - self._last_code_at <= max_age_seconds:
            return self._last_code
        return ""

    def wait_for_verification_code(
        self,
        email: str,
        timeout: int = 90,
        otp_sent_at: float | None = None,
        exclude_codes=None,
    ):
        excluded = set(exclude_codes) if exclude_codes is not None else set(self._used_codes)
        self.log_fn(f"正在等待邮箱 {email} 的验证码 ({timeout}s)...")
        code = self.email_service.get_verification_code(
            email=email,
            timeout=timeout,
            otp_sent_at=otp_sent_at,
            exclude_codes=excluded,
        )
        if code:
            code = str(code).strip()
            self._remember_code(code, successful=False)
            self.log_fn(f"成功获取验证码: {code}")
        return code


class RefreshTokenRegistrationEngine:
    """Refresh token 注册引擎。"""

    def __init__(
        self,
        email_service,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
        browser_mode: str = "protocol",
        max_retries: int = 3,
        extra_config: Optional[dict] = None,
    ):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda msg: logger.info(msg))
        self.task_uuid = task_uuid
        self.browser_mode = str(browser_mode or "protocol").strip().lower() or "protocol"
        # 已移除整流程重试能力，保留参数仅兼容调用方
        self.max_retries = 1
        self.extra_config = dict(extra_config or {})

        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.email_info: Optional[Dict[str, Any]] = None
        self.logs: list[str] = []

    def _log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        self.logs.append(log_message)

        if self.callback_logger:
            self.callback_logger(log_message)

        if level == "error":
            logger.error(log_message)
        elif level == "warning":
            logger.warning(log_message)
        else:
            logger.info(log_message)

    def _create_email(self) -> bool:
        try:
            self._log(f"正在创建 {self.email_service.service_type.value} 邮箱...")
            self.email_info = self.email_service.create_email()

            email_value = str(
                self.email
                or (self.email_info or {}).get("email")
                or ""
            ).strip()
            if not email_value:
                self._log(
                    f"创建邮箱失败: {self.email_service.service_type.value} 返回空邮箱地址",
                    "error",
                )
                return False

            if self.email_info is None:
                self.email_info = {}
            self.email_info["email"] = email_value
            self.email = email_value
            self._log(f"成功创建邮箱: {self.email}")
            return True
        except Exception as e:
            self._log(f"创建邮箱失败: {e}", "error")
            return False

    def _read_int_config(
        self,
        primary_key: str,
        *,
        fallback_keys: tuple[str, ...] = (),
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        keys = (primary_key, *tuple(fallback_keys or ()))
        for key in keys:
            if key not in self.extra_config:
                continue
            value = self.extra_config.get(key)
            try:
                parsed = int(value)
            except Exception:
                continue
            return max(minimum, min(parsed, maximum))
        return max(minimum, min(int(default), maximum))

    def _read_bool_config(self, key: str, *, default: bool = False) -> bool:
        if key not in self.extra_config:
            return default
        value = str(self.extra_config.get(key, "") or "").strip().lower()
        if not value:
            return default
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _should_switch_to_login_after_register_failure(message: str) -> bool:
        text = str(message or "").lower()
        markers = (
            "account already exists",
            "please login instead",
            "add_phone",
            "add-phone",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_user_already_exists_message(message: Any) -> bool:
        text = str(message or "").lower()
        return "user_already_exists" in text or "user already exists" in text

    def _build_chatgpt_client(self) -> ChatGPTClient:
        client = ChatGPTClient(
            proxy=self.proxy_url,
            verbose=False,
            browser_mode=self.browser_mode,
        )
        client._log = lambda msg: self._log(f"[注册链路] {msg}")
        return client

    def _build_oauth_client(self) -> OAuthClient:
        client = OAuthClient(
            self.extra_config,
            proxy=self.proxy_url,
            verbose=False,
            browser_mode=self.browser_mode,
        )
        client._log = lambda msg: self._log(f"[登录链路] {msg}")
        return client

    def _reuse_register_browser_context(
        self,
        register_client: ChatGPTClient,
        oauth_client: OAuthClient,
    ) -> None:
        oauth_client.adopt_browser_context(
            register_client.session,
            device_id=getattr(register_client, "device_id", "") or "",
            user_agent=getattr(register_client, "ua", None),
            sec_ch_ua=getattr(register_client, "sec_ch_ua", None),
            accept_language=(
                getattr(register_client.session, "headers", {}).get("Accept-Language", "")
                if getattr(register_client, "session", None) is not None
                else ""
            ),
            impersonate=getattr(register_client, "impersonate", None),
        )
        self._log("已接入前序 session/cookie/fingerprint，继续处理 OAuth 后续步骤")

    def _extract_account_info(self, tokens: dict[str, Any]) -> dict[str, Any]:
        id_token = str((tokens or {}).get("id_token") or "").strip()
        if not id_token:
            return {}
        manager = OAuthManager(proxy_url=self.proxy_url)
        return manager.extract_account_info(id_token)

    @staticmethod
    def _extract_workspace_id(oauth_client: OAuthClient) -> str:
        workspace_id = str(getattr(oauth_client, "last_workspace_id", "") or "").strip()
        if workspace_id:
            return workspace_id

        try:
            session_data = oauth_client._decode_oauth_session_cookie() or {}
        except Exception:
            session_data = {}

        workspaces = session_data.get("workspaces") or []
        if not workspaces:
            return ""
        return str((workspaces[0] or {}).get("id") or "").strip()

    @staticmethod
    def _extract_session_token(oauth_client: OAuthClient) -> str:
        getter = getattr(oauth_client, "_get_cookie_value", None)
        if not callable(getter):
            return ""
        return str(
            getter("__Secure-next-auth.session-token", "chatgpt.com")
            or getter("__Secure-authjs.session-token", "chatgpt.com")
            or ""
        ).strip()

    def _parallel_add_phone_retry(
        self,
        *,
        result,
        register_client,
        email_adapter,
        first_name: str,
        last_name: str,
        birthdate: str,
        register_otp_wait_seconds: int,
        parallel: int = 3,
    ):
        """add_phone 阻断后，并行启动多路全新 OAuth session，第一个成功的获胜。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        winning_tokens = None
        winning_client = None

        def _one_attempt(idx):
            client = self._build_oauth_client()
            client.config.setdefault(
                "chatgpt_oauth_otp_wait_seconds", register_otp_wait_seconds
            )
            self._log(f"add_phone 并行重试 #{idx + 1}/{parallel} 启动...")
            t = client.login_and_get_tokens(
                result.email,
                self.password,
                device_id="",
                user_agent=getattr(register_client, "ua", None),
                sec_ch_ua=getattr(register_client, "sec_ch_ua", None),
                impersonate=getattr(register_client, "impersonate", None),
                skymail_client=email_adapter,
                prefer_passwordless_login=True,
                allow_phone_verification=False,
                force_new_browser=True,
                force_chatgpt_entry=False,
                screen_hint="login",
                force_password_login=False,
                complete_about_you_if_needed=True,
                first_name=first_name,
                last_name=last_name,
                birthdate=birthdate,
                login_source=f"add_phone_parallel_{idx}",
            )
            return t, client

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {executor.submit(_one_attempt, i): i for i in range(parallel)}
            for future in as_completed(futures):
                try:
                    t, client = future.result()
                    if t and not winning_tokens:
                        winning_tokens = t
                        winning_client = client
                        self._log(
                            f"add_phone 并行重试 #{futures[future] + 1} 成功，取消其余..."
                        )
                        # 取消尚未开始的 futures
                        for f in futures:
                            if f is not future:
                                f.cancel()
                        break
                except Exception as exc:
                    self._log(f"add_phone 并行重试异常: {exc}", "warning")

        return winning_tokens, winning_client

    def _populate_result_from_tokens(
        self,
        result: RegistrationResult,
        tokens: dict[str, Any],
        oauth_client: OAuthClient,
        registration_message: str,
        source: str,
        register_client: Any,
    ) -> None:
        account_info = self._extract_account_info(tokens)
        workspace_id = self._extract_workspace_id(oauth_client)
        session_token = self._extract_session_token(oauth_client)

        result.success = True
        result.email = self.email or ""
        result.password = self.password or ""
        result.access_token = str(tokens.get("access_token") or "").strip()
        result.refresh_token = str(tokens.get("refresh_token") or "").strip()
        result.id_token = str(tokens.get("id_token") or "").strip()
        result.account_id = str(
            tokens.get("account_id")
            or account_info.get("account_id")
            or ""
        ).strip()
        result.workspace_id = workspace_id
        result.session_token = session_token
        result.source = source
        result.metadata = {
            "email_service": self.email_service.service_type.value,
            "proxy_used": self.proxy_url,
            "registered_at": datetime.now().isoformat(),
            "registration_message": registration_message,
            "registration_flow": "chatgpt_client.register_complete_flow",
            "token_flow": "oauth_client.login_and_get_tokens",
            "token_login_mode": "passwordless",
            "browser_mode": self.browser_mode,
            "device_id": getattr(register_client, "device_id", ""),
            "impersonate": getattr(register_client, "impersonate", ""),
            "user_agent": getattr(register_client, "ua", ""),
            "oai_client_version": getattr(register_client, "oai_client_version", ""),
            "oai_client_build_number": getattr(register_client, "oai_client_build_number", ""),
            "workspace_id": workspace_id,
            "account_claims_email": account_info.get("email", ""),
        }

    def _populate_result_from_session(
        self,
        result: RegistrationResult,
        session_result: dict[str, Any],
        *,
        registration_message: str,
        register_client: Any,
        gopay_result: dict | None = None,
    ) -> None:
        result.success = True
        result.email = self.email or ""
        result.password = self.password or ""
        result.access_token = str(session_result.get("access_token") or "").strip()
        result.session_token = str(session_result.get("session_token") or "").strip()
        result.account_id = str(session_result.get("account_id") or "").strip()
        result.workspace_id = str(session_result.get("workspace_id") or result.account_id or "").strip()
        result.source = "post_register_gopay_session"
        result.metadata = {
            "email_service": self.email_service.service_type.value,
            "proxy_used": self.proxy_url,
            "registered_at": datetime.now().isoformat(),
            "registration_message": registration_message,
            "registration_flow": "chatgpt_client.register_complete_flow",
            "token_flow": "chatgpt_client.reuse_session_and_get_tokens",
            "oauth_deferred": True,
            "browser_mode": self.browser_mode,
            "device_id": getattr(register_client, "device_id", ""),
            "impersonate": getattr(register_client, "impersonate", ""),
            "user_agent": getattr(register_client, "ua", ""),
            "oai_client_version": getattr(register_client, "oai_client_version", ""),
            "oai_client_build_number": getattr(register_client, "oai_client_build_number", ""),
            "gopay": gopay_result or {},
        }

    def _mark_email_consumed_after_signup(self, result: RegistrationResult) -> None:
        metadata = dict(result.metadata or {})
        metadata["mailbox_account_consumed"] = True
        result.metadata = metadata

    def run(self) -> RegistrationResult:
        result = RegistrationResult(success=False, logs=self.logs)
        last_error = ""
        fixed_email = str(self.email or "").strip()
        register_otp_wait_seconds = self._read_int_config(
            "chatgpt_register_otp_wait_seconds",
            fallback_keys=("chatgpt_otp_wait_seconds",),
            default=600,
            minimum=30,
            maximum=3600,
        )
        register_otp_resend_wait_seconds = self._read_int_config(
            "chatgpt_register_otp_resend_wait_seconds",
            fallback_keys=("chatgpt_register_otp_wait_seconds", "chatgpt_otp_wait_seconds"),
            default=300,
            minimum=30,
            maximum=3600,
        )

        try:
            registration_message = ""
            source = "register"

            self._log("=" * 60)
            self._log("ChatGPT RT 全新主链路启动")
            self._log(f"请求模式: {self.browser_mode}")
            self._log("实现策略: 注册状态机 + OAuth 接续流程")
            self._log("=" * 60)

            def _release_gopay_reservation_if_needed() -> None:
                gopay = (result.metadata or {}).get("gopay") if isinstance(result.metadata, dict) else None
                if not isinstance(gopay, dict):
                    return
                source_id = str(gopay.get("source_id") or "").strip()
                if not source_id:
                    return
                try:
                    from backend.integrations.chatgpt.gopay_bridge import release_gopay_phone_reservation

                    release_gopay_phone_reservation(source_id)
                except Exception:
                    pass

            if not fixed_email:
                self.email = None

            self._log("1. 创建邮箱...")
            if not self._create_email():
                last_error = "创建邮箱失败"
                result.error_message = last_error
                return result

            result.email = self.email or ""
            self.password = self.password or generate_random_password(16)
            result.password = self.password

            first_name, last_name = generate_random_name()
            birthdate = generate_random_birthday()
            self._log(f"邮箱: {result.email}")
            self._log(f"密码: {self.password}")
            self._log(f"注册信息: {first_name} {last_name}, 生日: {birthdate}")
            self._log("流程策略: 注册阶段推进到 about_you 后切换到 OAuth 流程继续完成后续步骤")
            self._log(
                "验证码等待策略: "
                f"register_wait={register_otp_wait_seconds}s, "
                f"register_resend_wait={register_otp_resend_wait_seconds}s, "
                "oauth_wait=读取 OAuthClient 配置（默认600s）"
            )

            email_adapter = EmailServiceAdapter(
                self.email_service,
                result.email,
                self._log,
            )

            _REG_RETRY_MARKERS = (
                "访问首页失败",
                "获取 CSRF token 失败",
                "预授权被拦截",
            )
            registered = False
            registration_message = ""
            for _reg_attempt in range(3):
                if _reg_attempt > 0:
                    self._log(
                        f"注册状态机重试 {_reg_attempt}/2（原因: {registration_message}）..."
                    )
                register_client = self._build_chatgpt_client()
                self._log("2. 执行注册状态机（interrupt 模式：不在注册阶段提交 about_you）...")
                registered, registration_message = register_client.register_complete_flow(
                    result.email,
                    self.password,
                    first_name,
                    last_name,
                    birthdate,
                    email_adapter,
                    stop_before_about_you_submission=True,
                    otp_wait_timeout=register_otp_wait_seconds,
                    otp_resend_wait_timeout=register_otp_resend_wait_seconds,
                )
                if registered:
                    break
                if not any(m in registration_message for m in _REG_RETRY_MARKERS):
                    break

            if not registered:
                if not self._should_switch_to_login_after_register_failure(
                    registration_message
                ):
                    last_error = f"注册状态机失败: {registration_message}"
                    result.error_message = last_error
                    return result

                self._mark_email_consumed_after_signup(result)
                self._log(
                    "注册阶段命中可继续处理的终态，改走 OAuth 登录流程",
                    "warning",
                )
                self._log(f"切换原因: {registration_message}")
                source = "login"
            else:
                if registration_message == "pending_about_you_submission":
                    self._log("注册状态机已推进至 about_you，符合预期。下一步进入 OAuth 会话补全资料")
                else:
                    self._log(
                        "注册状态机返回成功但未停在 about_you。"
                        "将继续进入 OAuth 会话，按状态机实际返回推进。"
                    )
                    self._mark_email_consumed_after_signup(result)

            oauth_client = self._build_oauth_client()
            oauth_client.config.setdefault(
                "chatgpt_oauth_otp_wait_seconds",
                register_otp_wait_seconds,
            )
            oauth_client.config.setdefault(
                "chatgpt_oauth_otp_resend_wait_seconds",
                register_otp_resend_wait_seconds,
            )

            use_continued_session = registered and (
                registration_message == "pending_about_you_submission"
            )
            gopay_result = None

            tokens = None
            if use_continued_session:
                self._reuse_register_browser_context(register_client, oauth_client)
                self._log("3. 承接前序 session，先在已验证注册会话中提交 about_you")
                self._log("4. 沿用前序阶段的 cookie / device_id / 浏览器指纹")
                create_ok, create_state = register_client.create_account(
                    first_name,
                    last_name,
                    birthdate,
                    return_state=True,
                )
                if not create_ok:
                    last_error = f"about_you 提交失败: {create_state}"
                    if self._is_user_already_exists_message(create_state):
                        self._mark_email_consumed_after_signup(result)
                    result.error_message = last_error
                    return result
                else:
                    self._mark_email_consumed_after_signup(result)
                    register_client.last_registration_state = create_state
                    self._log(
                        "about_you 已在注册会话提交完成，先提取 ChatGPT Session / AccessToken"
                    )
                    session_ok, session_result = register_client.reuse_session_and_get_tokens()
                    if session_ok:
                        self._log("已从注册会话提取 ChatGPT accessToken / session_token")
                        from backend.integrations.chatgpt.gopay_bridge import (
                            is_gopay_sms_otp_timeout,
                            run_gopay_with_chatgpt_session,
                            should_run_gopay,
                        )

                        if should_run_gopay(self.extra_config):
                            try:
                                gopay_result = run_gopay_with_chatgpt_session(
                                    chatgpt_client=register_client,
                                    tokens=session_result,
                                    extra_config=self.extra_config,
                                    proxy_url=self.proxy_url,
                                    log=self._log,
                                    billing_defaults={
                                        "name": f"{first_name} {last_name}".strip(),
                                        "email": result.email,
                                    },
                                )
                                result.metadata = {
                                    **(result.metadata or {}),
                                    "gopay": gopay_result,
                                }
                            except Exception as gopay_error:
                                last_error = f"GoPay checkout failed after ChatGPT signup: {gopay_error}"
                                self._log(last_error, "error")
                                if is_gopay_sms_otp_timeout(gopay_error):
                                    self._populate_result_from_session(
                                        result,
                                        session_result,
                                        registration_message=registration_message,
                                        register_client=register_client,
                                        gopay_result={
                                            "state": "verify_timeout",
                                            "error": str(gopay_error),
                                        },
                                    )
                                    self._mark_email_consumed_after_signup(result)
                                result.error_message = last_error
                                result.success = False
                                return result
                            if is_gopay_sms_otp_timeout(gopay_result):
                                last_error = "GoPay checkout failed after ChatGPT signup: GoPay SMS OTP timeout"
                                self._log(last_error, "error")
                                self._populate_result_from_session(
                                    result,
                                    session_result,
                                    registration_message=registration_message,
                                    register_client=register_client,
                                    gopay_result=gopay_result,
                                )
                                self._mark_email_consumed_after_signup(result)
                                result.error_message = last_error
                                result.success = False
                                return result
                            if gopay_result and str(gopay_result.get("state") or "").lower() in {
                                "manual_har_capture_only",
                                "manual_browser_open",
                            }:
                                self._populate_result_from_session(
                                    result,
                                    session_result,
                                    registration_message=registration_message,
                                    register_client=register_client,
                                    gopay_result=gopay_result,
                                )
                                if str(gopay_result.get("state") or "").lower() == "manual_browser_open":
                                    self._log("Team hosted 手动浏览器已打开，已跳过 OAuth RT 和 GoPay 自动后半段")
                                else:
                                    self._log("GoPay 仅手动抓 HAR 模式已结束，已跳过 OAuth RT 和自动支付后半段")
                                self._log(f"Account ID: {result.account_id}")
                                self._log("=" * 60)
                                return result
                            if gopay_result and str(gopay_result.get("state") or "").lower() == "succeeded":
                                run_oauth_after_gopay = str(
                                    self.extra_config.get("chatgpt_gopay_run_oauth_after_success") or ""
                                ).strip().lower() in {"1", "true", "yes", "on"}
                                if not run_oauth_after_gopay:
                                    self._populate_result_from_session(
                                        result,
                                        session_result,
                                        registration_message=registration_message,
                                        register_client=register_client,
                                        gopay_result=gopay_result,
                                    )
                                    self._log("GoPay 阶段已成功，按配置跳过 OAuth RT，先以 ChatGPT session/access token 落盘")
                                    self._log(f"Account ID: {result.account_id}")
                                    self._log("=" * 60)
                                    return result
                    else:
                        self._log(
                            f"注册会话 token 提取失败，继续尝试 OAuth token 流程: {session_result}",
                            "warning",
                        )
                    self._reuse_register_browser_context(register_client, oauth_client)
                    self._log("5. 启动 OAuth token 流程；如需重新认证则走密码路径，避免重复 passwordless OTP")
                    tokens = oauth_client.login_and_get_tokens(
                        result.email,
                        self.password,
                        device_id=getattr(register_client, "device_id", "") or "",
                        user_agent=getattr(register_client, "ua", None),
                        sec_ch_ua=getattr(register_client, "sec_ch_ua", None),
                        impersonate=getattr(register_client, "impersonate", None),
                        skymail_client=email_adapter,
                        prefer_passwordless_login=False,
                        allow_phone_verification=False,
                        force_new_browser=False,
                        force_chatgpt_entry=False,
                        screen_hint="login",
                        force_password_login=False,
                        complete_about_you_if_needed=False,
                        first_name=first_name,
                        last_name=last_name,
                        birthdate=birthdate,
                        login_source="post_register_workspace_continue",
                    )
            if not use_continued_session and tokens is None:
                self._log("3. 新开 OAuth session，按 screen_hint=login + passwordless OTP 登录...")
                self._log("4. 若命中 about_you，则在 OAuth 会话内提交姓名+生日，再继续 workspace/token")
                tokens = oauth_client.login_and_get_tokens(
                    result.email,
                    self.password,
                    device_id="",
                    user_agent=getattr(register_client, "ua", None),
                    sec_ch_ua=getattr(register_client, "sec_ch_ua", None),
                    impersonate=getattr(register_client, "impersonate", None),
                    skymail_client=email_adapter,
                    prefer_passwordless_login=True,
                    allow_phone_verification=False,
                    force_new_browser=True,
                    force_chatgpt_entry=False,
                    screen_hint="login",
                    force_password_login=False,
                    complete_about_you_if_needed=True,
                    first_name=first_name,
                    last_name=last_name,
                    birthdate=birthdate,
                    login_source=(
                        "existing_account_continue" if source == "login" else "post_register_workspace_continue"
                    ),
                )

            if not tokens:
                last_error = oauth_client.last_error or "OAuth 登录状态机失败"
                if "add_phone" in last_error:
                    if self._read_bool_config("chatgpt_oauth_add_phone_parallel_retry_enabled", default=False):
                        self._log(
                            "OAuth add_phone 阻断，按配置启动并行 OAuth 重试（3 路并发）...",
                            "warning",
                        )
                        tokens, oauth_client = self._parallel_add_phone_retry(
                            result=result,
                            register_client=register_client,
                            email_adapter=email_adapter,
                            first_name=first_name,
                            last_name=last_name,
                            birthdate=birthdate,
                            register_otp_wait_seconds=register_otp_wait_seconds,
                        )
                        if not tokens:
                            last_error = (oauth_client.last_error if oauth_client else None) or last_error
                    else:
                        self._log(
                            "OAuth add_phone 阻断，已跳过并行 OAuth 重试；该重试会重复发送邮箱 OTP 并容易触发 429。"
                            "如确认需要，可开启 chatgpt_oauth_add_phone_parallel_retry_enabled。",
                            "warning",
                        )
                if not tokens:
                    result.error_message = last_error
                    return result

            self._populate_result_from_tokens(
                result=result,
                tokens=tokens,
                oauth_client=oauth_client,
                registration_message=registration_message,
                source=source,
                register_client=register_client,
            )

            from backend.integrations.chatgpt.gopay_bridge import (
                is_gopay_sms_otp_timeout,
                run_gopay_with_oauth_session,
                should_run_gopay,
            )

            if should_run_gopay(self.extra_config) and gopay_result is None:
                try:
                    gopay_result = run_gopay_with_oauth_session(
                        oauth_client=oauth_client,
                        tokens=tokens,
                        extra_config=self.extra_config,
                        proxy_url=self.proxy_url,
                        log=self._log,
                        billing_defaults={
                            "name": f"{first_name} {last_name}".strip(),
                            "email": result.email,
                        },
                    )
                    result.metadata = {
                        **(result.metadata or {}),
                        "gopay": gopay_result,
                    }
                except Exception as gopay_error:
                    last_error = f"GoPay checkout failed after OAuth: {gopay_error}"
                    self._log(last_error, "error")
                    if is_gopay_sms_otp_timeout(gopay_error):
                        self._mark_email_consumed_after_signup(result)
                    result.error_message = last_error
                    result.success = False
                    return result
                if is_gopay_sms_otp_timeout(gopay_result):
                    last_error = "GoPay checkout failed after OAuth: GoPay SMS OTP timeout"
                    self._log(last_error, "error")
                    self._mark_email_consumed_after_signup(result)
                    result.error_message = last_error
                    result.success = False
                    return result
            if gopay_result is not None:
                result.metadata = {
                    **(result.metadata or {}),
                    "gopay": gopay_result,
                }

            self._log("5. 主链路完成")
            self._log(f"Account ID: {result.account_id}")
            self._log(f"Workspace ID: {result.workspace_id}")
            self._log("=" * 60)
            return result

        except TaskInterruption:
            try:
                _release_gopay_reservation_if_needed()
            except Exception:
                pass
            raise
        except Exception as e:
            self._log(f"RT 注册主链路异常: {e}", "error")
            result.error_message = str(e)
            return result
        finally:
            if not result.success:
                try:
                    _release_gopay_reservation_if_needed()
                except Exception:
                    pass

    def save_to_database(self, result: RegistrationResult) -> bool:
        """保留旧接口，占位返回。"""
        return bool(result and result.success)
