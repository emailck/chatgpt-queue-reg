"""pp_long_link stage.

Generate a PayPal BA approve long-link for a ChatGPT Plus trial/checkout using
HTTP logic ported from the uploaded app.py.
"""
from __future__ import annotations

import re
import time
from typing import Any

from sqlmodel import Session

from backend.core.constants import PAYMENT_LINK_STATUS_CREATED, PAYMENT_LINK_STATUS_FAILED
from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps, json_loads
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.payment import PaymentLink
from backend.schemas.stage_io import PPLongLinkInput, PPLongLinkOutput


@stage(
    name="pp_long_link",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=PPLongLinkInput,
    output_schema=PPLongLinkOutput,
    description="Generate PayPal long-link using uploaded app.py OPLL flow.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    account_id = ctx.account_id or _to_int(payload.get("account_id"))
    access_token = str(payload.get("access_token") or "").strip()
    account: ChatGPTAccount | None = None
    if account_id:
        ctx.attach_account(account_id)
        with Session(engine) as s:
            account = s.get(ChatGPTAccount, int(account_id))
            if account is None:
                raise RuntimeError(f"account {account_id} not found")
            access_token = access_token or str(account.access_token or "").strip()
    if not access_token:
        raise RuntimeError("pp_long_link requires account_id with access_token or direct access_token")

    country = str(payload.get("country") or settings.get("workpool.pp_long_link.country", "US") or "US").strip().upper()
    currency = str(payload.get("currency") or settings.get("workpool.pp_long_link.currency", "USD") or "USD").strip().upper()
    target_amount = str(payload.get("target_amount") or settings.get("workpool.pp_long_link.target_amount", "") or "").strip()
    # max_retries means “failed retries after the first attempt”. Total attempts = max_retries + 1.
    max_retries = _bounded_int(payload.get("max_retries", settings.get("workpool.pp_long_link.max_retries", 3)), default=3, minimum=0, maximum=20)
    retry_backoff_ms = _bounded_int(payload.get("retry_backoff_ms", settings.get("workpool.pp_long_link.retry_backoff_ms", 5000)), default=5000, minimum=0, maximum=300000)

    payload_proxy = str(payload.get("proxy_url") or "").strip()
    configured_proxy = str(settings.get("workpool.pp_long_link.proxy_url", "") or "").strip()
    account_proxy = str(ctx.effective_proxy_url() or "").strip()
    # pp_long_link has its own proxy setting. Priority:
    #   task input override > workpool.pp_long_link.proxy_url > account/register proxy.
    base_proxy = payload_proxy or configured_proxy or account_proxy
    proxy_source = "input" if payload_proxy else ("pp_long_link_config" if configured_proxy else ("account" if account_proxy else "none"))
    create_proxy_url = str(payload.get("create_proxy_url") or settings.get("workpool.pp_long_link.create_proxy_url", "") or base_proxy).strip()
    followup_proxy_url = str(payload.get("followup_proxy_url") or settings.get("workpool.pp_long_link.followup_proxy_url", "") or create_proxy_url).strip()
    approve_proxy_url = str(payload.get("approve_proxy_url") or settings.get("workpool.pp_long_link.approve_proxy_url", "") or followup_proxy_url).strip()

    ctx.log("starting pp_long_link", payload={
        "account_id": account_id,
        "email": account.email if account else str(payload.get("email") or ""),
        "country": country,
        "currency": currency,
        "has_create_proxy": bool(create_proxy_url),
        "has_followup_proxy": bool(followup_proxy_url),
        "has_approve_proxy": bool(approve_proxy_url),
        "proxy_source": proxy_source,
        "target_amount": target_amount,
        "max_retries": max_retries,
        "retry_backoff_ms": retry_backoff_ms,
    })

    result: dict[str, Any] = {}
    long_url = ""
    last_exc: Exception | None = None
    from backend.integrations.opll_app import generate_paypal_long_link
    for attempt in range(max_retries + 1):
        ctx.check_cancelled()
        try:
            if attempt > 0:
                ctx.log(f"pp_long_link retry {attempt}/{max_retries}", payload={"attempt": attempt + 1, "total_attempts": max_retries + 1})
            result = generate_paypal_long_link(
                access_token=access_token,
                country=country,
                currency=currency,
                create_proxy_url=create_proxy_url,
                followup_proxy_url=followup_proxy_url,
                approve_proxy_url=approve_proxy_url,
                target_amount=target_amount,
            )
            long_url = str(result.get("long_url") or result.get("provider_redirect_url") or "").strip()
            if not long_url:
                raise RuntimeError(f"pp_long_link result missing long_url: {str(result)[:500]}")
            break
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                payment_link_id = _record_payment_link(
                    ctx=ctx,
                    account_id=int(account_id or 0),
                    status=PAYMENT_LINK_STATUS_FAILED,
                    checkout_url="",
                    checkout_session_id="",
                    payload={
                        "country": country,
                        "currency": currency,
                        "target_amount": target_amount,
                        "provider": "paypal",
                        "max_retries": max_retries,
                        "attempts": attempt + 1,
                    },
                    error=str(exc),
                )
                ctx.attach_payment_link(payment_link_id)
                ctx.log(f"pp_long_link failed after {attempt + 1} attempts: {exc}", level="error")
                raise
            wait_ms = retry_backoff_ms * (attempt + 1)
            ctx.log(
                f"pp_long_link attempt {attempt + 1}/{max_retries + 1} failed, retry after {wait_ms}ms: {exc}",
                level="warning",
                payload={"attempt": attempt + 1, "max_retries": max_retries, "retry_after_ms": wait_ms},
            )
            if wait_ms > 0:
                time.sleep(wait_ms / 1000)
    if not long_url:
        raise RuntimeError(f"pp_long_link failed without result: {last_exc}")
    cs_id = str(result.get("cs_id") or _extract_cs_id(long_url) or "")
    payment_link_id = _record_payment_link(
        ctx=ctx,
        account_id=int(account_id or 0),
        status=PAYMENT_LINK_STATUS_CREATED,
        checkout_url=long_url,
        checkout_session_id=cs_id,
        payload={**result, "country": country, "currency": currency, "provider": "paypal", "max_retries": max_retries},
        error="",
    )
    ctx.attach_payment_link(payment_link_id)
    output = {
        "account_id": account_id,
        "payment_link_id": payment_link_id,
        "checkout_url": long_url,
        "long_url": long_url,
        "checkout_session_id": cs_id,
        "cs_id": cs_id,
        "country": country,
        "currency": currency,
        "payment_method_type": str(result.get("payment_method_type") or "paypal"),
        "provider_redirect_url": str(result.get("provider_redirect_url") or ""),
        "max_retries": max_retries,
        "retry_backoff_ms": retry_backoff_ms,
    }
    ctx.update_result(output)
    ctx.log("pp_long_link generated", payload={"payment_link_id": payment_link_id, "cs_id": cs_id, "url_prefix": long_url[:80]})


def _record_payment_link(*, ctx: JobContext, account_id: int, status: str, checkout_url: str, checkout_session_id: str, payload: dict[str, Any], error: str) -> int:
    with session_scope() as s:
        row = PaymentLink(
            account_id=account_id,
            pipeline_id=ctx.pipeline_id,
            job_id=ctx.job_id,
            plan="pp_long_link",
            promo_code="plus-1-month-free",
            checkout_url=checkout_url,
            checkout_session_id=checkout_session_id,
            payload_json=json_dumps(payload),
            status=status,
            error=error,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        link_id = int(row.id or 0)
        if account_id:
            account = s.get(ChatGPTAccount, account_id)
            if account is not None:
                account.last_payment_link_id = link_id
                account.updated_at = utcnow()
                s.add(account)
        return link_id


def _extract_cs_id(url: str) -> str:
    match = re.search(r"(cs_(?:live|test)_[A-Za-z0-9]+)", str(url or ""))
    return match.group(1) if match else ""


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    if n < minimum:
        return minimum
    if n > maximum:
        return maximum
    return n
