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
  account_id, payment_link_id, email_address, proxy_id, proxy_url, session/sub2api/codex token fields
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from sqlalchemy import select as sa_select
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


class PipelineRetryError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---- presets ---------------------------------------------------------------

FULL_CHAIN_STAGES: tuple[str, ...] = ("register", "payment_link", "payment", "chatgpt_session", "sub2api_sync")
DEFAULT_PRESET = "full_chain"

PRESETS: dict[str, tuple[str, ...]] = {
    DEFAULT_PRESET:                         FULL_CHAIN_STAGES,
    "register_only":                       ("register",),
    "register_with_sub2api":               ("register", "chatgpt_session", "sub2api_sync"),
    "account_paid":                        ("register", "payment_link", "payment"),
    "account_paid_with_sub2api":           ("register", "payment_link", "payment", "chatgpt_session", "sub2api_sync"),
    "link_only":                           ("register", "payment_link"),
    "sub2api_only":                        ("sub2api_sync",),
    "register_with_refresh_token":         ("register", "chatgpt_session", "openai_oauth", "sub2api_sync"),
    "account_paid_with_refresh_token":     ("register", "payment_link", "payment", "chatgpt_session", "openai_oauth", "sub2api_sync"),
    "refresh_token_only":                  ("chatgpt_session", "openai_oauth", "sub2api_sync"),
}


CARRY_OVER_KEYS: tuple[str, ...] = (
    "account_id",
    "payment_link_id",
    "email_address",
    "proxy_id",
    "proxy_url",
    "refresh_token_id",
    "has_refresh_token",
    "id_token",
    "session_token",
    "session_expires_at",
    "session_refresh_status",
    "chatgpt_account_id",
    "chatgpt_user_id",
    "plan_type",
    "sub2api_account_id",
    "sub2api_status",
)


# ---- creation --------------------------------------------------------------


def resolve_stages(*, preset: str | None = None, stages: Iterable[str] | None = None) -> list[str]:
    """Resolve `(preset, stages)` request inputs into a concrete stage list."""
    if stages is not None:
        result = [str(s).strip() for s in stages if str(s).strip()]
    else:
        preset_name = preset or DEFAULT_PRESET
        if preset_name not in PRESETS:
            raise ValueError(f"unknown preset {preset_name!r}")
        result = list(PRESETS[preset_name])

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


# ---- manual retry ----------------------------------------------------------


