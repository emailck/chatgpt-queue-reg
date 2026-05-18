"""Multi-project SMS pool.

`acquire(project=...)` is required; the pool routes by `sms_projects.name`
to a provider adapter. v1 only ships `smstome`. Adding new providers later
is a matter of dropping a new module under `pools/sms_providers/`.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.pools.base import Resource, ResourceUnavailable
from backend.core.pools.sms_providers.base import SmsProvider, SmsTicket
from backend.core.pools.sms_providers.smstome import SmstomeProvider
from backend.models.sms_project import SmsProject


PROVIDER_REGISTRY: dict[str, type[SmsProvider]] = {
    "smstome": SmstomeProvider,
}


class SmsPool:
    name = "sms_pool"

    def _resolve_provider(self, project: str) -> tuple[SmsProvider, SmsProject]:
        if not project:
            raise ResourceUnavailable(self.name, project, "sms_pool requires a project")
        with Session(engine) as s:
            row = s.exec(
                select(SmsProject)
                .where(SmsProject.name == project)
                .where(SmsProject.enabled == True)  # noqa: E712
            ).first()
        if row is None:
            raise ResourceUnavailable(self.name, project, "no enabled sms_project with this name")
        provider_cls = PROVIDER_REGISTRY.get(row.provider)
        if provider_cls is None:
            raise ResourceUnavailable(self.name, project, f"unknown provider {row.provider!r}")
        config = json_loads(row.config_json, fallback={}) or {}
        if not isinstance(config, dict):
            config = {}
        return provider_cls(config=config), row

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        provider, row = self._resolve_provider(project or "")
        ticket: Optional[SmsTicket] = provider.acquire(hint=hint or {})
        if ticket is None:
            return None
        return Resource(
            pool=self.name,
            id=ticket.id,
            project=project,
            payload={
                "phone": ticket.phone,
                "country": ticket.country,
                "provider": row.provider,
                "project": project,
                "raw": ticket.raw,
            },
        )

    def release(self, resource, *, outcome, reason: str = "") -> None:
        project = resource.project or (resource.payload or {}).get("project") or ""
        if not project:
            return
        try:
            provider, _row = self._resolve_provider(project)
        except ResourceUnavailable:
            return
        provider.release(
            ticket_id=resource.id,
            payload=resource.payload or {},
            outcome=outcome.value if hasattr(outcome, "value") else str(outcome),
            reason=reason,
        )

    def stats(self) -> dict[str, Any]:
        with Session(engine) as s:
            rows = list(s.exec(select(SmsProject)).all())
        return {
            "projects": [
                {"id": r.id, "name": r.name, "provider": r.provider, "enabled": r.enabled}
                for r in rows
            ],
            "total": len(rows),
            "enabled": sum(1 for r in rows if r.enabled),
        }


sms_pool = SmsPool()
