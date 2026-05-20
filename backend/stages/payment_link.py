"""payment_link stage.

Produces a Team hosted long-link for the linked ChatGPT account and writes a
row into `payment_links`.
"""
from __future__ import annotations

from sqlmodel import Session

from backend.core.settings import settings

from backend.core.constants import (
    ACCOUNT_STATUS_PAYMENT_LINK_READY,
    PAYMENT_LINK_STATUS_CREATED,
    PAYMENT_LINK_STATUS_FAILED,
    TEAM_PROMO_CODE,
)
from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps, json_loads
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.payment import PaymentLink
from backend.schemas.stage_io import PaymentLinkInput, PaymentLinkOutput


@stage(
    name="payment_link",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=PaymentLinkInput,
    output_schema=PaymentLinkOutput,
    description="Generate ChatGPT Team/Plus hosted payment long-link.",
)
def run(ctx: JobContext) -> None:
    account_id = ctx.account_id or int(ctx.input.get("account_id") or 0) or None
    if not account_id:
        raise RuntimeError("payment_link stage requires account_id")
    ctx.attach_account(account_id)

    plan = str(ctx.input.get("plan") or settings.get("workpool.payment_link.plan", "plus") or "plus").strip().lower()
    if plan not in {"team", "plus"}:
        raise RuntimeError(f"unsupported plan: {plan}")

    workspace_name = str(ctx.input.get("workspace_name") or settings.get("workpool.payment_link.workspace_name", "MyWorkspace") or "MyWorkspace")
    price_interval = str(ctx.input.get("price_interval") or settings.get("workpool.payment_link.price_interval", "month") or "month")
    seat_quantity = int(ctx.input.get("seat_quantity") or settings.get_int("workpool.payment_link.seat_quantity", 2) or 2)
    country = str(ctx.input.get("country") or settings.get("workpool.payment_link.country", "") or ("ID" if plan == "plus" else "US"))
    currency_override = ctx.input.get("currency") or settings.get("workpool.payment_link.currency", "") or None

    ctx.log(
        f"starting payment-link stage ({plan})",
        payload={
            "plan": plan,
            "account_id": account_id,
            "workspace_name": workspace_name,
            "price_interval": price_interval,
            "seat_quantity": seat_quantity,
            "country": country,
            "currency": currency_override,
        },
    )

    with Session(engine) as s:
        account_row = s.get(ChatGPTAccount, account_id)
        if account_row is None:
            raise RuntimeError(f"account {account_id} not found")

    adapter = _AccountAdapter(account_row)

    # Lazy import keeps API/queue imports fast.
    from backend.integrations.chatgpt.payment import (
        generate_plus_link,
        generate_team_link,
    )

    payload_for_log = {
        "plan": plan,
        "workspace_name": workspace_name,
        "price_interval": price_interval,
        "seat_quantity": seat_quantity,
        "country": country,
        "currency": currency_override,
    }

    try:
        if plan == "plus":
            url = generate_plus_link(
                adapter,
                proxy=ctx.effective_proxy_url() or None,
                country=country,
                currency=str(currency_override) if currency_override else None,
            )
            payload_for_log = {
                "plan": "plus",
                "country": country,
                "currency": currency_override,
            }
        else:
            url = generate_team_link(
                adapter,
                workspace_name=workspace_name,
                price_interval=price_interval,
                seat_quantity=seat_quantity,
                proxy=ctx.effective_proxy_url() or None,
                country=country,
            )
            payload_for_log = {
                "plan": "team",
                "workspace_name": workspace_name,
                "price_interval": price_interval,
                "seat_quantity": seat_quantity,
                "country": country,
            }
    except Exception as exc:
        with session_scope() as s:
            row = PaymentLink(
                account_id=account_id,
                pipeline_id=ctx.pipeline_id,
                job_id=ctx.job_id,
                plan=plan,
                promo_code=TEAM_PROMO_CODE if plan == "team" else "",
                checkout_url="",
                checkout_session_id="",
                payload_json=json_dumps(payload_for_log),
                status=PAYMENT_LINK_STATUS_FAILED,
                error=str(exc),
            )
            s.add(row)
        ctx.log(f"payment_link generate failed: {exc}", level="error")
        raise

    cs_id = _extract_cs_id(url)

    with session_scope() as s:
        row = PaymentLink(
            account_id=account_id,
            pipeline_id=ctx.pipeline_id,
            job_id=ctx.job_id,
            plan=plan,
            promo_code=TEAM_PROMO_CODE if plan == "team" else "",
            checkout_url=url,
            checkout_session_id=cs_id,
            payload_json=json_dumps(payload_for_log),
            status=PAYMENT_LINK_STATUS_CREATED,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        payment_link_id = int(row.id or 0)

        account_row = s.get(ChatGPTAccount, account_id)
        if account_row is not None:
            account_row.last_payment_link_id = payment_link_id
            account_row.status = ACCOUNT_STATUS_PAYMENT_LINK_READY
            account_row.updated_at = utcnow()
            s.add(account_row)

    ctx.attach_payment_link(payment_link_id)
    ctx.update_result({"payment_link_id": payment_link_id, "checkout_url": url, "cs_id": cs_id, "plan": plan})
    ctx.log(f"{plan} payment_link generated cs_id={cs_id}")


def _extract_cs_id(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    last = text.rstrip("/").split("/")[-1]
    return last if last.startswith("cs_") else ""


class _AccountAdapter:
    """Shape a `ChatGPTAccount` like the duck-type `payment.py` consumes."""

    def __init__(self, account: ChatGPTAccount) -> None:
        self.account = account
        cookies = json_loads(account.cookies_json, fallback=[]) or []
        # legacy `payment.py` reads a "name=value; ..." cookie string.
        self.cookies = "; ".join(
            f"{c.get('name')}={c.get('value')}"
            for c in cookies
            if isinstance(c, dict) and c.get("name") and c.get("value")
        )
        meta = json_loads(account.metadata_json, fallback={}) or {}
        self.access_token = account.access_token
        fingerprint = json_loads(account.browser_fingerprint_json, fallback={}) or {}
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
