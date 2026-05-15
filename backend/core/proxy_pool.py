"""DB-backed proxy pool with weighted round-robin selection.

Mirrors the legacy `core/proxy_pool.py` but reads / writes the new `Proxy`
model, not the old `ProxyModel`.
"""
from __future__ import annotations

import threading
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from sqlmodel import Session, select

from backend.core.db import engine
from backend.core.proxy import build_requests_proxy_config
from backend.core.time_utils import utcnow
from backend.models.proxy import Proxy


def _socks_scheme_variants(url: str) -> list[str]:
    value = str(url or "").strip()
    if not value:
        return []
    try:
        parts = urlsplit(value)
    except Exception:
        return [value]
    scheme = (parts.scheme or "").lower()
    if scheme not in {"socks5", "socks5h"}:
        return [value]
    other_scheme = "socks5h" if scheme == "socks5" else "socks5"
    other = urlunsplit(parts._replace(scheme=other_scheme))
    return list(dict.fromkeys([value, other]))


class ProxyPool:
    """Pick a proxy by success rate, optionally filtered by region."""

    def __init__(self) -> None:
        self._index = 0
        self._lock = threading.Lock()

    # ---- selection ----------------------------------------------------------

    def get_next(self, region: str = "") -> Optional[str]:
        with Session(engine) as s:
            stmt = select(Proxy).where(Proxy.enabled == True)  # noqa: E712
            if region:
                stmt = stmt.where(Proxy.region == region)
            rows = s.exec(stmt).all()
        if not rows:
            return None
        rows.sort(
            key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
            reverse=True,
        )
        with self._lock:
            idx = self._index % len(rows)
            self._index += 1
        return rows[idx].url

    def acquire(self, *, region: str = "") -> Optional[str]:
        return self.get_next(region=region)

    # ---- bookkeeping --------------------------------------------------------

    def _find(self, session: Session, url: str) -> Optional[Proxy]:
        variants = _socks_scheme_variants(url)
        if not variants:
            return None
        return session.exec(select(Proxy).where(Proxy.url.in_(variants))).first()

    def report_success(self, url: str) -> None:
        if not url:
            return
        with Session(engine) as s:
            row = self._find(s, url)
            if row is None:
                return
            row.success_count += 1
            row.last_used_at = utcnow()
            row.updated_at = utcnow()
            s.add(row)
            s.commit()

    def report_failure(self, url: str) -> None:
        if not url:
            return
        with Session(engine) as s:
            row = self._find(s, url)
            if row is None:
                return
            row.fail_count += 1
            row.last_used_at = utcnow()
            row.updated_at = utcnow()
            # auto-disable after 5 straight failures with zero successes.
            if row.fail_count >= 5 and row.success_count == 0:
                row.enabled = False
            s.add(row)
            s.commit()

    # legacy alias
    def report_fail(self, url: str) -> None:
        self.report_failure(url)

    def release(self, *_args, **_kwargs) -> None:
        return None

    # ---- maintenance --------------------------------------------------------

    def check_all(self, *, probe_url: str = "https://httpbin.org/ip", timeout: float = 8.0) -> dict[str, int]:
        with Session(engine) as s:
            rows = list(s.exec(select(Proxy)).all())
        results = {"ok": 0, "fail": 0}
        for row in rows:
            try:
                resp = requests.get(
                    probe_url,
                    proxies=build_requests_proxy_config(row.url),
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    self.report_success(row.url)
                    results["ok"] += 1
                    continue
            except Exception:
                pass
            self.report_failure(row.url)
            results["fail"] += 1
        return results


proxy_pool = ProxyPool()
