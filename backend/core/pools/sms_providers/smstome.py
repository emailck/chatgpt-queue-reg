"""smstome SMS provider adapter (v1 stub).

`compat/smstome_tool.py` is a no-op in this project — the legacy ChatGPT
phone OTP path has been disabled. This adapter therefore raises on
`acquire()` until a real backend is wired up. It still satisfies the
`SmsProvider` Protocol so `sms_pool` can route by `sms_projects.name`.
"""
from __future__ import annotations

from typing import Any

from backend.core.pools.sms_providers.base import SmsProvider, SmsTicket


class SmstomeProvider(SmsProvider):
    def __init__(self, *, config: dict[str, Any]) -> None:
        self.config = dict(config or {})

    def acquire(self, *, hint: dict[str, Any]) -> SmsTicket | None:
        raise NotImplementedError(
            "smstome provider is not implemented in v1; add API integration"
            " under backend/core/pools/sms_providers/smstome.py"
        )

    def release(
        self,
        *,
        ticket_id: str,
        payload: dict[str, Any],
        outcome: str,
        reason: str = "",
    ) -> None:
        return None

    def fetch_code(self, *, ticket_id: str, timeout_seconds: int = 120) -> str | None:
        return None
