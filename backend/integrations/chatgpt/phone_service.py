from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from curl_cffi import requests as curl_requests

from backend.core.proxy import build_requests_proxy_config


@dataclass
class PhoneLease:
    provider: str
    activation_id: str
    phone_number: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PhoneProvider:
    provider_name = "base"

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None], proxy_url: str = ""):
        self.config = dict(config or {})
        self.log_fn = log_fn
        self.proxy_url = proxy_url if _truthy(self.config.get("phone_verification_use_proxy")) else ""
        self.max_attempts = _read_int(
            self.config,
            "phone_verification_max_attempts",
            fallback_keys=(f"{self.provider_name}_max_tries",),
            default=3,
            minimum=1,
            maximum=10,
        )
        self.poll_timeout_seconds = _read_int(
            self.config,
            "phone_verification_poll_timeout_seconds",
            fallback_keys=(f"{self.provider_name}_poll_timeout_seconds", f"{self.provider_name}_poll_timeout_sec"),
            default=180,
            minimum=15,
            maximum=900,
        )
        self.poll_interval_seconds = _read_int(
            self.config,
            "phone_verification_poll_interval_seconds",
            fallback_keys=(f"{self.provider_name}_poll_interval_seconds",),
            default=3,
            minimum=1,
            maximum=30,
        )

    def acquire_phone(self) -> PhoneLease:
        raise NotImplementedError

    def prepare_for_sms(self, _lease: PhoneLease) -> None:
        return None

    def wait_for_code(self, lease: PhoneLease) -> str:
        raise NotImplementedError

    def mark_success(self, _lease: PhoneLease) -> None:
        return None

    def mark_failure(self, _lease: PhoneLease, _reason: str) -> None:
        return None

    def _proxies(self) -> dict[str, str] | None:
        return build_requests_proxy_config(self.proxy_url)

    def _log(self, message: str) -> None:
        self.log_fn(f"[{self.provider_name}] {message}")


