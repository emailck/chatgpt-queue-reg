"""Per-stage worker pools + helper to enqueue jobs.

Design notes
------------

* One `ThreadPoolExecutor` per stage. Each pool has its own concurrency,
  configured via `settings["worker_concurrency.<stage>"]` (falls back to the
  stage's `default_concurrency`, then to `DEFAULT_WORKER_CONCURRENCY`).
* The dispatcher loop polls `jobs` for `queued` rows in priority + FIFO order
  and routes each to its stage's pool. A queued job whose stage has no
  registered handler is rejected (`failed`) immediately to avoid stuck rows.
* Cancellation is cooperative: API writes `cancel_requested=True`, stages call
  `ctx.check_cancelled()`, the worker also pre-checks before starting.
* On boot, `recover_orphan_jobs()` flips lingering `running` rows to
  `interrupted` and updates their pipelines.
* Stages run synchronously from the worker thread (playwright/curl_cffi are
  blocking IO so threading is enough).

API summary
-----------

  - `enqueue_job(type=..., input=..., ...)` — `type` MUST be a registered stage
  - `get_pool().set_concurrency(stage_name, n)` — resize a single stage pool
  - `get_pool().concurrency_map()` — current per-stage concurrency
"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.constants import (
    DEFAULT_WORKER_CONCURRENCY,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
)
from backend.core.db import engine, session_scope
from backend.core.errors import JobCancelled
from backend.core.job_context import JobContext, _publish, mark_job_running
from backend.core.json_utils import json_dumps, json_loads
from backend.core.settings import settings
from backend.core.stages import STAGE_REGISTRY, get_stage
from backend.core.time_utils import utcnow
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline

logger = logging.getLogger(__name__)


# ---- enqueue -----------------------------------------------------------------

def enqueue_job(
    *,
    type: str,
    input: dict[str, Any] | None = None,
    pipeline_id: int | None = None,
    account_id: int | None = None,
    payment_link_id: int | None = None,
    proxy_id: int | None = None,
    proxy_url: str = "",
    email_address: str = "",
    priority: int = 0,
    max_attempts: int = 1,
) -> int:
    """Enqueue a job. `type` must be a registered stage name."""
    stage_name = str(type or "").strip()
    if not stage_name:
        raise ValueError("enqueue_job requires a stage `type`")
    if stage_name not in STAGE_REGISTRY:
        raise ValueError(f"unknown stage {stage_name!r}")

    with session_scope() as s:
        job = Job(
            type=stage_name,
            status=JOB_STATUS_QUEUED,
            priority=priority,
            max_attempts=max_attempts,
            input_json=json_dumps(input or {}),
            pipeline_id=pipeline_id,
            account_id=account_id,
            payment_link_id=payment_link_id,
            proxy_id=proxy_id,
            proxy_url=proxy_url or "",
            email_address=email_address or "",
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = int(job.id or 0)
    _publish(job_id, {"kind": "status", "status": "queued"})
    _wake_dispatcher()
    return job_id


# ---- recovery ----------------------------------------------------------------

def recover_orphan_jobs() -> int:
    """Flip lingering 'running' jobs/pipelines to 'interrupted' on boot."""
    fixed = 0
    stale_job_ids: list[int] = []
    with session_scope() as s:
        stale_jobs = list(s.exec(sa_select(Job).where(Job.status == JOB_STATUS_RUNNING)).scalars())
        for job in stale_jobs:
            job.status = JOB_STATUS_INTERRUPTED
            job.error = "process restart while running"
            job.updated_at = utcnow()
            job.finished_at = utcnow()
            s.add(job)
            s.add(JobEvent(
                job_id=job.id or 0,
                pipeline_id=job.pipeline_id,
                level="warning",
                event_type="status",
                message="job marked interrupted on boot (process restarted while running)",
            ))
            stale_job_ids.append(int(job.id or 0))
            fixed += 1
        stale_pipelines = list(
            s.exec(sa_select(Pipeline).where(Pipeline.status == JOB_STATUS_RUNNING)).scalars()
        )
        for pipeline in stale_pipelines:
            pipeline.status = JOB_STATUS_INTERRUPTED
            pipeline.error = pipeline.error or "process restart while running"
            pipeline.updated_at = utcnow()
            pipeline.finished_at = utcnow()
            s.add(pipeline)
    _release_orphan_resources(stale_job_ids)
    return fixed


def _release_orphan_resources(job_ids: list[int]) -> None:
    if not job_ids:
        return
    from backend.models.payment_card import CARD_STATUS_AVAILABLE, PaymentCard
    from backend.models.paypal_number import PAYPAL_NUMBER_STATUS_COOLING, PayPalNumber

    now = utcnow()
    with session_scope() as s:
        for row in s.exec(sa_select(PayPalNumber).where(PayPalNumber.bound_job_id.in_(job_ids))).scalars():
            row.status = PAYPAL_NUMBER_STATUS_COOLING
            row.last_used_at = now
            row.bound_job_id = None
            row.updated_at = now
            s.add(row)
        for row in s.exec(sa_select(PaymentCard).where(PaymentCard.bound_job_id.in_(job_ids))).scalars():
            row.status = CARD_STATUS_AVAILABLE
            row.bound_job_id = None
            row.updated_at = now
            s.add(row)


# ---- worker pool -------------------------------------------------------------


def _stage_concurrency(stage_name: str) -> int:
    """Resolve effective concurrency for a stage."""
    meta = get_stage(stage_name)
    fallback = meta.default_concurrency if meta else DEFAULT_WORKER_CONCURRENCY
    return max(1, settings.get_int(f"worker_concurrency.{stage_name}", fallback))


class StagePoolManager:
    """Owns one `ThreadPoolExecutor` per stage."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._lock = threading.Lock()
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._concurrency: dict[str, int] = {}
        self._inflight: dict[str, dict[int, Future]] = {}

    # -- lifecycle --

    def start(self) -> None:
        if self._dispatcher is not None:
            return
        with self._lock:
            for name in STAGE_REGISTRY.keys():
                self._ensure_executor(name)
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="cqr-dispatcher", daemon=True
        )
        self._dispatcher.start()
        logger.info("stage pool manager started: %s", self.concurrency_map())

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=timeout)
            self._dispatcher = None
        with self._lock:
            for ex in list(self._executors.values()):
                ex.shutdown(wait=True, cancel_futures=False)
            self._executors.clear()

    def wake(self) -> None:
        self._wakeup.set()

    # -- public introspection --

    def concurrency_map(self) -> dict[str, int]:
        with self._lock:
            return dict(self._concurrency)

    def inflight_map(self) -> dict[str, int]:
        with self._lock:
            return {
                name: sum(1 for f in inflight.values() if not f.done())
                for name, inflight in self._inflight.items()
            }

    def set_concurrency(self, stage: str, value: int) -> int:
        """Resize a single stage's pool. Returns effective concurrency."""
        if stage not in STAGE_REGISTRY:
            raise ValueError(f"unknown stage {stage!r}")
        new_value = max(1, int(value or 0))
        with self._lock:
            current = self._concurrency.get(stage)
            if current == new_value and stage in self._executors:
                return current
            old_executor = self._executors.get(stage)
            new_executor = ThreadPoolExecutor(
                max_workers=new_value, thread_name_prefix=f"cqr-{stage}"
            )
            self._executors[stage] = new_executor
            self._concurrency[stage] = new_value
            self._inflight.setdefault(stage, {})
        try:
            settings.set(f"worker_concurrency.{stage}", str(new_value))
        except Exception:
            pass
        if old_executor is not None:
            threading.Thread(
                target=lambda: old_executor.shutdown(wait=True, cancel_futures=False),
                name=f"cqr-{stage}-resize",
                daemon=True,
            ).start()
        logger.info("stage %s pool resized to concurrency=%s", stage, new_value)
        self._wakeup.set()
        return new_value

    # -- internals --

    def _ensure_executor(self, stage: str) -> ThreadPoolExecutor:
        if stage in self._executors:
            return self._executors[stage]
        n = _stage_concurrency(stage)
        ex = ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"cqr-{stage}")
        self._executors[stage] = ex
        self._concurrency[stage] = n
        self._inflight.setdefault(stage, {})
        return ex

    def _has_capacity(self, stage: str) -> bool:
        with self._lock:
            cap = self._concurrency.get(stage, 0)
            inflight = self._inflight.get(stage, {})
            active = sum(1 for f in inflight.values() if not f.done())
        return active < cap

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            ran_any = False
            while not self._stop.is_set():
                claimed = self._claim_one_for_any_capacity()
                if claimed is None:
                    break
                ran_any = True
            if not ran_any:
                self._wakeup.wait(timeout=1.0)
                self._wakeup.clear()

    def _claim_one_for_any_capacity(self) -> int | None:
        """Find a queued job whose stage still has capacity, claim & submit it."""
        # Snapshot stages with free slots.
        free_stages: list[str] = []
        with self._lock:
            for name in self._concurrency.keys():
                cap = self._concurrency.get(name, 0)
                inflight = self._inflight.get(name, {})
                active = sum(1 for f in inflight.values() if not f.done())
                if active < cap:
                    free_stages.append(name)
        if not free_stages:
            return None

        # Find earliest queued job among those stages.
        with Session(engine) as s:
            stmt = (
                sa_select(Job)
                .where(Job.status == JOB_STATUS_QUEUED)
                .where(Job.type.in_(free_stages))
                .order_by(Job.priority.desc(), Job.id.asc())
                .limit(1)
            )
            row = s.exec(stmt).scalars().first()
            if row is None:
                return None
            candidate_id = int(row.id or 0)
            stage_name = str(row.type or "")

        meta = get_stage(stage_name)
        if meta is None or not meta.is_implemented():
            # Defensive: a queued job whose stage has no handler. Fail it.
            _finish_job(candidate_id, JOB_STATUS_FAILED,
                        error=f"no handler registered for stage {stage_name!r}")
            try:
                from backend.core.pipeline import on_job_finished
                on_job_finished(candidate_id)
            except Exception:
                logger.exception("pipeline orchestration failed for job %s", candidate_id)
            return candidate_id

        ctx = mark_job_running(candidate_id)
        if ctx is None:
            return None
        self._submit(stage_name, candidate_id)
        return candidate_id

    def _submit(self, stage: str, job_id: int) -> None:
        with self._lock:
            executor = self._executors.get(stage)
            if executor is None:
                executor = self._ensure_executor(stage)
            inflight = self._inflight.setdefault(stage, {})
        future = executor.submit(_run_job_safely, job_id)
        with self._lock:
            inflight[job_id] = future
        future.add_done_callback(lambda _f, jid=job_id, st=stage: self._cleanup_inflight(st, jid))

    def _cleanup_inflight(self, stage: str, job_id: int) -> None:
        with self._lock:
            inflight = self._inflight.get(stage)
            if inflight is not None:
                inflight.pop(job_id, None)
        self._wakeup.set()