def retry_failed_pipeline_job(job_id: int) -> dict[str, Any]:
    """Manually retry a failed stage job within a failed pipeline.

    Only valid for stages strictly after `register`. Creates a fresh job and
    resets the pipeline so the existing `_advance_pipeline(...)` flow can
    continue once the retry succeeds.
    """
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise PipelineRetryError(404, f"job {job_id} not found")
        if job.pipeline_id is None:
            raise PipelineRetryError(409, "standalone jobs cannot be retried")
        if job.status not in {JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED}:
            raise PipelineRetryError(409, f"job is not retryable (status={job.status})")

        pipeline = s.get(Pipeline, job.pipeline_id)
        if pipeline is None:
            raise PipelineRetryError(404, f"pipeline {job.pipeline_id} not found")
        if pipeline.status not in {JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED}:
            raise PipelineRetryError(409, f"pipeline is not retryable (status={pipeline.status})")

        stage = str(job.type or "")
        if stage == "register":
            raise PipelineRetryError(400, "register stage cannot be retried manually")

        stages = json_loads(pipeline.stages_json, fallback=[]) or []
        if "register" not in stages:
            raise PipelineRetryError(409, "pipeline does not contain a register stage")
        if stage not in stages:
            raise PipelineRetryError(409, f"stage {stage!r} is not in pipeline.stages")

        register_idx = stages.index("register")
        stage_idx = stages.index(stage)
        if stage_idx <= register_idx:
            raise PipelineRetryError(400, f"stage {stage!r} must come after register")
        if pipeline.current_stage != stage:
            raise PipelineRetryError(
                409,
                f"pipeline current_stage={pipeline.current_stage!r}; cannot retry {stage!r}",
            )

        latest_for_stage = s.exec(
            sa_select(Job)
            .where(Job.pipeline_id == pipeline.id)
            .where(Job.type == stage)
            .order_by(Job.id.desc())
            .limit(1)
        ).scalars().first()
        if latest_for_stage is None or int(latest_for_stage.id or 0) != job_id:
            raise PipelineRetryError(409, "a newer job exists for this stage; refresh and retry the latest")

        active_for_stage = list(
            s.exec(
                sa_select(Job)
                .where(Job.pipeline_id == pipeline.id)
                .where(Job.type == stage)
                .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
            ).scalars()
        )
        if active_for_stage:
            raise PipelineRetryError(409, "an active job already exists for this stage")

        prev_stage = stages[stage_idx - 1]
        prev_succeeded = s.exec(
            sa_select(Job)
            .where(Job.pipeline_id == pipeline.id)
            .where(Job.type == prev_stage)
            .where(Job.status == JOB_STATUS_SUCCEEDED)
            .order_by(Job.id.desc())
            .limit(1)
        ).scalars().first()
        if prev_succeeded is None:
            raise PipelineRetryError(
                409,
                f"cannot retry: previous stage {prev_stage!r} has no succeeded job to resume from",
            )

        prior_result = json_loads(prev_succeeded.result_json, fallback={}) or {}
        stage_inputs = json_loads(pipeline.stage_inputs_json, fallback={}) or {}
        request_payload = json_loads(pipeline.input_json, fallback={}) or {}
        input_payload = _compose_stage_input(
            stage_inputs, stage,
            prior_result=prior_result,
            request_payload=request_payload,
        )

        account_id = pipeline.account_id
        payment_link_id = pipeline.payment_link_id
        proxy_id = pipeline.proxy_id
        proxy_url = pipeline.proxy_url or ""
        if isinstance(input_payload, dict):
            if input_payload.get("account_id") not in (None, ""):
                try:
                    account_id = int(input_payload.get("account_id") or 0) or account_id
                except Exception:
                    pass
            if input_payload.get("payment_link_id") not in (None, ""):
                try:
                    payment_link_id = int(input_payload.get("payment_link_id") or 0) or payment_link_id
                except Exception:
                    pass
            if input_payload.get("proxy_id") not in (None, ""):
                try:
                    proxy_id = int(input_payload.get("proxy_id") or 0) or proxy_id
                except Exception:
                    pass
            if input_payload.get("proxy_url") not in (None, ""):
                proxy_url = str(input_payload.get("proxy_url") or proxy_url or "")

        pipeline_id = int(pipeline.id or 0)

    with session_scope() as s:
        pipeline_row = s.get(Pipeline, pipeline_id)
        if pipeline_row is None:
            raise PipelineRetryError(404, f"pipeline {pipeline_id} disappeared")
        if pipeline_row.status not in {JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED}:
            raise PipelineRetryError(409, f"pipeline status changed to {pipeline_row.status}")
        pipeline_row.status = JOB_STATUS_QUEUED
        pipeline_row.current_stage = stage
        pipeline_row.completed_steps = min(int(pipeline_row.completed_steps or 0), stage_idx)
        pipeline_row.error = ""
        pipeline_row.finished_at = None
        pipeline_row.cancel_requested = False
        pipeline_row.updated_at = utcnow()
        s.add(pipeline_row)
        s.add(JobEvent(
            job_id=0,
            pipeline_id=pipeline_id,
            level="info",
            event_type="pipeline_retry",
            message=f"manual retry stage={stage} retried_job_id={job_id}",
        ))

    new_job_id = enqueue_job(
        type=stage,
        input=input_payload,
        pipeline_id=pipeline_id,
        account_id=account_id,
        payment_link_id=payment_link_id,
        proxy_id=proxy_id,
        proxy_url=proxy_url,
    )
    return {
        "job_id": new_job_id,
        "retried_job_id": job_id,
        "pipeline_id": pipeline_id,
        "stage": stage,
    }


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
