"""payment_empty flow.

Pipeline placeholder for the actual paid-payment step.  Logs that the empty
phase has run and marks the job succeeded so the pipeline closes out.
"""
from __future__ import annotations

from sqlmodel import Session

from backend.core.constants import (
    JOB_TYPE_PAYMENT_EMPTY,
    PAYMENT_LINK_STATUS_EMPTY_PAYMENT_PENDING,
)
from backend.core.db import engine, session_scope
from backend.core.flow_registry import register_flow
from backend.core.job_context import JobContext
from backend.core.time_utils import utcnow
from backend.models.payment import PaymentLink


def run(ctx: JobContext) -> None:
    payment_link_id = ctx.payment_link_id or int(ctx.input.get("payment_link_id") or 0) or None
    if payment_link_id:
        ctx.attach_payment_link(payment_link_id)
        with session_scope() as s:
            row = s.get(PaymentLink, payment_link_id)
            if row is not None:
                row.status = PAYMENT_LINK_STATUS_EMPTY_PAYMENT_PENDING
                row.updated_at = utcnow()
                s.add(row)

    ctx.log("支付链路尚未实现，仅占位记录长链已生成", payload={"payment_link_id": payment_link_id})
    ctx.update_result({
        "state": "not_implemented",
        "payment_link_id": payment_link_id,
    })


register_flow(JOB_TYPE_PAYMENT_EMPTY, run)
