"""Background schedulers for backend maintenance jobs."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import or_, select as sa_select
from sqlmodel import Session

from backend.core.constants import (
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
)
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.models.job import Job, JobEvent
from backend.models.pipeline import Pipeline

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 60
SUB2API_SYNC_STAGE = "sub2api_sync"
FAILED_RETRY_META_KEY = "auto_retry"
FAILED_RETRY_MAX_ATTEMPTS = 3
FAILED_RETRY_LIMIT_PER_SCAN = 50


class Sub2ApiSyncScheduler:
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
        logger.info("sub2api account sync scheduler started")

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
                    sa_select(OpenAIRefreshToken)
                    .where(OpenAIRefreshToken.enabled == True)  # noqa: E712
                    .where(or_(OpenAIRefreshToken.next_sync_at == None, OpenAIRefreshToken.next_sync_at <= now))  # noqa: E711
                    .order_by(OpenAIRefreshToken.next_sync_at.asc())
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
                    type=SUB2API_SYNC_STAGE,
                    input={"account_id": account_id, "refresh_token_id": token_id},
                    account_id=account_id or None,
                    proxy_id=proxy_id,
                    proxy_url=proxy_url,
                )
                enqueued += 1
            except Exception:
                logger.exception("failed to enqueue sub2api sync for token_id=%s", token_id)
        return enqueued

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = self.scan_once()
                if count:
                    logger.info("enqueued %s sub2api sync job(s)", count)
            except Exception:
                logger.exception("sub2api sync scan failed")
            self._stop.wait(self.interval_seconds)

    @staticmethod
    def _has_active_job(s: Session, token_id: int) -> bool:
        rows = s.exec(
            sa_select(Job)
            .where(Job.type == SUB2API_SYNC_STAGE)
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
        ).scalars()
        for job in rows:
            payload = json_loads(job.input_json, fallback={}) or {}
            if isinstance(payload, dict) and int(payload.get("refresh_token_id") or 0) == token_id:
                return True
        return False


class FailedPipelineRetryScheduler:
    """Periodically retry failed pipeline current-stage jobs only.

    The existing manual retry implementation resumes from the failed stage by
    composing input from the last succeeded previous stage, so earlier stages
    are not rerun. We track retry counts in Pipeline.result_json to avoid DB
    migrations and cap attempts per pipeline/stage at 3 by default.
    """

    def __init__(self, *, interval_seconds: int = SCAN_INTERVAL_SECONDS, max_attempts: int = FAILED_RETRY_MAX_ATTEMPTS) -> None:
        self.interval_seconds = max(1, int(interval_seconds))
        self.max_attempts = max(0, int(max_attempts))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="cqr-failed-pipeline-retry-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("failed pipeline retry scheduler started max_attempts=%s", self.max_attempts)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def scan_once(self) -> int:
        if self.max_attempts <= 0:
            return 0
        candidates: list[tuple[int, int, str, int]] = []
        now = utcnow()
        with Session(engine) as s:
            pipelines = list(
                s.exec(
                    sa_select(Pipeline)
                    .where(Pipeline.status.in_([JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED]))
                    .where(Pipeline.cancel_requested == False)  # noqa: E712
                    .order_by(Pipeline.updated_at.asc())
                    .limit(FAILED_RETRY_LIMIT_PER_SCAN)
                ).scalars()
            )
            for pipeline in pipelines:
                stage = str(pipeline.current_stage or "").strip()
                if not stage:
                    continue
                stages = json_loads(pipeline.stages_json, fallback=[]) or []
                # Only auto-retry the email account production chain.  Do not
                # resurrect historical SSO/manual/debug pipelines.
                if not {"register", "workspace_join", "chatgpt_session", "sub2api_sync"}.issubset(set(stages)):
                    continue
                if self._has_active_pipeline_job(s, int(pipeline.id or 0)):
                    continue
                latest = s.exec(
                    sa_select(Job)
                    .where(Job.pipeline_id == pipeline.id)
                    .where(Job.type == stage)
                    .where(Job.status.in_([JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED]))
                    .order_by(Job.id.desc())
                    .limit(1)
                ).scalars().first()
                if latest is None:
                    continue
                result = json_loads(pipeline.result_json, fallback={}) or {}
                meta = result.get(FAILED_RETRY_META_KEY) if isinstance(result.get(FAILED_RETRY_META_KEY), dict) else {}
                key = f"{stage}"
                attempts_by_stage = meta.get("attempts_by_stage") if isinstance(meta.get("attempts_by_stage"), dict) else {}
                attempts = int(attempts_by_stage.get(key) or 0)
                if attempts >= self.max_attempts:
                    continue
                # Reserve this retry before enqueueing so concurrent scans do
                # not double enqueue the same failed job.
                attempts += 1
                attempts_by_stage[key] = attempts
                meta.update({
                    "attempts_by_stage": attempts_by_stage,
                    "last_stage": stage,
                    "last_retried_job_id": int(latest.id or 0),
                    "last_retry_at": now.isoformat(),
                    "max_attempts": self.max_attempts,
                })
                result[FAILED_RETRY_META_KEY] = meta
                pipeline.result_json = json_dumps(result)
                pipeline.updated_at = now
                s.add(pipeline)
                s.add(JobEvent(
                    job_id=0,
                    pipeline_id=int(pipeline.id or 0),
                    level="info",
                    event_type="pipeline_auto_retry",
                    message=f"auto retry reserved stage={stage} attempt={attempts}/{self.max_attempts} job_id={int(latest.id or 0)}",
                ))
                candidates.append((int(pipeline.id or 0), int(latest.id or 0), stage, attempts))
            s.commit()

        retried = 0
        for pipeline_id, job_id, stage, attempts in candidates:
            try:
                result = self._retry_current_stage_job(job_id)
                retried += 1
                logger.info("auto retried pipeline=%s stage=%s attempt=%s job=%s new_job=%s", pipeline_id, stage, attempts, job_id, result.get("job_id"))
            except Exception as exc:
                logger.info("auto retry skipped pipeline=%s job=%s: %s", pipeline_id, job_id, exc)
        return retried

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = self.scan_once()
                if count:
                    logger.info("auto retried %s failed pipeline job(s)", count)
            except Exception:
                logger.exception("failed pipeline retry scan failed")
            self._stop.wait(self.interval_seconds)

    @staticmethod
    def _retry_current_stage_job(job_id: int) -> dict[str, int | str]:
        """Retry exactly the failed current stage of a pipeline.

        Unlike the manual API helper, this also supports register as the first
        stage and pipelines that do not contain a register stage. Previous
        successful stages are reused only as input context; they are not rerun.
        """
        from backend.core.pipeline import _compose_stage_input  # internal orchestrator helper

        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job is None:
                raise RuntimeError(f"job {job_id} not found")
            if job.pipeline_id is None:
                raise RuntimeError("standalone jobs cannot be retried by pipeline scheduler")
            if job.status not in {JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED}:
                raise RuntimeError(f"job status is not retryable: {job.status}")
            pipeline = s.get(Pipeline, job.pipeline_id)
            if pipeline is None:
                raise RuntimeError(f"pipeline {job.pipeline_id} not found")
            if pipeline.status not in {JOB_STATUS_FAILED, JOB_STATUS_INTERRUPTED}:
                raise RuntimeError(f"pipeline status is not retryable: {pipeline.status}")
            stage = str(job.type or "")
            if stage != str(pipeline.current_stage or ""):
                raise RuntimeError(f"job stage {stage!r} != pipeline current_stage {pipeline.current_stage!r}")
            latest = s.exec(
                sa_select(Job)
                .where(Job.pipeline_id == pipeline.id)
                .where(Job.type == stage)
                .order_by(Job.id.desc())
                .limit(1)
            ).scalars().first()
            if latest is None or int(latest.id or 0) != int(job.id or 0):
                raise RuntimeError("a newer job exists for this stage")
            if FailedPipelineRetryScheduler._has_active_pipeline_job(s, int(pipeline.id or 0)):
                raise RuntimeError("pipeline already has active job")

            stages = json_loads(pipeline.stages_json, fallback=[]) or []
            if stage not in stages:
                raise RuntimeError(f"stage {stage!r} not in pipeline stages")
            stage_idx = stages.index(stage)
            prior_result = {}
            if stage_idx > 0:
                prev_stage = stages[stage_idx - 1]
                prev_succeeded = s.exec(
                    sa_select(Job)
                    .where(Job.pipeline_id == pipeline.id)
                    .where(Job.type == prev_stage)
                    .where(Job.status == "succeeded")
                    .order_by(Job.id.desc())
                    .limit(1)
                ).scalars().first()
                if prev_succeeded is None:
                    raise RuntimeError(f"previous stage {prev_stage!r} has no succeeded job")
                prior_result = json_loads(prev_succeeded.result_json, fallback={}) or {}

            stage_inputs = json_loads(pipeline.stage_inputs_json, fallback={}) or {}
            request_payload = json_loads(pipeline.input_json, fallback={}) or {}
            input_payload = _compose_stage_input(stage_inputs, stage, prior_result=prior_result, request_payload=request_payload)

            account_id = pipeline.account_id
            payment_link_id = pipeline.payment_link_id
            proxy_id = pipeline.proxy_id
            proxy_url = pipeline.proxy_url or ""
            if isinstance(input_payload, dict):
                if input_payload.get("account_id") not in (None, ""):
                    try:
                        account_id = int(input_payload.get("account_id") or 0) or account_id
                    except Exception:
                        pass
                if input_payload.get("payment_link_id") not in (None, ""):
                    try:
                        payment_link_id = int(input_payload.get("payment_link_id") or 0) or payment_link_id
                    except Exception:
                        pass
                if input_payload.get("proxy_id") not in (None, ""):
                    try:
                        proxy_id = int(input_payload.get("proxy_id") or 0) or proxy_id
                    except Exception:
                        pass
                if input_payload.get("proxy_url") not in (None, ""):
                    proxy_url = str(input_payload.get("proxy_url") or proxy_url or "")

            pipeline_id = int(pipeline.id or 0)
            pipeline.status = JOB_STATUS_QUEUED
            pipeline.current_stage = stage
            pipeline.completed_steps = min(int(pipeline.completed_steps or 0), stage_idx)
            pipeline.error = ""
            pipeline.finished_at = None
            pipeline.cancel_requested = False
            pipeline.updated_at = utcnow()
            s.add(pipeline)
            s.add(JobEvent(
                job_id=0,
                pipeline_id=pipeline_id,
                level="info",
                event_type="pipeline_auto_retry",
                message=f"auto retry enqueue stage={stage} retried_job_id={job_id}",
            ))
            s.commit()

        new_job_id = enqueue_job(
            type=stage,
            input=input_payload if isinstance(input_payload, dict) else {},
            pipeline_id=pipeline_id,
            account_id=account_id,
            payment_link_id=payment_link_id,
            proxy_id=proxy_id,
            proxy_url=proxy_url,
        )
        return {"job_id": new_job_id, "retried_job_id": job_id, "pipeline_id": pipeline_id, "stage": stage}

    @staticmethod
    def _has_active_pipeline_job(s: Session, pipeline_id: int) -> bool:
        active = s.exec(
            sa_select(Job.id)
            .where(Job.pipeline_id == pipeline_id)
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
            .limit(1)
        ).first()
        return active is not None


class CompositeScheduler:
    def __init__(self) -> None:
        self.sub2api = Sub2ApiSyncScheduler()
        self.failed_retry = FailedPipelineRetryScheduler()

    def start(self) -> None:
        self.sub2api.start()
        self.failed_retry.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.failed_retry.stop(timeout=timeout)
        self.sub2api.stop(timeout=timeout)

    def scan_once(self) -> dict[str, int]:
        return {
            "sub2api": self.sub2api.scan_once(),
            "failed_retry": self.failed_retry.scan_once(),
        }


_scheduler = CompositeScheduler()


def get_scheduler() -> CompositeScheduler:
    return _scheduler


Sub2ApiAccountSyncScheduler = Sub2ApiSyncScheduler
