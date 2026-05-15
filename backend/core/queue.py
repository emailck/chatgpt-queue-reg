"""Multi-threaded queue worker pool + helper to enqueue jobs.

Design notes
------------

* Active workers run in a `ThreadPoolExecutor`. The pool size is read from
  settings (`worker_concurrency`, default 3).
* The dispatcher loop polls `jobs` for `queued` rows in priority + FIFO order
  and atomically claims one via `mark_job_running`.
* Cancellation is cooperative: API writes `cancel_requested=True`, flows call
  `ctx.check_cancelled()`, the worker also pre-checks before starting.
* On boot, `recover_orphan_jobs()` flips lingering `running` rows to
  `interrupted` and updates their pipelines.  No work is auto-resumed; users
  decide whether to retry.
* Flows run synchronously from the worker thread — playwright/curl_cffi are
  blocking-IO so threading is enough.
"""
from __future__ import annotations

import logging
import threading
import time
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
from backend.core.flow_registry import get_flow
from backend.core.job_context import JobContext, _publish, mark_job_running
from backend.core.json_utils import json_dumps
from backend.core.settings import settings
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
    with session_scope() as s:
        job = Job(
            type=type,
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
    """Flip lingering 'running' jobs to 'interrupted' on boot."""
    fixed = 0
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
    return fixed


# ---- worker pool -------------------------------------------------------------

class QueueWorkerPool:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._inflight: dict[int, Future] = {}
        self._inflight_lock = threading.Lock()
        self._concurrency = max(1, settings.get_int("worker_concurrency", DEFAULT_WORKER_CONCURRENCY))

    @property
    def concurrency(self) -> int:
        return self._concurrency

    def start(self) -> None:
        if self._dispatcher is not None:
            return
        self._concurrency = max(
            1, settings.get_int("worker_concurrency", DEFAULT_WORKER_CONCURRENCY)
        )
        self._executor = ThreadPoolExecutor(
            max_workers=self._concurrency, thread_name_prefix="cqr-worker"
        )
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="cqr-dispatcher", daemon=True
        )
        self._dispatcher.start()
        logger.info("queue worker pool started (concurrency=%s)", self._concurrency)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=timeout)
            self._dispatcher = None
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

    def wake(self) -> None:
        self._wakeup.set()

    def set_concurrency(self, value: int) -> int:
        """Resize the pool at runtime. Returns the effective concurrency.

        We swap the executor under the inflight lock so existing futures keep
        their original executor until they finish; new tasks land on the new
        one immediately.
        """
        new_value = max(1, int(value or 0))
        if new_value == self._concurrency and self._executor is not None:
            return self._concurrency
        old_executor = self._executor
        new_executor = ThreadPoolExecutor(
            max_workers=new_value, thread_name_prefix="cqr-worker"
        )
        self._executor = new_executor
        self._concurrency = new_value
        # Persist so it survives restarts.
        try:
            settings.set("worker_concurrency", str(new_value))
        except Exception:
            pass
        # Drain old executor in the background; do NOT block API.
        if old_executor is not None:
            threading.Thread(
                target=lambda: old_executor.shutdown(wait=True, cancel_futures=False),
                name="cqr-pool-resize",
                daemon=True,
            ).start()
        logger.info("queue worker pool resized to concurrency=%s", new_value)
        self._wakeup.set()
        return new_value

    # -- internals --

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            ran_any = False
            while not self._stop.is_set():
                if not self._has_capacity():
                    break
                claimed_id = self._claim_one()
                if claimed_id is None:
                    break
                self._submit(claimed_id)
                ran_any = True
            # poll roughly every second; flows wake us via _wake_dispatcher.
            if not ran_any:
                self._wakeup.wait(timeout=1.0)
                self._wakeup.clear()

    def _has_capacity(self) -> bool:
        with self._inflight_lock:
            active = sum(1 for f in self._inflight.values() if not f.done())
        return active < self._concurrency

    def _claim_one(self) -> int | None:
        with Session(engine) as s:
            stmt = (
                sa_select(Job)
                .where(Job.status == JOB_STATUS_QUEUED)
                .order_by(Job.priority.desc(), Job.id.asc())
                .limit(1)
            )
            row = s.exec(stmt).scalars().first()
            if row is None:
                return None
            candidate_id = int(row.id or 0)
        ctx = mark_job_running(candidate_id)
        if ctx is None:
            return None
        return candidate_id

    def _submit(self, job_id: int) -> None:
        executor = self._executor
        if executor is None:
            return
        future = executor.submit(_run_job_safely, job_id)
        with self._inflight_lock:
            self._inflight[job_id] = future
        future.add_done_callback(lambda _f, jid=job_id: self._cleanup_inflight(jid))

    def _cleanup_inflight(self, job_id: int) -> None:
        with self._inflight_lock:
            self._inflight.pop(job_id, None)
        # New capacity may unlock another job.
        self._wakeup.set()


_pool = QueueWorkerPool()


def get_pool() -> QueueWorkerPool:
    return _pool


def _wake_dispatcher() -> None:
    _pool.wake()


# ---- per-job execution -------------------------------------------------------

def _run_job_safely(job_id: int) -> None:
    """Execute a single job; never raise into the executor."""
    ctx: JobContext | None = None
    try:
        ctx = _load_running_context(job_id)
        if ctx is None:
            return
        flow = get_flow(_lookup_job_type(job_id))
        if flow is None:
            _finish_job(job_id, JOB_STATUS_FAILED, error=f"no flow registered for job type")
            return
        ctx.check_cancelled()
        flow(ctx)
        _finish_job(job_id, JOB_STATUS_SUCCEEDED)
    except JobCancelled as exc:
        _finish_job(job_id, JOB_STATUS_CANCELLED, error=str(exc) or "cancelled")
    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("job %s failed", job_id)
        _finish_job(job_id, JOB_STATUS_FAILED, error=str(exc) or exc.__class__.__name__, traceback=tb)
    finally:
        # Pipeline orchestrator runs on every terminal event.
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
            proxy_url=job.proxy_url,
            input=__import__("backend.core.json_utils", fromlist=["json_loads"]).json_loads(
                job.input_json, fallback={}
            ) or {},
        )


def _lookup_job_type(job_id: int) -> str:
    with Session(engine) as s:
        job = s.get(Job, job_id)
        return str(job.type or "") if job is not None else ""


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
