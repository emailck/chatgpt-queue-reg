"""Pipeline + Job APIs.

Pipelines own the user-facing batch ("create N ChatGPT accounts and produce
their long-links"); Jobs are the per-step rows the worker pool actually
consumes.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import (
    account_to_dict,
    event_to_dict,
    job_to_dict,
    payment_link_to_dict,
    pipeline_to_dict,
)
from backend.core.constants import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_TERMINAL_STATUSES,
    PIPELINE_TYPE_CHATGPT_ACCOUNT,
)
from backend.core.db import engine, session_scope
from backend.core.job_context import subscribe_job_events
from backend.core.json_utils import json_loads
from backend.core.pipeline import (
    cancel_pipeline,
    create_chatgpt_account_pipeline,
    create_chatgpt_register_only_pipeline,
)
from backend.core.queue import enqueue_job, get_pool
from backend.core.time_utils import utcnow
from backend.models.account import ChatGPTAccount
from backend.models.job import Job, JobEvent
from backend.models.payment import PaymentLink
from backend.models.pipeline import Pipeline

router = APIRouter()


# ---- request bodies ---------------------------------------------------------


class PaymentLinkOptions(BaseModel):
    plan: str = "team"  # "team" | "plus"
    workspace_name: str = "MyWorkspace"
    price_interval: str = "month"
    seat_quantity: int = Field(default=2, ge=1, le=99)
    country: str | None = None  # default chosen by plan
    currency: str | None = None


class ChatGPTPipelineRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=200)
    concurrency: Optional[int] = Field(default=None, ge=1, le=64)
    email: Optional[str] = None
    password: Optional[str] = None
    proxy_id: Optional[int] = None
    proxy_url: Optional[str] = None
    payment_link_options: PaymentLinkOptions = Field(default_factory=PaymentLinkOptions)
    extra_config: dict[str, Any] = Field(default_factory=dict)


class ChatGPTRegisterOnlyRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=200)
    concurrency: Optional[int] = Field(default=None, ge=1, le=64)
    email: Optional[str] = None
    password: Optional[str] = None
    proxy_id: Optional[int] = None
    proxy_url: Optional[str] = None
    extra_config: dict[str, Any] = Field(default_factory=dict)


class JobEnqueueRequest(BaseModel):
    type: str
    input: dict[str, Any] = Field(default_factory=dict)
    pipeline_id: Optional[int] = None
    account_id: Optional[int] = None
    payment_link_id: Optional[int] = None
    proxy_id: Optional[int] = None
    proxy_url: Optional[str] = None


class IdsRequest(BaseModel):
    ids: list[int]


# ---- pipeline endpoints -----------------------------------------------------


@router.post("/api/pipelines/chatgpt-account", tags=["pipelines"])
def create_chatgpt_pipeline(body: ChatGPTPipelineRequest):
    if body.concurrency is not None:
        get_pool().set_concurrency(int(body.concurrency))
    pipeline_ids: list[int] = []
    for _ in range(body.count):
        payload = {
            "email": body.email,
            "password": body.password,
            "proxy_url": body.proxy_url,
            "payment_link_options": body.payment_link_options.model_dump(),
            "extra_config": body.extra_config or {},
        }
        pipeline_ids.append(
            create_chatgpt_account_pipeline(
                input=payload,
                proxy_id=body.proxy_id,
                proxy_url=body.proxy_url or "",
            )
        )
    return {"pipeline_ids": pipeline_ids, "concurrency": get_pool().concurrency}


@router.post("/api/pipelines/chatgpt-register-only", tags=["pipelines"])
def create_register_only_pipeline(body: ChatGPTRegisterOnlyRequest):
    """Stop after access_token is obtained — no payment-link step."""
    if body.concurrency is not None:
        get_pool().set_concurrency(int(body.concurrency))
    pipeline_ids: list[int] = []
    for _ in range(body.count):
        payload = {
            "email": body.email,
            "password": body.password,
            "proxy_url": body.proxy_url,
            "extra_config": body.extra_config or {},
        }
        pipeline_ids.append(
            create_chatgpt_register_only_pipeline(
                input=payload,
                proxy_id=body.proxy_id,
                proxy_url=body.proxy_url or "",
            )
        )
    return {"pipeline_ids": pipeline_ids, "concurrency": get_pool().concurrency}


@router.get("/api/pipelines", tags=["pipelines"])
def list_pipelines(
    status: Optional[str] = None,
    account_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(Pipeline)
        if status:
            stmt = stmt.where(Pipeline.status == status)
        if account_id is not None:
            stmt = stmt.where(Pipeline.account_id == account_id)
        stmt = stmt.order_by(Pipeline.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
    return [pipeline_to_dict(p) for p in rows]


@router.get("/api/pipelines/{pipeline_id}", tags=["pipelines"])
def get_pipeline(pipeline_id: int):
    with Session(engine) as s:
        pipeline = s.get(Pipeline, pipeline_id)
        if pipeline is None:
            raise HTTPException(status_code=404, detail="pipeline not found")
        jobs = list(
            s.exec(
                sa_select(Job).where(Job.pipeline_id == pipeline_id).order_by(Job.id.asc())
            ).scalars()
        )
    return {
        "pipeline": pipeline_to_dict(pipeline),
        "jobs": [job_to_dict(j) for j in jobs],
    }


@router.post("/api/pipelines/{pipeline_id}/cancel", tags=["pipelines"])
def cancel_pipeline_endpoint(pipeline_id: int):
    if not cancel_pipeline(pipeline_id):
        raise HTTPException(status_code=409, detail="pipeline not cancellable")
    return {"ok": True}


@router.delete("/api/pipelines/{pipeline_id}", tags=["pipelines"])
def delete_pipeline(pipeline_id: int):
    with session_scope() as s:
        pipeline = s.get(Pipeline, pipeline_id)
        if pipeline is None:
            raise HTTPException(status_code=404, detail="pipeline not found")
        if pipeline.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
            raise HTTPException(status_code=409, detail="cannot delete an active pipeline")
        # Drop child jobs + events first.
        children = list(s.exec(sa_select(Job).where(Job.pipeline_id == pipeline_id)).scalars())
        for job in children:
            s.delete(job)
        s.delete(pipeline)
    return {"ok": True}


@router.post("/api/pipelines/batch-delete", tags=["pipelines"])
def batch_delete_pipelines(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    skipped: list[dict[str, Any]] = []
    not_found: list[int] = []
    with session_scope() as s:
        for pid in ids:
            pipeline = s.get(Pipeline, pid)
            if pipeline is None:
                not_found.append(pid)
                continue
            if pipeline.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
                skipped.append({"id": pid, "reason": f"status={pipeline.status}"})
                continue
            for job in list(s.exec(sa_select(Job).where(Job.pipeline_id == pid)).scalars()):
                s.delete(job)
            s.delete(pipeline)
            deleted.append(pid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "skipped": skipped,
        "not_found": not_found,
        "total_requested": len(ids),
    }


# ---- job endpoints -----------------------------------------------------------


@router.post("/api/jobs", tags=["jobs"])
def enqueue_job_endpoint(body: JobEnqueueRequest):
    job_id = enqueue_job(
        type=body.type,
        input=body.input or {},
        pipeline_id=body.pipeline_id,
        account_id=body.account_id,
        payment_link_id=body.payment_link_id,
        proxy_id=body.proxy_id,
        proxy_url=body.proxy_url or "",
    )
    return {"job_id": job_id}


@router.get("/api/jobs", tags=["jobs"])
def list_jobs(
    type: Optional[str] = None,
    status: Optional[str] = None,
    account_id: Optional[int] = None,
    pipeline_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(Job)
        if type:
            stmt = stmt.where(Job.type == type)
        if status:
            stmt = stmt.where(Job.status == status)
        if account_id is not None:
            stmt = stmt.where(Job.account_id == account_id)
        if pipeline_id is not None:
            stmt = stmt.where(Job.pipeline_id == pipeline_id)
        stmt = stmt.order_by(Job.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
    return [job_to_dict(j) for j in rows]


@router.get("/api/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: int):
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
    return job_to_dict(job)


@router.post("/api/jobs/{job_id}/cancel", tags=["jobs"])
def cancel_job_endpoint(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in JOB_TERMINAL_STATUSES:
            return {"ok": False, "detail": f"job already {job.status}"}
        job.cancel_requested = True
        job.updated_at = utcnow()
        s.add(job)
    return {"ok": True}


@router.delete("/api/jobs/{job_id}", tags=["jobs"])
def delete_job(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
            raise HTTPException(status_code=409, detail="cannot delete an active job")
        s.delete(job)
    return {"ok": True}


@router.post("/api/jobs/batch-delete", tags=["jobs"])
def batch_delete_jobs(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    skipped: list[dict[str, Any]] = []
    not_found: list[int] = []
    with session_scope() as s:
        for jid in ids:
            job = s.get(Job, jid)
            if job is None:
                not_found.append(jid)
                continue
            if job.status in {JOB_STATUS_QUEUED, JOB_STATUS_RUNNING}:
                skipped.append({"id": jid, "reason": f"status={job.status}"})
                continue
            s.delete(job)
            deleted.append(jid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "skipped": skipped,
        "not_found": not_found,
        "total_requested": len(ids),
    }


@router.get("/api/jobs/{job_id}/events", tags=["jobs"])
def list_job_events(job_id: int, since_id: int = 0, limit: int = Query(500, ge=1, le=2000)):
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .where(JobEvent.id > since_id)
                .order_by(JobEvent.id.asc())
                .limit(limit)
            ).scalars()
        )
    return [event_to_dict(r) for r in rows]


@router.get("/api/jobs/{job_id}/events/stream", tags=["jobs"])
async def stream_job_events(job_id: int, since_id: int = 0):
    """SSE stream: replay events past `since_id`, then live-tail."""
    from fastapi.responses import StreamingResponse

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _on_event(eid: int, data: dict[str, Any]) -> None:
        if eid != job_id:
            return
        loop.call_soon_threadsafe(queue.put_nowait, data)

    unsubscribe = subscribe_job_events(_on_event)

    async def _gen():
        try:
            with Session(engine) as s:
                rows = list(
                    s.exec(
                        sa_select(JobEvent)
                        .where(JobEvent.job_id == job_id)
                        .where(JobEvent.id > since_id)
                        .order_by(JobEvent.id.asc())
                    ).scalars()
                )
            for row in rows:
                yield f"data: {_sse_payload(event_to_dict(row))}\n\n"

            while True:
                data = await queue.get()
                yield f"data: {_sse_payload(data)}\n\n"
                if data.get("kind") == "status" and data.get("status") in JOB_TERMINAL_STATUSES:
                    break
        finally:
            unsubscribe()

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _sse_payload(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, default=str)


@router.get("/api/queue/stats", tags=["queue"])
def queue_stats():
    with Session(engine) as s:
        rows = list(s.exec(sa_select(Job.status)).all())
    counts: dict[str, int] = {}
    for row in rows:
        # SQLAlchemy 2.x returns Row objects for column-only selects; flatten.
        if isinstance(row, tuple):
            value = row[0] if row else ""
        else:
            value = row
        key = str(value or "")
        counts[key] = counts.get(key, 0) + 1
    return {
        "pool_concurrency": get_pool().concurrency,
        "counts": counts,
    }
