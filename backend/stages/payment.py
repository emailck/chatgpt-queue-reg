"""payment stage (v1 stub).

Browser-automation payment with card + sms is not yet implemented. This stub
keeps the pipeline contract whole: it accepts a `payment_link_id`, logs that
card_pool / sms_pool acquisition would happen, and emits a stub result so the
pipeline can advance.
"""
from __future__ import annotations

from backend.core.job_context import JobContext
from backend.core.pools.base import AcquireOutcome
from backend.core.stages import stage
from backend.schemas.stage_io import PaymentInput, PaymentOutput


@stage(
    name="payment",
    requires_resources=["card_pool", "sms_pool"],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=PaymentInput,
    output_schema=PaymentOutput,
    description="[STUB] Browser-automation payment with card + sms; not yet implemented.",
)
def run(ctx: JobContext) -> None:
    payment_link_id = ctx.payment_link_id or int(ctx.input.get("payment_link_id") or 0) or None
    if not payment_link_id:
        raise RuntimeError("payment stage requires payment_link_id")
    ctx.attach_payment_link(payment_link_id)
    identity = ctx.identity
    payment_region = str(
        ctx.input.get("payment_proxy_region")
        or ctx.input.get("proxy_region")
        or ctx.input.get("region")
        or ""
    ).strip()
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
    )
    ctx.log(
        "payment stage stub: card_pool/sms_pool acquire skipped, payment not implemented",
        level="warning",
        payload={
            "payment_link_id": payment_link_id,
            "account_proxy_id": identity.proxy_id if identity else None,
            "payment_proxy_id": (payment_proxy.payload or {}).get("proxy_id") if payment_proxy else None,
            "payment_proxy_region": payment_region,
        },
    )
    ctx.update_result({
        "state": "stub_not_implemented",
        "payment_link_id": payment_link_id,
        "payment_proxy_id": (payment_proxy.payload or {}).get("proxy_id") if payment_proxy else None,
        "payment_proxy_region": payment_region,
    })
