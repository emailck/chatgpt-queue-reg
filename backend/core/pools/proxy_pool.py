"""ResourcePool wrapper around the DB-backed proxy pool.

Register/payment_link use the account-bound proxy. Payment can ask for a
region-specific proxy and exclude the account-bound proxy.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from backend.core.db import engine
from backend.core.pools.base import AcquireOutcome, Resource
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.proxy import Proxy


class ProxyResourcePool:
    name = "proxy_pool"

    def acquire(self, *, stage, job_id, project=None, hint=None) -> Optional[Resource]:
        hint = hint or {}
        account_id = int(hint.get("account_id") or 0)
        requested_proxy_id = int(hint.get("proxy_id") or 0)
        region = str(hint.get("region") or "").strip()
        exclude_proxy_id = int(hint.get("exclude_proxy_id") or 0)
        exclude_url = str(hint.get("exclude_url") or "").strip()

        if account_id and not region and not requested_proxy_id:
            with Session(engine) as s:
                account = s.get(ChatGPTAccount, account_id)
                if account is not None and (account.proxy_url or "").strip():
                    proxy = s.get(Proxy, int(account.proxy_id or 0)) if account.proxy_id else None
                    return self._resource_from_values(
                        proxy_id=account.proxy_id,
                        url=account.proxy_url,
                        region=proxy.region if proxy else "",
                        account_pinned=True,
                    )

        with Session(engine) as s:
            stmt = select(Proxy).where(Proxy.enabled == True)  # noqa: E712
            if requested_proxy_id:
                stmt = stmt.where(Proxy.id == requested_proxy_id)
            if region:
                stmt = stmt.where(Proxy.region == region)
            if exclude_proxy_id:
                stmt = stmt.where(Proxy.id != exclude_proxy_id)
            if exclude_url:
                stmt = stmt.where(Proxy.url != exclude_url)
            rows = list(s.exec(stmt).all())
            if not rows:
                return None
            rows.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True,
            )
            row = rows[0]
        return self._resource_from_values(
            proxy_id=row.id,
            url=row.url,
            region=row.region,
            account_pinned=False,
        )

    def release(self, resource, *, outcome, reason: str = "") -> None:
        proxy_id = int((resource.payload or {}).get("proxy_id") or 0)
        url = str((resource.payload or {}).get("url") or resource.id or "")
        with Session(engine) as s:
            row = s.get(Proxy, proxy_id) if proxy_id else None
            if row is None and url:
                row = s.exec(select(Proxy).where(Proxy.url == url)).first()
            if row is None:
                return
            if outcome in (AcquireOutcome.CONSUMED, AcquireOutcome.REUSABLE):
                row.success_count += 1
            elif outcome in (AcquireOutcome.FAILED, AcquireOutcome.BANNED):
                row.fail_count += 1
                if outcome == AcquireOutcome.BANNED or (row.fail_count >= 5 and row.success_count == 0):
                    row.enabled = False
            row.last_used_at = utcnow()
            row.updated_at = utcnow()
            s.add(row)
            s.commit()

    def _resource_from_values(self, *, proxy_id: int | None, url: str, region: str, account_pinned: bool) -> Resource:
        return Resource(
            pool=self.name,
            id=str(proxy_id or url),
            payload={
                "proxy_id": proxy_id,
                "url": url,
                "region": region,
                "account_pinned": account_pinned,
            },
        )

    def stats(self) -> dict[str, Any]:
        with Session(engine) as s:
            rows = list(s.exec(select(Proxy)).all())
        return {
            "total": len(rows),
            "enabled": sum(1 for r in rows if r.enabled),
            "disabled": sum(1 for r in rows if not r.enabled),
        }


proxy_pool = ProxyResourcePool()
