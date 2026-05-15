"""JobContext: the only object flows are allowed to use to talk to the system.

Provides:

  - ctx.log(message, level="info", payload=None)
  - ctx.check_cancelled()
  - ctx.update_result(partial)
  - ctx.attach_account(account_id)
  - ctx.attach_payment_link(payment_link_id)

All writes go through `session_scope()` so they are durable and visible to
the API layer immediately.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlmodel import Session

from backend.core.constants import JOB_STATUS_RUNNING
from backend.core.db import engine, session_scope
from backend.core.errors import JobCancelled
from backend.core.json_utils import json_dumps, json_loads
from backend.core.time_utils import utcnow
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline


_BUS_LISTENERS: list[Callable[[int, dict[str, Any]], None]] = []
_BUS_LOCK = threading.Lock()


def subscribe_job_events(callback: Callable[[int, dict[str, Any]], None]) -> Callable[[], None]:
    """Register an in-process listener for job events.

    Used by the SSE log streamer.  Returns an unsubscribe callable.
    """
    with _BUS_LOCK:
        _BUS_LISTENERS.append(callback)

    def _unsubscribe() -> None:
        with _BUS_LOCK:
            try:
                _BUS_LISTENERS.remove(callback)
            except ValueError:
                pass

    return _unsubscribe


def _publish(job_id: int, event: dict[str, Any]) -> None:
    with _BUS_LOCK:
        listeners = list(_BUS_LISTENERS)
    for cb in listeners:
        try:
            cb(job_id, event)
        except Exception:
            # Listener errors must not break the worker.
            continue


@dataclass
class JobContext:
    job_id: int
    pipeline_id: int | None = None
    account_id: int | None = None
    payment_link_id: int | None = None
    proxy_url: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    _result: dict[str, Any] = field(default_factory=dict)

    # ---- logging ----
    def log(
        self,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        event_type: str = "log",
    ) -> None:
        message = str(message or "")
        with session_scope() as s:
            evt = JobEvent(
                job_id=self.job_id,
                pipeline_id=self.pipeline_id,
                level=level,
                event_type=event_type,
                message=message,
                payload_json=json_dumps(payload or {}),
            )
            s.add(evt)
            job = s.get(Job, self.job_id)
            if job is not None:
                job.updated_at = utcnow()
                s.add(job)
        _publish(self.job_id, {
            "kind": "event",
            "level": level,
            "message": message,
            "event_type": event_type,
            "payload": payload or {},
        })

    # ---- cancellation ----
    def check_cancelled(self) -> None:
        with Session(engine) as s:
            job = s.get(Job, self.job_id)
            if job is None:
                raise JobCancelled("job missing")
            if job.cancel_requested:
                raise JobCancelled("cancellation requested")
            if self.pipeline_id is not None:
                pipeline = s.get(Pipeline, self.pipeline_id)
                if pipeline is not None and pipeline.cancel_requested:
                    raise JobCancelled("pipeline cancelled")

    # ---- result ----
    def update_result(self, partial: dict[str, Any]) -> None:
        if not isinstance(partial, dict):
            return
        merged = {**self._result, **partial}
        self._result = merged
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            existing = json_loads(job.result_json, fallback={}) or {}
            if isinstance(existing, dict):
                existing.update(partial)
                job.result_json = json_dumps(existing)
            else:
                job.result_json = json_dumps(partial)
            job.updated_at = utcnow()
            s.add(job)
        _publish(self.job_id, {"kind": "result", "result": dict(self._result)})

    @property
    def result(self) -> dict[str, Any]:
        return dict(self._result)

    # ---- attaching domain rows ----
    def attach_account(self, account_id: int) -> None:
        self.account_id = account_id
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.account_id = account_id
                job.updated_at = utcnow()
                s.add(job)
            if self.pipeline_id is not None:
                pipeline = s.get(Pipeline, self.pipeline_id)
                if pipeline is not None:
                    pipeline.account_id = account_id
                    pipeline.updated_at = utcnow()
                    s.add(pipeline)

    def attach_payment_link(self, payment_link_id: int) -> None:
        self.payment_link_id = payment_link_id
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.payment_link_id = payment_link_id
                job.updated_at = utcnow()
                s.add(job)
            if self.pipeline_id is not None:
                pipeline = s.get(Pipeline, self.pipeline_id)
                if pipeline is not None:
                    pipeline.payment_link_id = payment_link_id
                    pipeline.updated_at = utcnow()
                    s.add(pipeline)


def mark_job_running(job_id: int) -> JobContext | None:
    """Atomically flip a job from queued to running.

    Returns a `JobContext` when this call won the race, otherwise None.
    """
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or job.status != "queued":
            return None
        job.status = JOB_STATUS_RUNNING
        job.attempt += 1
        job.started_at = utcnow()
        job.updated_at = utcnow()
        s.add(job)
        ctx = JobContext(
            job_id=job.id or 0,
            pipeline_id=job.pipeline_id,
            account_id=job.account_id,
            payment_link_id=job.payment_link_id,
            proxy_url=job.proxy_url,
            input=json_loads(job.input_json, fallback={}) or {},
        )
    _publish(job_id, {"kind": "status", "status": "running"})
    return ctx