class SmsBowerProvider(PhoneProvider):
    provider_name = "smsbower"

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None], proxy_url: str = ""):
        super().__init__(config, log_fn=log_fn, proxy_url=proxy_url)
        self.api_key = str(config.get("smsbower_api_key") or "").strip()
        if not self.api_key:
            raise RuntimeError("smsbower_api_key 未配置")
        self.base_url = str(config.get("smsbower_base_url") or "https://smsbower.page/stubs/handler_api.php").strip()
        self.service = str(config.get("smsbower_service") or "dr").strip()
        self.country = str(config.get("smsbower_country") or "0").strip()
        self.operator = str(config.get("smsbower_operator") or "").strip()
        self.max_price = _read_float(config, "smsbower_max_price", default=0.0)
        self.min_price = _read_float(config, "smsbower_min_price", default=0.0)

    def acquire_phone(self) -> PhoneLease:
        params: dict[str, Any] = {"service": self.service, "country": self.country}
        if self.operator:
            params["operator"] = self.operator
        if self.max_price > 0:
            params["maxPrice"] = self.max_price
        if self.min_price > 0:
            params["minPrice"] = self.min_price
        ok, text, data = self._request("getNumberV2", params=params, timeout=30)
        if ok and isinstance(data, dict):
            activation_id = str(data.get("activationId") or "").strip()
            phone = _normalize_phone(str(data.get("phoneNumber") or ""))
            if activation_id and phone:
                self._log(f"取号成功: {phone} id={activation_id} cost={data.get('activationCost') or '-'}")
                return PhoneLease(self.provider_name, activation_id, phone, data)
        if ok and text.upper().startswith("ACCESS_NUMBER:"):
            parts = text.split(":", 2)
            if len(parts) >= 3:
                activation_id = parts[1].strip()
                phone = _normalize_phone(parts[2])
                self._log(f"取号成功: {phone} id={activation_id}")
                return PhoneLease(self.provider_name, activation_id, phone, {"raw": text})
        raise RuntimeError(text or str(data) or "SmsBower 取号失败")

    def prepare_for_sms(self, lease: PhoneLease) -> None:
        self._request("setStatus", params={"id": lease.activation_id, "status": 1}, timeout=20)

    def wait_for_code(self, lease: PhoneLease) -> str:
        started_at = time.time()
        self._log(f"等待短信验证码，最长 {self.poll_timeout_seconds}s")
        while time.time() - started_at < self.poll_timeout_seconds:
            ok, text, _data = self._request("getStatus", params={"id": lease.activation_id}, timeout=20)
            upper = str(text or "").strip().upper()
            if ok and upper.startswith("STATUS_OK"):
                code = text.split(":", 1)[1].strip() if ":" in text else ""
                if code:
                    self._log("收到短信验证码")
                    return code
            if any(marker in upper for marker in ("STATUS_CANCEL", "NO_ACTIVATION", "BAD_STATUS")):
                raise RuntimeError(f"SmsBower 状态异常: {text}")
            time.sleep(self.poll_interval_seconds)
        return ""

    def mark_success(self, lease: PhoneLease) -> None:
        self._request("setStatus", params={"id": lease.activation_id, "status": 6}, timeout=20)

    def mark_failure(self, lease: PhoneLease, _reason: str) -> None:
        self._request("setStatus", params={"id": lease.activation_id, "status": 8}, timeout=20)

    def _request(self, action: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> tuple[bool, str, Any]:
        query = {"api_key": self.api_key, "action": action, **dict(params or {})}
        try:
            response = curl_requests.get(
                self.base_url,
                params=query,
                proxies=self._proxies(),
                timeout=timeout,
                impersonate="chrome142",
            )
        except Exception as exc:
            return False, f"REQUEST_ERROR: {exc}", None
        text = str(response.text or "").strip()
        try:
            data = response.json()
        except Exception:
            data = None
        if 200 <= response.status_code < 300 and not text.upper().startswith(("BAD_", "NO_", "ERROR")):
            return True, text, data
        return False, text or f"HTTP {response.status_code}", data


class FiveSimProvider(PhoneProvider):
    provider_name = "fivesim"

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None], proxy_url: str = ""):
        super().__init__(config, log_fn=log_fn, proxy_url=proxy_url)
        self.api_key = str(config.get("fivesim_api_key") or "").strip()
        if not self.api_key:
            raise RuntimeError("fivesim_api_key 未配置")
        self.service = str(config.get("fivesim_service") or "openai").strip()
        self.country = str(config.get("fivesim_country") or "any").strip()
        self.operator = str(config.get("fivesim_operator") or "any").strip() or "any"
        self.max_price = _read_float(config, "fivesim_max_price", default=0.0)
        self.fail_action = str(config.get("fivesim_fail_action") or "cancel").strip().lower()
        if self.fail_action not in {"cancel", "ban"}:
            self.fail_action = "cancel"

    def acquire_phone(self) -> PhoneLease:
        endpoint = f"user/buy/activation/{self.country or 'any'}/{self.operator or 'any'}/{self.service}"
        params = {"maxPrice": self.max_price} if self.max_price > 0 else None
        ok, text, data = self._request("GET", endpoint, params=params)
        if ok and isinstance(data, dict):
            activation_id = str(data.get("id") or "").strip()
            phone = _normalize_phone(str(data.get("phone") or ""))
            if activation_id and phone:
                self._log(f"取号成功: {phone} id={activation_id} price={data.get('price') or '-'}")
                return PhoneLease(self.provider_name, activation_id, phone, data)
        raise RuntimeError(text or str(data) or "5SIM 取号失败")

    def wait_for_code(self, lease: PhoneLease) -> str:
        started_at = time.time()
        self._log(f"等待短信验证码，最长 {self.poll_timeout_seconds}s")
        while time.time() - started_at < self.poll_timeout_seconds:
            ok, _text, data = self._request("GET", f"user/check/{lease.activation_id}")
            if ok and isinstance(data, dict):
                status = str(data.get("status") or "").upper()
                sms_list = data.get("sms") if isinstance(data.get("sms"), list) else []
                for sms in sms_list:
                    code = str((sms or {}).get("code") or "").strip()
                    if code:
                        self._log("收到短信验证码")
                        return code
                if status in {"CANCELED", "BANNED", "TIMEOUT"}:
                    raise RuntimeError(f"5SIM 状态异常: {status}")
            time.sleep(self.poll_interval_seconds)
        return ""

    def mark_success(self, lease: PhoneLease) -> None:
        self._request("GET", f"user/finish/{lease.activation_id}")

    def mark_failure(self, lease: PhoneLease, _reason: str) -> None:
        self._request("GET", f"user/{self.fail_action}/{lease.activation_id}")

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> tuple[bool, str, Any]:
        url = f"https://5sim.net/v1/{endpoint.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        try:
            response = curl_requests.request(
                method,
                url,
                headers=headers,
                params=params,
                proxies=self._proxies(),
                timeout=timeout,
                impersonate="chrome142",
            )
        except Exception as exc:
            return False, f"REQUEST_ERROR: {exc}", None
        try:
            data = response.json()
        except Exception:
            data = None
        text = str(response.text or "").strip()
        if 200 <= response.status_code < 300:
            return True, text, data
        if isinstance(data, dict) and data.get("message"):
            text = str(data.get("message"))
        return False, text or f"HTTP {response.status_code}", data


