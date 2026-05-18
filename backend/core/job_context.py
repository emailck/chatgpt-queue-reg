"""JobContext: the only object stages/flows are allowed to use to talk to the system.

Provides:

  - ctx.log(message, level="info", payload=None)
  - ctx.check_cancelled()
  - ctx.update_result(partial)
  - ctx.attach_account(account_id)
  - ctx.attach_payment_link(payment_link_id)

P1 additions:

  - ctx.stage           — stage name (== Job.type)
  - ctx.identity        — AccountIdentity hydrated from chatgpt_accounts (lazy)
  - ctx.acquire(pool, project=?, hint=?) -> Resource
  - ctx.release(resource, outcome=?, reason=?)
  - automatic resource release on job termination (handled by the runner)

All writes go through `session_scope()` so they are durable and visible to
the API layer immediately.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlmodel import Session

from backend.core.constants import JOB_STATUS_RUNNING
from backend.core.db import engine, session_scope
from backend.core.errors import JobCancelled
from backend.core.json_utils import json_dumps, json_loads
from backend.core.time_utils import utcnow
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline

logger = logging.getLogger(__name__)


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


# ---- identity --------------------------------------------------------------


@dataclass
class AccountIdentity:
    """Frozen identity bundle for an account.

    Once `register` succeeds for an account, every subsequent stage MUST run
    with this exact (proxy_url, user_agent, fingerprint, cookies, local_storage).
    See ARCHITECTURE.md §4.
    """

    account_id: int
    proxy_id: int | None = None
    proxy_region: str = ""
    proxy_url: str = ""
    user_agent: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)
    local_storage: dict[str, Any] = field(default_factory=dict)


def _load_identity(account_id: Optional[int]) -> Optional[AccountIdentity]:
    """Hydrate an `AccountIdentity` from `chatgpt_accounts` if possible.

    Defensive: this code is called from every stage, but the `chatgpt_accounts`
    table may not yet have the identity columns added (those land in P3).
    Read what is available; tolerate missing columns.
    """
    if not account_id:
        return None
    try:
        # Imported lazily so test environments without the model still load this module.
        from backend.models.account import ChatGPTAccount  # type: ignore
    except Exception:
        return None

    with Session(engine) as s:
        try:
            acc = s.get(ChatGPTAccount, int(account_id))
        except Exception:
            return None
        if acc is None:
            return None

        def _safe_get(name: str, default: Any = "") -> Any:
            return getattr(acc, name, default)

        def _safe_dict_json(name: str) -> dict[str, Any]:
            raw = getattr(acc, name, None)
            if not raw:
                return {}
            parsed = json_loads(raw, fallback={}) or {}
            return parsed if isinstance(parsed, dict) else {}

        def _safe_cookie_json(name: str) -> list[dict[str, Any]]:
            raw = getattr(acc, name, None)
            if not raw:
                return []
            parsed = json_loads(raw, fallback=[]) or []
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            return []

        proxy_id = int(_safe_get("proxy_id", 0) or 0) or None
        proxy_region = ""
        if proxy_id:
            try:
                from backend.models.proxy import Proxy

                proxy = s.get(Proxy, proxy_id)
                proxy_region = str(proxy.region or "") if proxy else ""
            except Exception:
                proxy_region = ""
        return AccountIdentity(
            account_id=int(account_id),
            proxy_id=proxy_id,
            proxy_region=proxy_region,
            proxy_url=str(_safe_get("proxy_url", "") or ""),
            user_agent=str(_safe_get("user_agent", "") or ""),
            fingerprint=_safe_dict_json("browser_fingerprint_json"),
            cookies=_safe_cookie_json("cookies_json"),
            local_storage=_safe_dict_json("local_storage_json"),
        )


# ---- JobContext ------------------------------------------------------------


@dataclass
class JobContext:
    job_id: int
    pipeline_id: int | None = None
    account_id: int | None = None
    payment_link_id: int | None = None
    proxy_id: int | None = None
    proxy_url: str = ""
    stage: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    _result: dict[str, Any] = field(default_factory=dict)
    # Filled in lazily by `.identity` property; cached.
    _identity_cache: Optional[AccountIdentity] = None
    _identity_loaded: bool = False
    # Internal book-keeping for resource pool acquisitions.
    _acquired: list[Any] = field(default_factory=list)  # list[AcquiredResource]; avoid cyclic import

    # ---- identity (lazy) ----
    @property
    def identity(self) -> Optional[AccountIdentity]:
        if not self._identity_loaded:
            self._identity_cache = _load_identity(self.account_id)
            self._identity_loaded = True
        return self._identity_cache

    def reload_identity(self) -> Optional[AccountIdentity]:
        """Force re-hydrate identity (e.g. after register stage just wrote it)."""
        self._identity_cache = _load_identity(self.account_id)
        self._identity_loaded = True
        return self._identity_cache

    def effective_proxy_url(self) -> str:
        """Return the proxy bound to this account, or the pre-account job proxy."""
        ident = self.identity
        if ident is not None:
            return ident.proxy_url
        if self.account_id:
            return ""
        return self.proxy_url or ""

    def require_identity(self) -> AccountIdentity:
        ident = self.reload_identity()
        if ident is None:
            raise RuntimeError(f"account identity not found for account_id={self.account_id}")
        return ident

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

    # Alias for the contract documented in ARCHITECTURE.md §6.
    def emit_result(self, **fields: Any) -> None:
        self.update_result(dict(fields))

    @property
    def result(self) -> dict[str, Any]:
        return dict(self._result)

    # ---- attaching domain rows ----
    def attach_account(self, account_id: int) -> None:
        self.account_id = account_id
        # Identity needs re-hydration now that account exists.
        self._identity_loaded = False
        self._identity_cache = None
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

    def attach_proxy(self, *, proxy_id: int | None, proxy_url: str) -> None:
        self.proxy_id = proxy_id
        self.proxy_url = proxy_url or ""
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.proxy_id = proxy_id
                job.proxy_url = proxy_url or ""
                job.updated_at = utcnow()
                s.add(job)
            if self.pipeline_id is not None:
                pipeline = s.get(Pipeline, self.pipeline_id)
                if pipeline is not None:
                    pipeline.proxy_id = proxy_id
                    pipeline.proxy_url = proxy_url or ""
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

    # ---- resource pool integration ----
    def acquire(
        self,
        pool_name: str,
        *,
        project: Optional[str] = None,
        hint: Optional[dict[str, Any]] = None,
        auto_outcome_on_success: Optional[str] = None,
        auto_outcome_on_failure: Optional[str] = None,
    ):
        """Acquire a resource from `pool_name`.

        Raises `ResourceUnavailable` if the pool has nothing to give. The
        resource is auto-tracked on this context; the runner releases it
        when the job terminates unless the stage already called `release`.
        """
        # Imported lazily to avoid touching pools at module import time.
        from backend.core.pools import get_resource_pool
        from backend.core.pools.base import (
            AcquiredResource,
            AcquireOutcome,
            ResourceUnavailable,
        )

        pool = get_resource_pool(pool_name)
        if pool is None:
            raise ResourceUnavailable(pool_name, project, "pool not registered")

        resource = pool.acquire(
            stage=self.stage,
            job_id=self.job_id,
            project=project,
            hint=hint,
        )
        if resource is None:
            raise ResourceUnavailable(pool_name, project, "pool empty")

        on_success = AcquireOutcome(auto_outcome_on_success) if auto_outcome_on_success else AcquireOutcome.REUSABLE
        on_failure = AcquireOutcome(auto_outcome_on_failure) if auto_outcome_on_failure else AcquireOutcome.FAILED

        self._acquired.append(
            AcquiredResource(
                resource=resource,
                auto_outcome_on_success=on_success,
                auto_outcome_on_failure=on_failure,
            )
        )
        return resource

    def release(self, resource, *, outcome, reason: str = "") -> None:
        from backend.core.pools import get_resource_pool
        from backend.core.pools.base import AcquireOutcome

        pool = get_resource_pool(resource.pool)
        if pool is None:
            return

        outcome_enum = outcome if isinstance(outcome, AcquireOutcome) else AcquireOutcome(outcome)
        try:
            pool.release(resource, outcome=outcome_enum, reason=reason)
        except Exception:
            logger.exception("resource release failed: pool=%s id=%s", resource.pool, resource.id)

        for entry in self._acquired:
            if entry.resource is resource and not entry.released:
                entry.released = True
                break

    def auto_release_all(self, *, success: bool) -> None:
        """Called by the runner after handler returns/raises.

        Releases any resource the stage forgot. Outcome chosen per
        `AcquiredResource.auto_outcome_on_*`.
        """
        from backend.core.pools import get_resource_pool

        for entry in list(self._acquired):
            if entry.released:
                continue
            pool = get_resource_pool(entry.resource.pool)
            if pool is None:
                continue
            outcome = entry.auto_outcome_on_success if success else entry.auto_outcome_on_failure
            try:
                pool.release(entry.resource, outcome=outcome, reason="auto")
            except Exception:
                logger.exception("auto release failed: pool=%s id=%s", entry.resource.pool, entry.resource.id)
            entry.released = True


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
            proxy_id=job.proxy_id,
            proxy_url=job.proxy_url,
            stage=str(job.type or ""),
            input=json_loads(job.input_json, fallback={}) or {},
        )
    _publish(job_id, {"kind": "status", "status": "running"})
    return ctx
