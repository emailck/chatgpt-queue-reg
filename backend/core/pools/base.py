"""Resource pool abstractions (interfaces only).

Concrete implementations are added in P2. Everything in this module is
storage-engine agnostic: a pool can be DB-backed, in-memory, or an external
HTTP service.

Contract:
  * `acquire` returns a `Resource` or `None` if nothing is available.
  * Calling `acquire` MUST mark the resource as in_use atomically.
  * `release` is mandatory. Outcome decides what state the resource ends in:
      - consumed   : single-use, never returned (email after register, card after pay)
      - reusable   : returned to the pool, available again
      - failed     : transient failure, may go back or be retried later
      - banned     : permanently disabled, manual review required
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


class AcquireOutcome(str, Enum):
    CONSUMED = "consumed"
    REUSABLE = "reusable"
    FAILED = "failed"
    BANNED = "banned"


@dataclass
class Resource:
    """A single piece of resource handed out by a pool.

    `id` is opaque to callers; the pool uses it on release to find the row.
    `payload` carries the actual data the stage code reads (email address,
    card number etc).
    """

    pool: str                    # which pool produced this (e.g. "email_pool")
    id: str                      # opaque key used on release
    payload: dict[str, Any] = field(default_factory=dict)
    project: Optional[str] = None  # for pools that route by project (sms_pool)


@dataclass
class AcquiredResource:
    """Internal book-keeping inside JobContext.

    Tracks each acquired resource so the runner can auto-release on job
    termination. Stage code normally calls `ctx.release(resource, outcome=...)`
    explicitly; if it doesn't, the runner assumes `reusable` on success and
    `failed` on exception.
    """

    resource: Resource
    auto_outcome_on_success: AcquireOutcome = AcquireOutcome.REUSABLE
    auto_outcome_on_failure: AcquireOutcome = AcquireOutcome.FAILED
    released: bool = False


@runtime_checkable
class ResourcePool(Protocol):
    """All concrete resource pools must satisfy this protocol."""

    name: str

    def acquire(
        self,
        *,
        stage: str,
        job_id: int,
        project: Optional[str] = None,
        hint: Optional[dict[str, Any]] = None,
    ) -> Optional[Resource]:
        ...

    def release(
        self,
        resource: Resource,
        *,
        outcome: AcquireOutcome,
        reason: str = "",
    ) -> None:
        ...

    def stats(self) -> dict[str, Any]:
        ...


class ResourceUnavailable(RuntimeError):
    """Raised by `JobContext.acquire` when a required pool has nothing to give."""

    def __init__(self, pool: str, project: Optional[str] = None, reason: str = ""):
        self.pool = pool
        self.project = project
        msg = f"resource pool {pool!r} unavailable"
        if project:
            msg += f" (project={project!r})"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


__all__ = [
    "AcquireOutcome",
    "AcquiredResource",
    "Resource",
    "ResourcePool",
    "ResourceUnavailable",
]
