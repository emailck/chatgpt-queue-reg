"""Pipeline orchestrator (declarative).

A `Pipeline` carries:

  * `stages_json`            — ordered list of stage names to walk through
  * `stop_after`             — optional stage name; once it succeeds the
                               pipeline closes out without enqueuing the rest
  * `stage_inputs_json`      — per-stage private input dict
  * `resource_bindings_json` — overrides for sms project routing etc.

There is exactly one entry point (`create_pipeline(...)`); presets are sugar
on top. Advancing is a pure function of the persisted lists, so the queue
can support arbitrary stage chains and arbitrary cut-off points without
adding new pipeline types.

Carry-over fields between stages (whitelist; see ARCHITECTURE.md §3.2):
  account_id, payment_link_id, email_address, proxy_id, proxy_url, codex_rt, codex_at
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from sqlmodel import Session

from backend.core.constants import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
)
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.queue import enqueue_job
from backend.core.stages import STAGE_REGISTRY
from backend.core.time_utils import utcnow
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline

logger = logging.getLogger(__name__)


# ---- presets ---------------------------------------------------------------

PRESETS: dict[str, tuple[str, ...]] = {
    "register_only":             ("register",),
    "register_with_codex_rt":    ("register", "oauth_codex"),
    "account_paid":              ("register", "payment_link", "payment"),
    "account_paid_with_codex_rt": ("register", "payment_link", "payment", "oauth_codex"),
    "link_only":                 ("register", "payment_link"),
    "codex_rt_only":             ("oauth_codex",),
}


CARRY_OVER_KEYS: tuple[str, ...] = (
    "account_id",
    "payment_link_id",
    "email_address",
    "proxy_id",
    "proxy_url",
    "codex_rt",
    "codex_at",
)


# ---- creation --------------------------------------------------------------


def resolve_stages(*, preset: str | None, stages: Iterable[str] | None) -> list[str]:
    """Resolve `(preset, stages)` request inputs into a concrete stage list."""
    if stages is not None:
        result = [str(s).strip() for s in stages if str(s).strip()]
    elif preset:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}")
        result = list(PRESETS[preset])
    else:
        raise ValueError("either preset or stages must be provided")

    if not result:
        raise ValueError("stage list is empty")
    unknown = [s for s in result if s not in STAGE_REGISTRY]
    if unknown:
        raise ValueError(f"unknown stage(s): {unknown}")
    return result


def create_pipeline(
    *,
    stages: list[str],
    preset: str = "",
    stop_after: str = "",
    stage_inputs: dict[str, dict[str, Any]] | None = None,
    resource_bindings: dict[str, dict[str, Any]] | None = None,
    proxy_id: int | None = None,
    proxy_url: str = "",
    request_payload: dict[str, Any] | None = None,
) -> int:
    """Create a pipeline row and enqueue its first stage's job."""
    if not stages:
        raise ValueError("stages cannot be empty")
    if stop_after and stop_after not in stages:
        raise ValueError(f"stop_after {stop_after!r} not in stages list")

    payload = dict(request_payload or {})
    stage_inputs = dict(stage_inputs or {})
    resource_bindings = dict(resource_bindings or {})
    first_stage = stages[0]

    effective_proxy_url = (proxy_url or "").strip()

    with session_scope() as s:
        pipeline = Pipeline(
            preset=preset or "",
            status=JOB_STATUS_QUEUED,
            stages_json=json_dumps(stages),
            stop_after=stop_after or "",
            stage_inputs_json=json_dumps(stage_inputs),
            resource_bindings_json=json_dumps(resource_bindings),
            current_stage=first_stage,
            total_steps=len(stages),
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
        type=first_stage,
        input=_compose_stage_input(stage_inputs, first_stage, prior_result={}, request_payload=payload),
        pipeline_id=pipeline_id,
        proxy_id=proxy_id,
        proxy_url=effective_proxy_url,
    )
    return pipeline_id


# ---- cancellation ----------------------------------------------------------


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
        children = s.exec(
            __import__("sqlalchemy", fromlist=["select"]).select(Job).where(Job.pipeline_id == pipeline_id)
        ).scalars()
        for job in children:
            if job.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
                job.cancel_requested = True
                s.add(job)
    return True


# ---- progression -----------------------------------------------------------


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
            "pipeline_status": pipeline.status,
            "pipeline_current_stage": pipeline.current_stage,
            "pipeline_id": pipeline.id,
        }

    _advance_pipeline(snapshot)


