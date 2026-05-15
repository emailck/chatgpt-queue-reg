"""Pool operations for the Microsoft email mailbox.

Mirrors the legacy `OutlookMailbox` semantics (pop / requeue / mark used)
but writes to the new `email_accounts` table.

Statuses (stored in `EmailAccount.metadata_json["pool_status"]`):

  - "available": enabled, ready to be claimed
  - "claimed":   currently in use by a flow
  - "consumed":  registration succeeded, retain the row but keep it disabled
  - "blacklist": import-time OAuth probe failed or marked bad

The `enabled` boolean stays the source of truth for "claimable now":
  available  -> enabled = True
  claimed    -> enabled = False
  consumed   -> enabled = False
  blacklist  -> enabled = False
"""
from __future__ import annotations

import threading
from typing import Iterable

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.time_utils import utcnow
from backend.models.email import EmailAccount

POOL_STATUS_AVAILABLE = "available"
POOL_STATUS_CLAIMED = "claimed"
POOL_STATUS_CONSUMED = "consumed"
POOL_STATUS_BLACKLIST = "blacklist"

ALL_POOL_STATUSES = (
    POOL_STATUS_AVAILABLE,
    POOL_STATUS_CLAIMED,
    POOL_STATUS_CONSUMED,
    POOL_STATUS_BLACKLIST,
)

_pop_lock = threading.Lock()


def _get_meta(row: EmailAccount) -> dict:
    meta = json_loads(row.metadata_json, fallback={}) or {}
    return meta if isinstance(meta, dict) else {}


def _set_pool_status(row: EmailAccount, status: str, *, note: str = "") -> None:
    meta = _get_meta(row)
    meta["pool_status"] = status
    if note:
        meta["pool_status_note"] = note
    row.metadata_json = json_dumps(meta)


def get_pool_status(row: EmailAccount) -> str:
    return str(_get_meta(row).get("pool_status") or
               (POOL_STATUS_AVAILABLE if row.enabled else POOL_STATUS_CONSUMED))


def claim(*, fixed_email: str | None = None) -> EmailAccount | None:
    """Atomically pick an available mailbox and flip it to `claimed`.

    Returns None when the pool is empty.  When `fixed_email` is given, only
    that exact address is considered.
    """
    with _pop_lock:
        with session_scope() as s:
            stmt = (
                sa_select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.enabled == True)  # noqa: E712
                .order_by(EmailAccount.id)
            )
            if fixed_email:
                stmt = stmt.where(EmailAccount.email == str(fixed_email).strip())
            row = s.exec(stmt).scalars().first()
            if row is None:
                return None
            row.enabled = False
            _set_pool_status(row, POOL_STATUS_CLAIMED)
            row.updated_at = utcnow()
            s.add(row)
            s.commit()
            s.refresh(row)
            # Detach so callers don't accidentally re-bind to a closed session.
            s.expunge(row)
            return row


def requeue(*, email: str) -> bool:
    """Put an email back into the pool (after a failed registration)."""
    target = str(email or "").strip()
    if not target:
        return False
    with session_scope() as s:
        row = s.exec(
            sa_select(EmailAccount)
            .where(EmailAccount.provider == "microsoft")
            .where(EmailAccount.email == target)
        ).scalars().first()
        if row is None:
            return False
        row.enabled = True
        _set_pool_status(row, POOL_STATUS_AVAILABLE, note="requeued")
        row.updated_at = utcnow()
        s.add(row)
    return True


def mark_consumed(*, email: str, note: str = "registered") -> bool:
    target = str(email or "").strip()
    if not target:
        return False
    with session_scope() as s:
        row = s.exec(
            sa_select(EmailAccount)
            .where(EmailAccount.provider == "microsoft")
            .where(EmailAccount.email == target)
        ).scalars().first()
        if row is None:
            return False
        row.enabled = False
        _set_pool_status(row, POOL_STATUS_CONSUMED, note=note)
        row.updated_at = utcnow()
        s.add(row)
    return True


def blacklist(*, email: str, note: str = "") -> bool:
    target = str(email or "").strip()
    if not target:
        return False
    with session_scope() as s:
        row = s.exec(
            sa_select(EmailAccount)
            .where(EmailAccount.provider == "microsoft")
            .where(EmailAccount.email == target)
        ).scalars().first()
        if row is None:
            return False
        row.enabled = False
        _set_pool_status(row, POOL_STATUS_BLACKLIST, note=note)
        row.updated_at = utcnow()
        s.add(row)
    return True


def stats() -> dict[str, int]:
    """Count rows by pool_status for dashboards."""
    counts: dict[str, int] = {k: 0 for k in ALL_POOL_STATUSES}
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(EmailAccount).where(EmailAccount.provider == "microsoft")
            ).scalars()
        )
    for row in rows:
        counts[get_pool_status(row)] = counts.get(get_pool_status(row), 0) + 1
    counts["total"] = len(rows)
    return counts


def batch_delete(emails: Iterable[str]) -> dict[str, list[str] | int]:
    """Drop rows by email address."""
    wanted = [str(e or "").strip() for e in emails if str(e or "").strip()]
    deleted: list[str] = []
    not_found: list[str] = []
    if not wanted:
        return {"deleted": 0, "not_found": [], "total_requested": 0}
    with session_scope() as s:
        for email in wanted:
            row = s.exec(
                sa_select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.email == email)
            ).scalars().first()
            if row is None:
                not_found.append(email)
                continue
            s.delete(row)
            deleted.append(email)
    return {
        "deleted": len(deleted),
        "deleted_emails": deleted,
        "not_found": not_found,
        "total_requested": len(wanted),
    }
