"""Email resource pool.

Wraps the existing `backend.integrations.mail.pool` helpers so the v2
`ResourcePool` Protocol is satisfied.  We deliberately do NOT rewrite the
legacy helpers: they already implement the atomic claim/requeue/blacklist
semantics for `email_accounts`.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.core.pools.base import AcquireOutcome, Resource
from backend.integrations.mail import pool as legacy_email_pool


class EmailPool:
    name = "email_pool"

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        fixed = (hint or {}).get("fixed_email") if hint else None
        row = legacy_email_pool.claim(fixed_email=fixed)
        if row is None:
            return None
        return Resource(
            pool=self.name,
            id=str(row.id),
            payload={
                "email": row.email,
                "password": row.password,
                "refresh_token": row.refresh_token,
                "api_base": row.api_base,
                "metadata_json": row.metadata_json,
            },
        )

    def release(self, resource, *, outcome, reason: str = "") -> None:
        email = (resource.payload or {}).get("email") or ""
        if not email:
            return
        if outcome == AcquireOutcome.CONSUMED:
            legacy_email_pool.mark_consumed(email=email, note=reason or "registered")
        elif outcome == AcquireOutcome.BANNED:
            legacy_email_pool.blacklist(email=email, note=reason or "banned")
        else:  # REUSABLE / FAILED → requeue
            legacy_email_pool.requeue(email=email)

    def stats(self) -> dict[str, Any]:
        return legacy_email_pool.stats()


email_pool = EmailPool()
