"""Minimal compatibility shim for code copied from the legacy project.

The legacy ChatGPT modules import a few helpers from `core.task_runtime`,
`core.config_store`, `core.proxy_pool`, and `core.base_platform`.  We don't
want to drag the entire legacy task system into the new queue project, so we
provide thin substitutes here.

Behavior:
  - `TaskInterruption` is mapped to `JobCancelled` so existing flows still
    cooperate with cancellation.
  - `config_store` exposes `get/get_all/set/set_many/delete_many` backed by
    the new `settings` table.
  - `proxy_pool` exposes the few methods legacy code calls but is a no-op,
    since proxy assignment is handled per-pipeline in the new design.
  - `Account` / `AccountStatus` are simple dataclasses with the fields the
    copied code reads/writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from backend.core.errors import JobCancelled
from backend.core.settings import settings


# ---- task_runtime ------------------------------------------------------------

class TaskInterruption(JobCancelled):
    """Alias kept for legacy ChatGPT modules."""


class StopTaskRequested(JobCancelled):
    pass


class SkipCurrentAttemptRequested(JobCancelled):
    pass


# ---- config_store ------------------------------------------------------------

class _ConfigStoreShim:
    def get(self, key: str, default: str = "") -> str:
        return settings.get(key, default)

    def get_all(self) -> dict[str, str]:
        return settings.get_all()

    def set(self, key: str, value: str) -> None:
        settings.set(key, value)

    def set_many(self, items: dict[str, Any]) -> None:
        settings.set_many({k: str(v) for k, v in items.items()})

    def delete_many(self, keys: Iterable[str]) -> None:
        settings.delete_many(keys)


config_store = _ConfigStoreShim()
ConfigStore = _ConfigStoreShim


# ---- proxy_pool --------------------------------------------------------------

from backend.core.proxy_pool import ProxyPool, proxy_pool  # noqa: F401,E402


# ---- base_platform Account stub ---------------------------------------------

class AccountStatus(str, Enum):
    REGISTERED = "registered"
    FAILED = "failed"
    PENDING = "pending"


@dataclass
class Account:
    platform: str = "chatgpt"
    email: str = ""
    password: str = ""
    user_id: str = ""
    region: str = ""
    token: str = ""
    status: AccountStatus = AccountStatus.PENDING
    extra: dict[str, Any] = field(default_factory=dict)
