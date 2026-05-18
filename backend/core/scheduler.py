"""Background schedulers for backend maintenance jobs."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import or_, select as sa_select
from sqlmodel import Session

from backend.core.constants import JOB_STATUS_QUEUED, JOB_STATUS_RUNNING
from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.codex_token import CodexToken
from backend.models.job import Job

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60
RT_SYNC_STAGE = "rt_keepalive"


class RtPoolSyncScheduler:
    def __init__(self, *, interval_seconds: int = SCAN_INTERVAL_SECONDS) -> None:
        self.interval_seconds = max(1, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="cqr-sub2api-rt-sync-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("sub2api RT pool sync scheduler started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def scan_once(self) -> int:
        now = utcnow()
        due: list[tuple[int, int, int | None, str]] = []
        with Session(engine) as s:
            rows = list(
                s.exec(
                    sa_select(CodexToken)
                    .where(CodexToken.alive == True)  # noqa: E712
                    .where(or_(CodexToken.next_refresh_at == None, CodexToken.next_refresh_at <= now))  # noqa: E711
                    .order_by(CodexToken.next_refresh_at.asc())
                    .limit(100)
                ).scalars()
            )
            for row in rows:
                account_id = int(row.account_id or 0)
                token_id = int(row.id or 0)
                if not token_id or self._has_active_job(s, token_id):
                    continue
                account = s.get(ChatGPTAccount, account_id) if account_id else None
                due.append((
                    account_id,
                    token_id,
                    account.proxy_id if account else None,
                    account.proxy_url if account else "",
                ))

        enqueued = 0
        for account_id, token_id, proxy_id, proxy_url in due:
            try:
                enqueue_job(
                    type=RT_SYNC_STAGE,
                    input={"account_id": account_id, "codex_token_id": token_id},
                    account_id=account_id or None,
                    proxy_id=proxy_id,
                    proxy_url=proxy_url,
                )
                enqueued += 1
            except Exception:
                logger.exception("failed to enqueue sub2api RT sync for token_id=%s", token_id)
        return enqueued

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = self.scan_once()
                if count:
                    logger.info("enqueued %s sub2api RT sync job(s)", count)
            except Exception:
                logger.exception("sub2api RT pool sync scan failed")
            self._stop.wait(self.interval_seconds)

    @staticmethod
    def _has_active_job(s: Session, token_id: int) -> bool:
        rows = s.exec(
            sa_select(Job)
            .where(Job.type == RT_SYNC_STAGE)
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
        ).scalars()
        for job in rows:
            payload = json_loads(job.input_json, fallback={}) or {}
            if isinstance(payload, dict) and int(payload.get("codex_token_id") or 0) == token_id:
                return True
        return False


_scheduler = RtPoolSyncScheduler()


def get_scheduler() -> RtPoolSyncScheduler:
    return _scheduler


RtKeepaliveScheduler = RtPoolSyncScheduler