_pool = StagePoolManager()


def get_pool() -> StagePoolManager:
    return _pool


def _wake_dispatcher() -> None:
    _pool.wake()


# ---- per-job execution -------------------------------------------------------

def _run_job_safely(job_id: int) -> None:
    """Execute a single job; never raise into the executor."""
    ctx: JobContext | None = None
    success = False
    try:
        ctx = _load_running_context(job_id)
        if ctx is None:
            return
        meta = get_stage(ctx.stage)
        if meta is None or meta.handler is None:
            _finish_job(job_id, JOB_STATUS_FAILED,
                        error=f"no handler for stage {ctx.stage!r}")
            return
        ctx.check_cancelled()
        if ctx.account_id and ctx.stage != "register":
            ctx.require_identity()
        meta.handler(ctx)
        _finish_job(job_id, JOB_STATUS_SUCCEEDED)
        success = True
    except JobCancelled as exc:
        _finish_job(job_id, JOB_STATUS_CANCELLED, error=str(exc) or "cancelled")
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("job %s failed", job_id)
        _finish_job(job_id, JOB_STATUS_FAILED,
                    error=str(exc) or exc.__class__.__name__, traceback=tb)
    finally:
        if ctx is not None:
            try:
                ctx.auto_release_all(success=success)
            except Exception:
                logger.exception("auto release failed for job %s", job_id)
        try:
            from backend.core.pipeline import on_job_finished
            on_job_finished(job_id)
        except Exception:
            logger.exception("pipeline orchestration failed for job %s", job_id)


def _load_running_context(job_id: int) -> JobContext | None:
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None or job.status != JOB_STATUS_RUNNING:
            return None
        return JobContext(
            job_id=int(job.id or 0),
            pipeline_id=job.pipeline_id,
            account_id=job.account_id,
            payment_link_id=job.payment_link_id,
            proxy_id=job.proxy_id,
            proxy_url=job.proxy_url,
            stage=str(job.type or ""),
            input=json_loads(job.input_json, fallback={}) or {},
        )


def _finish_job(
    job_id: int,
    status: str,
    *,
    error: str = "",
    traceback: str = "",
) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.error = error
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        s.add(job)
        if error:
            s.add(JobEvent(
                job_id=job.id or 0,
                pipeline_id=job.pipeline_id,
                level="error" if status == JOB_STATUS_FAILED else "warning",
                event_type="status",
                message=f"job {status}: {error}",
                payload_json=json_dumps({"traceback": traceback}) if traceback else "{}",
            ))
        else:
            s.add(JobEvent(
                job_id=job.id or 0,
                pipeline_id=job.pipeline_id,
                level="info",
                event_type="status",
                message=f"job {status}",
            ))
    _publish(job_id, {"kind": "status", "status": status, "error": error})
