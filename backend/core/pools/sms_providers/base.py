"""SMS provider adapter interface.

`SmsPool` keeps a dict of `name -> provider class`. Each provider is
instantiated per-acquire with the matching `sms_projects.config_json`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SmsTicket:
    id: str                # opaque per-provider key (e.g. activation id)
    phone: str
    country: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class SmsProvider(Protocol):
    def __init__(self, *, config: dict[str, Any]) -> None: ...
    def acquire(self, *, hint: dict[str, Any]) -> SmsTicket | None: ...
    def release(
        self,
        *,
        ticket_id: str,
        payload: dict[str, Any],
        outcome: str,
        reason: str = "",
    ) -> None: ...
    def fetch_code(self, *, ticket_id: str, timeout_seconds: int = 120) -> str | None: ...