def _advance_pipeline(snap: dict[str, Any]) -> None:
    pipeline_id = int(snap["pipeline_id"])
    job_status = str(snap["job_status"])
    job_type = str(snap["job_type"])
    job_error = str(snap["job_error"] or "")
    job_account_id = snap.get("job_account_id")
    job_payment_link_id = snap.get("job_payment_link_id")
    job_result = snap.get("job_result") or {}

    if job_status == JOB_STATUS_SUCCEEDED:
        with session_scope() as s:
            pipeline = s.get(Pipeline, pipeline_id)
            if pipeline is None:
                return
            if pipeline.status in {JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}:
                return
            stages = json_loads(pipeline.stages_json, fallback=[]) or []
            stop_after = str(pipeline.stop_after or "")
            stage_inputs = json_loads(pipeline.stage_inputs_json, fallback={}) or {}
            request_payload = json_loads(pipeline.input_json, fallback={}) or {}

            try:
                idx = stages.index(job_type)
            except ValueError:
                # Job not in this pipeline's stages (shouldn't happen in
                # normal flow). Treat as terminal.
                pipeline.status = JOB_STATUS_SUCCEEDED
                pipeline.finished_at = utcnow()
                pipeline.updated_at = utcnow()
                s.add(pipeline)
                return

            pipeline.completed_steps = max(int(pipeline.completed_steps or 0), idx + 1)
            pipeline.updated_at = utcnow()
            if job_account_id and not pipeline.account_id:
                pipeline.account_id = int(job_account_id)
            if job_payment_link_id and not pipeline.payment_link_id:
                pipeline.payment_link_id = int(job_payment_link_id)

            terminal = (idx == len(stages) - 1) or (stop_after and stages[idx] == stop_after)
            if terminal:
                pipeline.status = JOB_STATUS_SUCCEEDED
                pipeline.current_stage = stages[idx]
                pipeline.finished_at = utcnow()
                pipeline.result_json = json_dumps({
                    **(json_loads(pipeline.result_json, fallback={}) or {}),
                    "last_job_result": job_result,
                })
                s.add(pipeline)
                s.add(JobEvent(
                    job_id=0,
                    pipeline_id=pipeline_id,
                    level="info",
                    event_type="pipeline_status",
                    message="pipeline succeeded",
                ))
                return

            next_stage = stages[idx + 1]
            pipeline.current_stage = next_stage
            pipeline.status = JOB_STATUS_RUNNING
            if pipeline.started_at is None:
                pipeline.started_at = utcnow()
            s.add(pipeline)
            s.add(JobEvent(
                job_id=0,
                pipeline_id=pipeline_id,
                level="info",
                event_type="pipeline_stage",
                message=f"pipeline advanced to stage={next_stage}",
            ))
            input_payload = _compose_stage_input(stage_inputs, next_stage,
                                                 prior_result=job_result,
                                                 request_payload=request_payload)
            proxy_id = pipeline.proxy_id
            proxy_url = pipeline.proxy_url
            if isinstance(input_payload, dict):
                if input_payload.get("proxy_id") not in (None, ""):
                    try:
                        proxy_id = int(input_payload.get("proxy_id") or 0) or proxy_id
                    except Exception:
                        pass
                if input_payload.get("proxy_url") not in (None, ""):
                    proxy_url = str(input_payload.get("proxy_url") or proxy_url or "")
            account_id = pipeline.account_id
            payment_link_id = pipeline.payment_link_id

        # Outside the session: enqueue.
        enqueue_job(
            type=next_stage,
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


def _compose_stage_input(
    stage_inputs: dict[str, dict[str, Any]],
    stage: str,
    *,
    prior_result: dict[str, Any],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the final input dict for `stage`.

    Layering (later wins):
      1. carry-over fields from `prior_result` (whitelist)
      2. fields from the original `request_payload` that match carry-over keys
         (so e.g. a top-level proxy_url survives even before the first stage runs)
      3. explicit `stage_inputs[stage]`
    """
    out: dict[str, Any] = {}
    for k in CARRY_OVER_KEYS:
        if k in request_payload and request_payload.get(k) not in (None, ""):
            out[k] = request_payload[k]
    for k in CARRY_OVER_KEYS:
        if k in prior_result and prior_result.get(k) not in (None, ""):
            out[k] = prior_result[k]
    explicit = dict(stage_inputs.get(stage) or {})
    out.update(explicit)
    return out
