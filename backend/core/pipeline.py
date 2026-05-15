"""Pipeline orchestrator.

Watches job terminal events.  When a job that belongs to a pipeline finishes,
we either advance to the next step or close the pipeline out.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from backend.core.constants import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_TYPE_CHATGPT_PAYMENT_LINK,
    JOB_TYPE_CHATGPT_REGISTER,
    JOB_TYPE_PAYMENT_EMPTY,
    PIPELINE_STEP_DONE,
    PIPELINE_STEP_PAYMENT_EMPTY,
    PIPELINE_STEP_PAYMENT_LINK,
    PIPELINE_STEP_REGISTER,
    PIPELINE_STEP_TO_JOB_TYPE,
    PIPELINE_STEPS_ORDERED,
    PIPELINE_STEPS_REGISTER_ONLY,
    PIPELINE_TYPE_CHATGPT_ACCOUNT,
    PIPELINE_TYPE_CHATGPT_REGISTER_ONLY,
)
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline

logger = logging.getLogger(__name__)


def create_chatgpt_account_pipeline(
    *,
    input: dict[str, Any] | None = None,
    proxy_id: int | None = None,
    proxy_url: str = "",
) -> int:
    """Full pipeline: register → payment link → empty placeholder."""
    return _create_pipeline(
        pipeline_type=PIPELINE_TYPE_CHATGPT_ACCOUNT,
        steps=PIPELINE_STEPS_ORDERED,
        input=input,
        proxy_id=proxy_id,
        proxy_url=proxy_url,
    )


def create_chatgpt_register_only_pipeline(
    *,
    input: dict[str, Any] | None = None,
    proxy_id: int | None = None,
    proxy_url: str = "",
) -> int:
    """Register-only pipeline: stash access_token, no payment link."""
    return _create_pipeline(
        pipeline_type=PIPELINE_TYPE_CHATGPT_REGISTER_ONLY,
        steps=PIPELINE_STEPS_REGISTER_ONLY,
        input=input,
        proxy_id=proxy_id,
        proxy_url=proxy_url,
    )


def _create_pipeline(
    *,
    pipeline_type: str,
    steps: tuple[str, ...],
    input: dict[str, Any] | None,
    proxy_id: int | None,
    proxy_url: str,
) -> int:
    payload = dict(input or {})

    # Auto-acquire a proxy from the pool when the caller didn't pin one.
    effective_proxy_url = (proxy_url or "").strip()
    if not effective_proxy_url:
        try:
            from backend.core.proxy_pool import proxy_pool

            picked = proxy_pool.get_next()
            if picked:
                effective_proxy_url = str(picked).strip()
        except Exception:
            effective_proxy_url = ""

    first_step = steps[0]
    with session_scope() as s:
        pipeline = Pipeline(
            type=pipeline_type,
            status=JOB_STATUS_QUEUED,
            current_step=first_step,
            total_steps=len(steps),
            completed_steps=0,
            proxy_id=proxy_id,
            proxy_url=effective_proxy_url,
            input_json=json_dumps(payload),
        )
        s.add(pipeline)
        s.commit()
        s.refresh(pipeline)
        pipeline_id = int(pipeline.id or 0)

    enqueue_job(
        type=PIPELINE_STEP_TO_JOB_TYPE[first_step],
        input=payload,
        pipeline_id=pipeline_id,
        proxy_id=proxy_id,
        proxy_url=effective_proxy_url,
    )
    return pipeline_id


def cancel_pipeline(pipeline_id: int) -> bool:
    with session_scope() as s:
        pipeline = s.get(Pipeline, pipeline_id)
        if pipeline is None:
            return False
        if pipeline.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
            return False
        pipeline.cancel_requested = True
        pipeline.updated_at = utcnow()
        s.add(pipeline)
        # Also cancel any queued/running children so they exit fast.
        children = s.exec(
            __import__("sqlalchemy", fromlist=["select"]).select(Job).where(Job.pipeline_id == pipeline_id)
        ).scalars()
        for job in children:
            if job.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
                job.cancel_requested = True
                s.add(job)
    return True


def on_job_finished(job_id: int) -> None:
    """Called from the worker after every terminal job state."""
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None or job.pipeline_id is None:
            return
        pipeline = s.get(Pipeline, job.pipeline_id)
        if pipeline is None:
            return
        snapshot = {
            "job_status": job.status,
            "job_type": job.type,
            "job_error": job.error,
            "job_result": json_loads(job.result_json, fallback={}),
            "job_account_id": job.account_id,
            "job_payment_link_id": job.payment_link_id,
            "pipeline_type": pipeline.type,
            "pipeline_status": pipeline.status,
            "pipeline_step": pipeline.current_step,
            "pipeline_id": pipeline.id,
        }

    _advance_pipeline(snapshot)


def _advance_pipeline(snapshot: dict[str, Any]) -> None:
    pipeline_id = int(snapshot["pipeline_id"])
    pipeline_type = str(snapshot.get("pipeline_type") or "")
    job_status = str(snapshot["job_status"])
    job_type = str(snapshot["job_type"])
    job_error = str(snapshot["job_error"] or "")
    job_account_id = snapshot.get("job_account_id")
    job_payment_link_id = snapshot.get("job_payment_link_id")
    job_result = snapshot.get("job_result") or {}

    if job_status == JOB_STATUS_SUCCEEDED:
        next_step = _next_step_after(pipeline_type, job_type)
        with session_scope() as s:
            pipeline = s.get(Pipeline, pipeline_id)
            if pipeline is None:
                return
            if pipeline.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
                return
            pipeline.completed_steps = pipeline.completed_steps + 1
            pipeline.updated_at = utcnow()
            if job_account_id and not pipeline.account_id:
                pipeline.account_id = int(job_account_id)
            if job_payment_link_id and not pipeline.payment_link_id:
                pipeline.payment_link_id = int(job_payment_link_id)
            if next_step is None:
                pipeline.status = JOB_STATUS_SUCCEEDED
                pipeline.current_step = PIPELINE_STEP_DONE
                pipeline.finished_at = utcnow()
                pipeline.result_json = json_dumps(
                    {**(json_loads(pipeline.result_json, fallback={}) or {}), "last_job_result": job_result}
                )
                s.add(pipeline)
                s.add(JobEvent(
                    job_id=0,
                    pipeline_id=pipeline_id,
                    level="info",
                    event_type="pipeline_status",
                    message="pipeline succeeded",
                ))
                return
            pipeline.current_step = next_step
            pipeline.status = JOB_STATUS_RUNNING
            if pipeline.started_at is None:
                pipeline.started_at = utcnow()
            s.add(pipeline)
            s.add(JobEvent(
                job_id=0,
                pipeline_id=pipeline_id,
                level="info",
                event_type="pipeline_step",
                message=f"pipeline advanced to step={next_step}",
            ))
            input_payload = _build_step_input(pipeline, next_step, job_result)
            proxy_id = pipeline.proxy_id
            proxy_url = pipeline.proxy_url
            account_id = pipeline.account_id
            payment_link_id = pipeline.payment_link_id

        next_job_type = PIPELINE_STEP_TO_JOB_TYPE[next_step]
        enqueue_job(
            type=next_job_type,
            input=input_payload,
            pipeline_id=pipeline_id,
            account_id=account_id,
            payment_link_id=payment_link_id,
            proxy_id=proxy_id,
            proxy_url=proxy_url,
        )
        return

    if job_status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED, JOB_STATUS_INTERRUPTED}:
        target_status = {
            JOB_STATUS_FAILED: JOB_STATUS_FAILED,
            JOB_STATUS_CANCELLED: JOB_STATUS_CANCELLED,
            JOB_STATUS_INTERRUPTED: JOB_STATUS_INTERRUPTED,
        }[job_status]
        with session_scope() as s:
            pipeline = s.get(Pipeline, pipeline_id)
            if pipeline is None:
                return
            if pipeline.status in {JOB_STATUS_SUCCEEDED, JOB_STATUS_CANCELLED, JOB_STATUS_FAILED}:
                return
            pipeline.status = target_status
            pipeline.error = job_error
            pipeline.finished_at = utcnow()
            pipeline.updated_at = utcnow()
            s.add(pipeline)
            s.add(JobEvent(
                job_id=0,
                pipeline_id=pipeline_id,
                level="error" if target_status == JOB_STATUS_FAILED else "warning",
                event_type="pipeline_status",
                message=f"pipeline {target_status}: {job_error}",
            ))


def _next_step_after(pipeline_type: str, job_type: str) -> str | None:
    # register-only pipelines stop right after `chatgpt_register` succeeds.
    if pipeline_type == PIPELINE_TYPE_CHATGPT_REGISTER_ONLY:
        return None
    if job_type == JOB_TYPE_CHATGPT_REGISTER:
        return PIPELINE_STEP_PAYMENT_LINK
    if job_type == JOB_TYPE_CHATGPT_PAYMENT_LINK:
        return PIPELINE_STEP_PAYMENT_EMPTY
    if job_type == JOB_TYPE_PAYMENT_EMPTY:
        return None
    return None


def _build_step_input(pipeline: Pipeline, next_step: str, prior_result: dict[str, Any]) -> dict[str, Any]:
    base = json_loads(pipeline.input_json, fallback={}) or {}
    if next_step == PIPELINE_STEP_PAYMENT_LINK:
        opts = dict(base.get("payment_link_options") or {})
        opts["account_id"] = pipeline.account_id or prior_result.get("account_id")
        # Default plan / country selection mirrors `payment.py` defaults.
        plan = str(opts.get("plan") or "team").lower()
        opts["plan"] = plan
        if not opts.get("country"):
            opts["country"] = "ID" if plan == "plus" else "US"
        return opts
    if next_step == PIPELINE_STEP_PAYMENT_EMPTY:
        return {
            "payment_link_id": pipeline.payment_link_id or prior_result.get("payment_link_id"),
        }
    return {}
