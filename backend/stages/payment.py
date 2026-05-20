"""payment stage: backend PayPal HTTP protocol flow."""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from backend.core.constants import (
    PAYMENT_LINK_STATUS_EMPTY_PAYMENT_PENDING,
    PAYMENT_LINK_STATUS_FAILED,
    PAYMENT_LINK_STATUS_PAID_UNKNOWN,
)
from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.json_utils import json_loads
from backend.core.pools.base import AcquireOutcome
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.payment import PaymentLink
from backend.schemas.stage_io import PaymentInput, PaymentOutput


@stage(
    name="payment",
    requires_resources=[],
    optional_resources=["paypal_number_pool", "proxy_pool"],
    default_concurrency=2,
    input_schema=PaymentInput,
    output_schema=PaymentOutput,
    description="Run backend PayPal HTTP payment protocol for an existing checkout session.",
)
def run(ctx: JobContext) -> None:
    payment_link_id = ctx.payment_link_id or int(ctx.input.get("payment_link_id") or 0) or None
    if not payment_link_id:
        raise RuntimeError("payment stage requires payment_link_id")
    ctx.attach_payment_link(payment_link_id)

    payload = dict(ctx.input or {})
    extra_config = _as_dict(payload.get("extra_config"))
    merged_extra = {**settings.get_all(), **extra_config}
    fresh_checkout_cfg = _as_dict(merged_extra.get("fresh_checkout"))
    fresh_plan_cfg = _as_dict(fresh_checkout_cfg.get("plan"))

    with Session(engine) as s:
        payment_link = s.get(PaymentLink, payment_link_id)
        if payment_link is None:
            raise RuntimeError(f"payment_link {payment_link_id} not found")
        account_id = ctx.account_id or int(payload.get("account_id") or 0) or int(payment_link.account_id or 0)
        if not account_id:
            raise RuntimeError("payment stage requires account_id")
        account_row = s.get(ChatGPTAccount, account_id)
        if account_row is None:
            raise RuntimeError(f"account {account_id} not found")
        checkout_url = str(payment_link.checkout_url or "")
        checkout_session_id = str(payment_link.checkout_session_id or "")
        plan = str(payment_link.plan or "")

    ctx.attach_account(account_id)
    identity = ctx.identity

    payment_region = str(
        payload.get("payment_proxy_region")
        or merged_extra.get("payment_proxy_region")
        or settings.get("workpool.payment.proxy_region", "")
        or ""
    ).strip()
    explicit_payment_proxy = _proxy_url_from_config(
        payload.get("payment_proxy_url")
        or merged_extra.get("payment_proxy_url")
        or merged_extra.get("paypal_proxy_url")
        or fresh_checkout_cfg.get("payment_proxy")
        or ""
    )
    payment_proxy = None
    payment_proxy_url = explicit_payment_proxy
    if not payment_proxy_url:
        if not payment_region:
            raise RuntimeError("payment stage requires payment_proxy_region/region")
        payment_proxy = ctx.acquire(
            "proxy_pool",
            hint={
                "region": payment_region,
                "exclude_proxy_id": identity.proxy_id if identity else None,
                "exclude_url": identity.proxy_url if identity else "",
            },
            auto_outcome_on_success=AcquireOutcome.REUSABLE.value,
            auto_outcome_on_failure=AcquireOutcome.FAILED.value,
        )
        payment_proxy_url = str((payment_proxy.payload or {}).get("url") or "")
    payment_proxy_actual_region = str((payment_proxy.payload or {}).get("region") or "") if payment_proxy else payment_region

    paypal_cfg = _section_config(
        merged_extra,
        "paypal",
        aliases=("paypal_config", "paypal_json"),
        flat_map={
            "paypal_email": "email",
            "paypal_password": "password",
            "paypal_cookies": "cookies",
            "paypal_cookie_header": "cookies",
        },
    )
    billing_cfg = _section_config(
        merged_extra,
        "billing",
        aliases=("paypal_billing", "payment_billing", "billing_details"),
        flat_map={
            "billing_name": "name",
            "billing_email": "email",
            "billing_country": "country",
            "billing_line1": "line1",
            "billing_city": "city",
            "billing_state": "state",
            "billing_postal": "postal_code",
            "billing_postal_code": "postal_code",
        },
    )
    runtime_cfg = _section_config(
        merged_extra,
        "runtime",
        aliases=("stripe_runtime", "paypal_runtime"),
        flat_map={
            "stripe_runtime_version": "version",
            "stripe_js_checksum": "js_checksum",
            "stripe_rv_timestamp": "rv_timestamp",
            "stripe_passive_captcha_token": "passive_captcha_token",
            "stripe_passive_captcha_ekey": "passive_captcha_ekey",
            "stripe_checkout_config_id": "checkout_config_id",
            "stripe_top_checkout_config_id": "top_checkout_config_id",
            "stripe_payment_method_checkout_config_id": "payment_method_checkout_config_id",
            "paypal_address_autocomplete": "paypal_address_autocomplete",
            "paypal_address_session_id": "paypal_address_session_id",
            "paypal_address_location": "paypal_address_location",
            "paypal_address_place_id": "paypal_address_place_id",
        },
    )
    stripe_cfg = _section_config(
        merged_extra,
        "stripe",
        aliases=("stripe_config",),
        flat_map={
            "stripe_publishable_key": "publishable_key",
            "publishable_key": "publishable_key",
        },
    )
    captcha_cfg = _section_config(
        merged_extra,
        "captcha",
        aliases=("captcha_config",),
        flat_map={
            "captcha_api_key": "api_key",
            "captcha_api_url": "api_url",
        },
    )
    if captcha_cfg and "captcha" not in paypal_cfg:
        paypal_cfg["captcha"] = captcha_cfg

    paypal_number = None
    configured_phone = str(
        paypal_cfg.get("phone")
        or paypal_cfg.get("phone_number")
        or merged_extra.get("paypal_phone")
        or ""
    ).strip()
    configured_smsurl = str(paypal_cfg.get("smsurl") or merged_extra.get("paypal_smsurl") or "").strip()
    if configured_phone:
        paypal_cfg["phone"] = configured_phone
        paypal_cfg["smsurl"] = configured_smsurl
    else:
        paypal_number = ctx.acquire(
            "paypal_number_pool",
            auto_outcome_on_success=AcquireOutcome.CONSUMED.value,
            auto_outcome_on_failure=AcquireOutcome.FAILED.value,
        )
        paypal_cfg["phone"] = str((paypal_number.payload or {}).get("phone") or "")
        paypal_cfg["smsurl"] = str((paypal_number.payload or {}).get("smsurl") or "")

    if fresh_plan_cfg:
        billing_defaults = {
            "country": fresh_plan_cfg.get("billing_country") or fresh_plan_cfg.get("country") or "",
            "currency": fresh_plan_cfg.get("billing_currency") or fresh_plan_cfg.get("currency") or "",
        }
        billing_cfg = {**billing_defaults, **{k: v for k, v in billing_cfg.items() if v not in (None, "")}}
        runtime_cfg = {
            **{
                "checkout_config_id": fresh_plan_cfg.get("checkout_config_id") or "",
                "top_checkout_config_id": fresh_plan_cfg.get("top_checkout_config_id") or "",
                "payment_method_checkout_config_id": fresh_plan_cfg.get("payment_method_checkout_config_id") or "",
            },
            **{k: v for k, v in runtime_cfg.items() if v not in (None, "")},
        }
    if "email" not in billing_cfg or not billing_cfg.get("email"):
        billing_cfg["email"] = str(account_row.email or "")

    ctx.log(
        "starting payment stage (paypal_http)",
        payload={
            "payment_link_id": payment_link_id,
            "account_id": account_id,
            "checkout_session_id": checkout_session_id,
            "plan": plan,
            "payment_proxy_id": (payment_proxy.payload or {}).get("proxy_id") if payment_proxy else None,
            "payment_proxy_region": payment_region,
            "payment_proxy_actual_region": payment_proxy_actual_region,
            "payment_proxy_explicit": bool(explicit_payment_proxy),
            "paypal_has_cookies": bool(paypal_cfg.get("cookies") or paypal_cfg.get("cookie_header")),
            "paypal_number_id": (paypal_number.payload or {}).get("id") if paypal_number else None,
            "paypal_has_phone": bool(paypal_cfg.get("phone")),
            "runtime_has_js_checksum": bool(runtime_cfg.get("js_checksum")),
            "runtime_has_rv_timestamp": bool(runtime_cfg.get("rv_timestamp")),
        },
    )

    account_adapter = _AccountAdapter(account_row)

    def _log(message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
        ctx.log(message, level=level, payload=payload)

    try:
        from backend.integrations.chatgpt.paypal_http import run_paypal_http_payment

        result = run_paypal_http_payment(
            account=account_adapter,
            checkout_url=checkout_url,
            checkout_session_id=checkout_session_id,
            paypal=paypal_cfg,
            billing=billing_cfg,
            runtime=runtime_cfg,
            stripe=stripe_cfg,
            proxy_url=payment_proxy_url,
            chatgpt_proxy_url=ctx.effective_proxy_url() or "",
            log=_log,
            check_cancelled=ctx.check_cancelled,
        )
    except Exception as exc:
        with session_scope() as s:
            row = s.get(PaymentLink, payment_link_id)
            if row is not None:
                row.status = PAYMENT_LINK_STATUS_FAILED
                row.error = str(exc)[:2000]
                row.updated_at = utcnow()
                s.add(row)
        ctx.log(f"payment paypal_http failed: {exc}", level="error")
        raise

    state = str(result.get("state") or "")
    link_status = PAYMENT_LINK_STATUS_PAID_UNKNOWN if state == "succeeded" else PAYMENT_LINK_STATUS_EMPTY_PAYMENT_PENDING
    with session_scope() as s:
        row = s.get(PaymentLink, payment_link_id)
        if row is not None:
            row.status = link_status
            row.error = ""
            row.updated_at = utcnow()
            s.add(row)

    ctx.update_result({
        "state": state,
        "payment_link_id": payment_link_id,
        "account_id": account_id,
        "checkout_session_id": result.get("checkout_session_id") or checkout_session_id,
        "payment_method_id": result.get("payment_method_id") or "",
        "payment_proxy_id": (payment_proxy.payload or {}).get("proxy_id") if payment_proxy else None,
        "payment_proxy_region": payment_region,
        "payment_proxy_actual_region": payment_proxy_actual_region,
        "paypal_number_id": (paypal_number.payload or {}).get("id") if paypal_number else None,
        "paypal_ba_token": result.get("paypal_ba_token") or "",
        "paypal_ec_token": result.get("paypal_ec_token") or "",
        "stripe_payment_status": result.get("stripe_payment_status") or "",
        "stripe_redirect_url": result.get("stripe_redirect_url") or "",
        "paypal_final_url": result.get("paypal_final_url") or "",
    })
    ctx.log(f"paypal_http payment finished state={state}")


class _AccountAdapter:
    def __init__(self, account: ChatGPTAccount) -> None:
        cookies = json_loads(account.cookies_json, fallback=[]) or []
        self.cookies = "; ".join(
            f"{c.get('name')}={c.get('value')}"
            for c in cookies
            if isinstance(c, dict) and c.get("name") and c.get("value")
        )
        meta = json_loads(account.metadata_json, fallback={}) or {}
        fingerprint = json_loads(account.browser_fingerprint_json, fallback={}) or {}
        self.account = account
        self.email = account.email
        self.access_token = account.access_token
        self.user_agent = account.user_agent
        self.browser_fingerprint = fingerprint
        self.extra = {
            **meta,
            "browser_fingerprint": fingerprint,
            "user_agent": account.user_agent,
            "x_oai_is": meta.get("x_oai_is") or "",
            "oai_client_version": meta.get("oai_client_version") or "",
            "oai_client_build_number": meta.get("oai_client_build_number") or "",
            "cookies": self.cookies,
        }
        self.oai_client_version = str(meta.get("oai_client_version") or "")
        self.oai_client_build_number = str(meta.get("oai_client_build_number") or "")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _proxy_url_from_config(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    if value.get("url"):
        return str(value.get("url") or "").strip()
    host = str(value.get("host") or "").strip()
    if not host:
        return ""
    port = value.get("port")
    user = str(value.get("user") or value.get("username") or "").strip()
    password = str(value.get("pass") or value.get("password") or "").strip()
    scheme = str(value.get("scheme") or "http").strip() or "http"
    auth = f"{user}:{password}@" if user and password else ""
    suffix = f":{port}" if port not in (None, "") else ""
    return f"{scheme}://{auth}{host}{suffix}"


def _section_config(
    source: dict[str, Any],
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    flat_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (name, *aliases):
        result.update(_as_dict(source.get(key)))
    prefix = name + "."
    for key, value in source.items():
        if key.startswith(prefix):
            result[key[len(prefix):]] = value
    for key, dest in (flat_map or {}).items():
        value = source.get(key)
        if value not in (None, ""):
            result[dest] = value
    return result
