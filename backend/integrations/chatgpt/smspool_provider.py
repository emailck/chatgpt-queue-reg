from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from curl_cffi import requests as curl_requests
from sqlmodel import Session, select

from backend.core.db import session_scope
from backend.core.json_utils import json_dumps
from backend.core.time_utils import utcnow
from backend.integrations.chatgpt.phone_service import PhoneLease, PhoneProvider
from backend.models.smspool_phone import (
    SMSPOOL_PHONE_STATUS_AVAILABLE,
    SMSPOOL_PHONE_STATUS_BANNED,
    SMSPOOL_PHONE_STATUS_COOLING,
    SMSPOOL_PHONE_STATUS_CONSUMED,
    SMSPOOL_PHONE_STATUS_FAILED,
    SMSPOOL_PHONE_STATUS_IN_USE,
    SmsPoolPhone,
)




def _as_utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _read_int(
    values: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(values.get(key))
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _read_float(values: dict[str, Any], key: str, *, default: float) -> float:
    try:
        return float(values.get(key))
    except Exception:
        return float(default)


def _split_csv(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = []
    for chunk in raw.replace(";", ",").replace("|", ",").split(","):
        piece = chunk.strip()
        if piece:
            parts.append(piece)
    return parts


def _normalize_phone(value: str) -> str:
    phone = str(value or "").strip().replace(" ", "")
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


def _parse_smspool_phone(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    raw_number = str(data.get("number") or "").strip()
    if raw_number:
        digits = "".join(ch for ch in raw_number if ch.isdigit())
        if digits:
            return f"+{digits}"

    cc = str(data.get("cc") or data.get("countryCode") or data.get("country_code") or "").strip()
    local = str(data.get("phonenumber") or data.get("phoneNumber") or data.get("phone") or data.get("number") or "").strip()
    digits = "".join(ch for ch in local if ch.isdigit())
    if digits:
        cc_digits = "".join(ch for ch in cc if ch.isdigit())
        if cc_digits and not digits.startswith(cc_digits):
            return f"+{cc_digits}{digits}"
        return f"+{digits}"
    return ""


def _normalize_sms_code(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.isdigit() and 4 <= len(raw) <= 6:
        return raw
    return _extract_sms_code_from_text(raw)


def _extract_sms_code_from_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    patterns = (
        r"(?i)(?:code|otp|pin|验证码|verification(?:\s+code)?)[^0-9]{0,30}(\d{4,6})\b",
        r"\b(\d{4,6})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _short_sms_resp(text: str, data: Any) -> str:
    if isinstance(data, dict):
        # 这里不再只截取 status；SMSPool 的数字 status 无法单独判断是否成功，
        # 需要看 sms/code/message 等完整字段，排查时保留完整响应预览。
        preview = dict(data)
        if "key" in preview:
            preview["key"] = "***"
        return json_dumps(preview, fallback={})[:500]
    return str(text or data or "")[:500]


def _reuse_lease_log_message(lease: PhoneLease) -> str:
    success_count = ""
    metadata = getattr(lease, "metadata", None)
    if isinstance(metadata, dict):
        success_count = metadata.get("success_count", "")
    return f"复用号码: {lease.phone_number} orderid={lease.activation_id} success_count={success_count}"


class SmsPoolProvider(PhoneProvider):
    provider_name = "smspool"

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None], proxy_url: str = ""):
        super().__init__(config, log_fn=log_fn, proxy_url=proxy_url)
        self.api_key = str(
            config.get("smspool_api_key")
            or config.get("apiKey")
            or config.get("sms_pool_api_key")
            or ""
        ).strip()
        if not self.api_key:
            raise RuntimeError("smspool_api_key 未配置")
        self.base_url = str(config.get("smspool_base_url") or "https://api.smspool.net").strip().rstrip("/")
        self.service = str(config.get("smspool_service") or "openai").strip()
        self.pool = str(config.get("smspool_pool") or "").strip()
        self.country = str(config.get("smspool_country") or "").strip()
        self.countries = _split_csv(config.get("smspool_countries") or self.country or "0")
        if not self.countries:
            self.countries = [self.country or "0"]
        self.max_price = _read_float(config, "smspool_max_price", default=0.0)
        self.pricing_option = str(config.get("smspool_pricing_option") or "").strip()
        self.max_reuses = _read_int(config, "smspool_max_reuses", default=3, minimum=1, maximum=10)
        self.reuse_cooldown_seconds = _read_int(
            config,
            "smspool_reuse_cooldown_seconds",
            default=1800,
            minimum=60,
            maximum=86400,
        )
        self.purchase_enabled = _truthy(config.get("smspool_purchase_enabled", True))
        self.reuse_enabled = _truthy(config.get("smspool_reuse_enabled", True))

    def acquire_phone(self) -> PhoneLease:
        if self.reuse_enabled:
            reused = self._acquire_reusable_phone()
            if reused is not None:
                return reused
        if not self.purchase_enabled:
            raise RuntimeError("smspool_purchase_enabled 未开启，且没有可复用号码")

        last_error = ""
        countries = list(dict.fromkeys([c for c in self.countries if c]))
        random.shuffle(countries)
        for country in countries:
            pools = self._candidate_pools(country)
            if not pools:
                pools = [self.pool] if self.pool else [""]
            for pool in pools:
                ok, text, data = self._purchase(country=country, pool=pool)
                lease = self._parse_purchase_response(ok, text, data, country=country, pool=pool)
                if lease is not None:
                    self._persist_lease(lease, country=country, pool=pool, raw=data, status=SMSPOOL_PHONE_STATUS_IN_USE)
                    return lease
                last_error = text or str(data) or "SmsPool 取号失败"
                self._log(f"国家 {country} / pool {pool or '-'} 取号失败: {last_error}")
        raise RuntimeError(last_error or "SmsPool 取号失败")

    def prepare_for_sms(self, _lease: PhoneLease) -> None:
        return None

    def request_resend(self, lease: PhoneLease) -> None:
        metadata = getattr(lease, "metadata", None) or {}
        if not isinstance(metadata, dict) or not metadata.get("reused"):
            return None
        ok, text, data = self._resend_sms(lease.activation_id)
        if ok:
            self._log(f"resend 成功: orderid={lease.activation_id} resp={text[:180] or data}")
            return None
        raise RuntimeError(f"resend 失败: orderid={lease.activation_id} resp={text[:180] or data}")

    def wait_for_code(self, lease: PhoneLease) -> str:
        started_at = time.time()
        self._log(f"等待短信验证码，最长 {self.poll_timeout_seconds}s")
        checks = 0
        while time.time() - started_at < self.poll_timeout_seconds:
            checks += 1
            ok, text, data = self._check_sms(lease.activation_id)
            code = self._extract_code(ok, text, data)
            if code:
                self._log(f"收到短信验证码: len={len(code)} check={checks}")
                return code
            if checks <= 3 or checks % 5 == 0:
                self._log(f"短信未到: check={checks} resp={_short_sms_resp(text, data)}")
            time.sleep(self.poll_interval_seconds)
        return ""

    def mark_success(self, lease: PhoneLease) -> None:
        self._update_lease(
            lease.activation_id,
            status=SMSPOOL_PHONE_STATUS_COOLING if self.max_reuses > 1 else SMSPOOL_PHONE_STATUS_CONSUMED,
            success_count_delta=1,
            last_error="",
            last_error_kind="",
            set_cooldown=True,
        )

    def mark_failure(self, lease: PhoneLease, reason: str) -> None:
        kind = self._classify_failure_reason(reason)
        should_cancel = False
        success_count = 0
        with session_scope() as s:
            row = self._get_row(s, lease.activation_id)
            if row is None:
                return
            now = utcnow()
            success_count = int(row.success_count or 0)
            row.last_error = str(reason or "")
            row.last_error_kind = kind
            row.last_used_at = now
            row.updated_at = now
            if row.success_count > 0 and kind == "transient":
                row.status = SMSPOOL_PHONE_STATUS_COOLING
                row.cooldown_until = now + timedelta(seconds=self.reuse_cooldown_seconds)
            elif row.success_count > 0 and kind == "number":
                row.status = SMSPOOL_PHONE_STATUS_BANNED
                row.cooldown_until = None
            else:
                row.status = SMSPOOL_PHONE_STATUS_FAILED
                row.cooldown_until = None
            row.locked_until = None
            s.add(row)
        should_cancel = success_count == 0 or kind == "number" or self._looks_like_cancel_candidate(reason)
        if should_cancel:
            self._log(
                "失败后尝试退款/取消号码: "
                f"orderid={lease.activation_id} phone={lease.phone_number} "
                f"success_count={success_count} kind={kind}"
            )
            try:
                ok, text, data = self._cancel_sms(lease.activation_id)
                if ok:
                    self._log(
                        "退款/取消成功: "
                        f"orderid={lease.activation_id} resp={text[:180] or data}"
                    )
                else:
                    self._log(
                        "退款/取消失败: "
                        f"orderid={lease.activation_id} resp={text[:180] or data}"
                    )
            except Exception as exc:
                self._log(f"退款/取消异常（忽略）: orderid={lease.activation_id} err={exc}")

    def _acquire_reusable_phone(self) -> PhoneLease | None:
        now = utcnow()
        with session_scope() as s:
            rows = list(
                s.exec(
                    select(SmsPoolPhone)
                    .where(SmsPoolPhone.provider == self.provider_name)
                    .where(SmsPoolPhone.success_count > 0)
                    .where(SmsPoolPhone.success_count < self.max_reuses)
                ).all()
            )
            rows = [
                r
                for r in rows
                if str(r.status or "").lower()
                not in {
                    SMSPOOL_PHONE_STATUS_FAILED,
                    SMSPOOL_PHONE_STATUS_BANNED,
                    SMSPOOL_PHONE_STATUS_CONSUMED,
                }
            ]
            if self.pool:
                rows = [r for r in rows if str(r.pool or "") == self.pool]
            rows = [r for r in rows if self._country_matches(r.country)]
            rows.sort(
                key=lambda r: (
                    _as_utc_aware(r.last_success_at or r.created_at) or now,
                    _as_utc_aware(r.last_used_at or r.created_at) or now,
                    _as_utc_aware(r.created_at) or now,
                )
            )
            lease: PhoneLease | None = None
            for row in rows:
                locked_until = _as_utc_aware(row.locked_until)
                if locked_until and locked_until > now:
                    continue
                last_touch = max(
                    _as_utc_aware(row.last_success_at or row.created_at) or now,
                    _as_utc_aware(row.last_used_at or row.created_at) or now,
                )
                if (now - last_touch).total_seconds() < self.reuse_cooldown_seconds:
                    continue
                row.status = SMSPOOL_PHONE_STATUS_IN_USE
                row.last_used_at = now
                row.locked_until = now + timedelta(seconds=self.poll_timeout_seconds + 600)
                row.updated_at = now
                s.add(row)
                s.flush()
                lease = PhoneLease(
                    self.provider_name,
                    str(row.orderid or row.phone),
                    row.phone,
                    {
                        "reused": True,
                        "record_id": row.id,
                        "country": row.country,
                        "service": row.service,
                        "pool": row.pool,
                        "success_count": row.success_count,
                    },
                )
                break
            else:
                return None
        if lease is None:
            return None
        self._log(_reuse_lease_log_message(lease))
        return lease

    def _candidate_pools(self, country: str) -> list[str]:
        if self.pool:
            return [self.pool]
        ok, text, data = self._request("POST", "/pool/retrieve_valid", payload={"country": country, "service": self.service, "web": 1}, include_key=False)
        if not ok:
            self._log(f"pool/retrieve_valid 失败 country={country}: {text}")
            return []
        items = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
        pools: list[str] = []
        for item in items or []:
            if isinstance(item, dict):
                pool_id = str(item.get("id") or item.get("pool") or item.get("pool_id") or item.get("name") or "").strip()
                if pool_id:
                    pools.append(pool_id)
        return list(dict.fromkeys(pools))

    def _purchase(self, *, country: str, pool: str) -> tuple[bool, str, Any]:
        payload: dict[str, Any] = {
            "country": country,
            "service": self.service,
            "quantity": 1,
        }
        if pool:
            payload["pool"] = pool
        if self.max_price > 0:
            payload["max_price"] = self.max_price
        if self.pricing_option:
            payload["pricing_option"] = self.pricing_option
        return self._request("POST", "/purchase/sms", payload=payload)

    def _check_sms(self, orderid: str) -> tuple[bool, str, Any]:
        return self._request("POST", "/sms/check", payload={"orderid": orderid})

    def _cancel_sms(self, orderid: str) -> tuple[bool, str, Any]:
        return self._request("POST", "/sms/cancel", payload={"orderid": orderid})

    def _resend_sms(self, orderid: str) -> tuple[bool, str, Any]:
        return self._request("POST", "/sms/resend", payload={"orderid": orderid})

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        include_key: bool = True,
        timeout: int | None = None,
    ) -> tuple[bool, str, Any]:
        url = f"{self.base_url}{path}"
        data = dict(payload or {})
        if include_key:
            data = {"key": self.api_key, **data}
        try:
            response = curl_requests.request(
                method,
                url,
                data=data,
                proxies=self._proxies(),
                timeout=timeout or 30,
                impersonate="chrome142",
            )
        except Exception as exc:
            return False, f"REQUEST_ERROR: {exc}", None
        text = str(response.text or "").strip()
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        if 200 <= response.status_code < 300:
            return True, text, parsed
        return False, text or f"HTTP {response.status_code}", parsed

    def _parse_purchase_response(self, ok: bool, text: str, data: Any, *, country: str, pool: str) -> PhoneLease | None:
        if ok and isinstance(data, dict):
            orderid = str(data.get("orderid") or data.get("order_id") or data.get("id") or "").strip()
            phone = _parse_smspool_phone(data)
            if orderid and phone:
                self._log(f"取号成功: {phone} orderid={orderid} country={country} pool={pool or '-'}")
                return PhoneLease(
                    self.provider_name,
                    orderid,
                    phone,
                    {
                        "raw": data,
                        "country": country,
                        "pool": pool,
                        "service": self.service,
                    },
                )
        return None

    def _extract_code(self, ok: bool, text: str, data: Any) -> str:
        if not ok:
            return ""
        if isinstance(data, dict):
            success = data.get("success")
            status = str(data.get("status") or data.get("status_code") or "").strip().lower()
            code = str(data.get("code") or data.get("pin") or data.get("otp") or "").strip()
            code = _normalize_sms_code(code)
            if code:
                return code
            sms = str(data.get("sms") or data.get("message") or "").strip()
            # SmsPool 的 pending / refund / purchase message 里也可能有金额、order、
            # expires 等数字，不能从任意 message 直接抽数字。只有明确成功/收到
            # 状态时才从短信正文里解析 4-6 位验证码。
            received = (
                success in {1, "1", True}
                or status in {"received", "complete", "completed", "ok", "done", "sms_received"}
                or any(str(data.get(k) or "").strip() for k in ("sms", "code", "pin", "otp"))
            )
            if received and sms:
                code = _extract_sms_code_from_text(sms)
                if code:
                    return code
        if text:
            stripped = text.strip()
            # 兼容少数纯文本成功响应，拒绝从 JSON/pending 文本中盲抽数字。
            m = re.search(r"(?i)(?:status_ok|code|otp|pin|验证码|verification)[^0-9]{0,20}(\d{4,6})\b", stripped)
            if m:
                return m.group(1)
            if stripped.isdigit() and 4 <= len(stripped) <= 6:
                return stripped
        return ""

    def _persist_lease(self, lease: PhoneLease, *, country: str, pool: str, raw: Any, status: str) -> None:
        now = utcnow()
        with session_scope() as s:
            row = self._get_row(s, lease.activation_id)
            if row is None:
                row = SmsPoolPhone(
                    provider=self.provider_name,
                    country=str(country or ""),
                    service=self.service,
                    pool=str(pool or ""),
                    phone=lease.phone_number,
                    orderid=lease.activation_id,
                    status=status,
                    success_count=0,
                    locked_until=now + timedelta(seconds=self.poll_timeout_seconds + 600),
                    last_used_at=now,
                    metadata_json=json_dumps(raw, fallback={}),
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
                return
            row.country = str(country or row.country or "")
            row.service = self.service
            row.pool = str(pool or row.pool or "")
            row.phone = lease.phone_number or row.phone
            row.status = status
            row.locked_until = now + timedelta(seconds=self.poll_timeout_seconds + 600)
            row.last_used_at = now
            row.metadata_json = json_dumps(raw, fallback={})
            row.updated_at = now
            s.add(row)

    def _update_lease(
        self,
        orderid: str,
        *,
        status: str,
        success_count_delta: int = 0,
        last_error: str = "",
        last_error_kind: str = "",
        set_cooldown: bool = False,
    ) -> None:
        with session_scope() as s:
            row = self._get_row(s, orderid)
            if row is None:
                return
            now = utcnow()
            row.success_count = max(0, int(row.success_count or 0) + success_count_delta)
            row.status = status
            row.last_error = last_error
            row.last_error_kind = last_error_kind
            row.last_used_at = now
            row.last_success_at = now if success_count_delta > 0 else row.last_success_at
            row.cooldown_until = now + timedelta(seconds=self.reuse_cooldown_seconds) if set_cooldown else None
            row.locked_until = None
            row.updated_at = now
            s.add(row)

    def _get_row(self, session: Session, orderid: str) -> SmsPoolPhone | None:
        return session.exec(
            select(SmsPoolPhone)
            .where(SmsPoolPhone.provider == self.provider_name)
            .where(SmsPoolPhone.orderid == str(orderid))
        ).first()

    def _country_matches(self, country: str) -> bool:
        configured = [c for c in self.countries if c]
        if not configured:
            return True
        if country in configured:
            return True
        if "0" in configured or "any" in {c.lower() for c in configured}:
            return True
        return False

    def _classify_failure_reason(self, reason: str) -> str:
        text = str(reason or "").lower()
        number_markers = [
            "phone number",
            "phone_number",
            "invalid phone",
            "invalid number",
            "invalid_phone_num",
            "number banned",
            "already used",
            "blacklist",
            "blacklisted",
            "carrier",
            "unsupported country",
            "unsupported carrier",
            "disallowed",
            "too many requests for this number",
            "sms pool number",
            "suspicious behavior",
            "similar to yours",
            "phone numbers similar",
        ]
        if any(marker in text for marker in number_markers):
            return "number"
        transient_markers = [
            "invalid_auth_step",
            "stale auth step",
            "redirected to login",
            "session",
            "rate limit",
            "timeout",
            "http 429",
            "http 5",
            "temporary",
            "risk",
            "captcha",
            "forbidden",
            "blocked",
            "review",
        ]
        if any(marker in text for marker in transient_markers):
            return "transient"
        return "transient"

    def _looks_like_cancel_candidate(self, reason: str) -> bool:
        text = str(reason or "").lower()
        return any(
            marker in text
            for marker in (
                "unsupported",
                "invalid",
                "blocked",
                "blacklisted",
                "already used",
            )
        )
