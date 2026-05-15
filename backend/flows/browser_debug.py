"""browser_debug flow — opens a Camoufox/Chromium debug session via the queue.

Most callers will hit `BrowserDebugService.open_debug_session` directly from
the API.  This flow exists for batch debug runs and to keep a job-log audit
trail.
"""
from __future__ import annotations

from backend.core.browser_debug import open_debug_session
from backend.core.constants import JOB_TYPE_BROWSER_DEBUG
from backend.core.flow_registry import register_flow
from backend.core.job_context import JobContext


def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    target_url = str(payload.get("target_url") or "")
    if not target_url and ctx.payment_link_id is None:
        raise RuntimeError("browser_debug flow requires target_url or payment_link_id")

    info = open_debug_session(
        target_url=target_url,
        account_id=ctx.account_id or payload.get("account_id"),
        payment_link_id=ctx.payment_link_id or payload.get("payment_link_id"),
        pipeline_id=ctx.pipeline_id,
        job_id=ctx.job_id,
        proxy_url=ctx.proxy_url or payload.get("proxy_url"),
        browser_type=str(payload.get("browser_type") or "camoufox"),
        inject_cookies=bool(payload.get("inject_cookies", True)),
        inject_local_storage=bool(payload.get("inject_local_storage", True)),
        inject_fingerprint=bool(payload.get("inject_fingerprint", True)),
        record_har=bool(payload.get("record_har", True)),
        omit_har_content=bool(payload.get("omit_har_content", False)),
        har_dir=payload.get("har_dir"),
        log=ctx.log,
    )
    ctx.update_result(info)


register_flow(JOB_TYPE_BROWSER_DEBUG, run)