class SmsGiaReProvider(PhoneProvider):
    provider_name = "smsgiare"

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None], proxy_url: str = ""):
        super().__init__(config, log_fn=log_fn, proxy_url=proxy_url)
        self.token = str(config.get("smsgiare_token") or config.get("smsgiare_api_key") or "").strip()
        if not self.token:
            raise RuntimeError("smsgiare_token 未配置")
        self.base_url = str(config.get("smsgiare_base_url") or "https://api.smsgiare.io.vn/api/v1").strip().rstrip("/")
        self.service_id = _read_int(
            config,
            "smsgiare_service_id",
            fallback_keys=("smsgiare_service",),
            default=2653,
            minimum=1,
            maximum=999999,
        )
        self.carrier = str(config.get("smsgiare_carrier") or "ALL").strip().upper() or "ALL"
        self.reuse_phone_number = str(config.get("smsgiare_reuse_phone_number") or "").strip()

    def acquire_phone(self) -> PhoneLease:
        payload: dict[str, Any] = {
            "token": self.token,
            "serviceId": self.service_id,
            "carrier": self.carrier,
        }
        if self.reuse_phone_number:
            payload["reusePhoneNumber"] = self.reuse_phone_number
        ok, text, data = self._request("POST", "buy", json=payload, timeout=30)
        if ok and isinstance(data, dict):
            request_id = str(data.get("requestId") or "").strip()
            phone = _normalize_phone(str(data.get("phoneNum") or ""))
            if request_id and phone:
                self._log(f"取号成功: {phone} id={request_id} price={data.get('price') or '-'}")
                return PhoneLease(self.provider_name, request_id, phone, data)
        raise RuntimeError(text or str(data) or "SmsGiaRe 取号失败")

    def wait_for_code(self, lease: PhoneLease) -> str:
        started_at = time.time()
        self._log(f"等待短信验证码，最长 {self.poll_timeout_seconds}s")
        while time.time() - started_at < self.poll_timeout_seconds:
            ok, text, data = self._request("GET", "getcode", params={"requestId": lease.activation_id}, timeout=20)
            if ok and isinstance(data, dict):
                status = str(data.get("status") or "").strip()
                code = str(data.get("code") or "").strip()
                message = str(data.get("message") or "").strip().lower()
                if status == "11" and code:
                    self._log("收到短信验证码")
                    return code
                if status == "0" or message == "pending":
                    time.sleep(self.poll_interval_seconds)
                    continue
                if data.get("error"):
                    raise RuntimeError(f"SmsGiaRe 状态异常: {data.get('error')}")
            elif text:
                self._log(f"查询验证码失败: {text}")
            time.sleep(self.poll_interval_seconds)
        return ""

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> tuple[bool, str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = curl_requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                proxies=self._proxies(),
                timeout=timeout,
                impersonate="chrome142",
            )
        except Exception as exc:
            return False, f"REQUEST_ERROR: {exc}", None
        try:
            data = response.json()
        except Exception:
            data = None
        text = str(response.text or "").strip()
        if isinstance(data, dict) and data.get("error"):
            return False, str(data.get("error")), data
        if 200 <= response.status_code < 300:
            return True, text, data
        if isinstance(data, dict) and data.get("message"):
            text = str(data.get("message"))
        return False, text or f"HTTP {response.status_code}", data


def build_phone_provider(
    config: dict[str, Any] | None,
    *,
    log_fn: Callable[[str], None],
    proxy_url: str = "",
) -> PhoneProvider | None:
    values = dict(config or {})
    if not _truthy(values.get("phone_verification_enabled") or values.get("chatgpt_add_phone_enabled")):
        return None
    provider = str(values.get("phone_verification_provider") or values.get("sms_provider") or "smsbower").strip().lower()
    if provider in {"smsbower", "sms_bower"}:
        return SmsBowerProvider(values, log_fn=log_fn, proxy_url=proxy_url)
    if provider in {"fivesim", "5sim", "five_sim"}:
        return FiveSimProvider(values, log_fn=log_fn, proxy_url=proxy_url)
    if provider in {"smsgiare", "sms_giare"}:
        return SmsGiaReProvider(values, log_fn=log_fn, proxy_url=proxy_url)
    raise RuntimeError(f"不支持的接码平台: {provider}")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _read_int(
    values: dict[str, Any],
    key: str,
    *,
    fallback_keys: tuple[str, ...] = (),
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    for candidate in (key, *fallback_keys):
        if candidate not in values:
            continue
        try:
            parsed = int(values.get(candidate))
        except Exception:
            continue
        return max(minimum, min(parsed, maximum))
    return max(minimum, min(int(default), maximum))


def _read_float(values: dict[str, Any], key: str, *, default: float) -> float:
    try:
        return float(values.get(key))
    except Exception:
        return float(default)


def _normalize_phone(value: str) -> str:
    phone = str(value or "").strip().replace(" ", "")
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    return phone
