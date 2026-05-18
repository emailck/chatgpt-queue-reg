"""ChatGPT account list + per-row helper actions."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import account_to_dict, payment_link_to_dict
from backend.core.constants import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
)
from backend.core.db import engine, session_scope
from backend.core.browser_debug import open_debug_session
from backend.core.queue import enqueue_job
from backend.models.account import ChatGPTAccount
from backend.models.codex_token import CodexToken
from backend.models.job import Job
from backend.models.payment import PaymentLink
from backend.models.pipeline import Pipeline

INVALID_SUB2API_STATUSES = {"dead", "disabled", "banned", "invalid", "expired"}

router = APIRouter()


class RetryPaymentLinkRequest(BaseModel):
    plan: str | None = None  # "team" | "plus"
    workspace_name: str | None = None
    price_interval: str | None = None
    seat_quantity: int | None = None
    country: str | None = None
    currency: str | None = None


class ReadEmailRequest(BaseModel):
    timeout_seconds: int = 120
    keyword: str = ""
    code_regex: str | None = None


class DebugBrowserRequest(BaseModel):
    target_url: str | None = None
    browser_type: str = "camoufox"
    inject_cookies: bool = True
    inject_local_storage: bool = True
    inject_fingerprint: bool = True
    record_har: bool = True
    omit_har_content: bool = False


@router.get("/api/accounts", tags=["accounts"])
def list_accounts(
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(ChatGPTAccount)
        if status:
            stmt = stmt.where(ChatGPTAccount.status == status)
        stmt = stmt.order_by(ChatGPTAccount.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
        link_ids = {r.last_payment_link_id for r in rows if r.last_payment_link_id}
        urls: dict[int, str] = {}
        if link_ids:
            for row in s.exec(
                sa_select(PaymentLink).where(PaymentLink.id.in_(list(link_ids)))
            ).scalars():
                urls[int(row.id or 0)] = row.checkout_url
        codex_tokens = _codex_tokens_by_account(s, [int(row.id or 0) for row in rows])
    return [
        _with_codex_token(
            account_to_dict(
                row,
                last_payment_link_url=urls.get(row.last_payment_link_id or 0, ""),
            ),
            codex_tokens.get(int(row.id or 0)),
        )
        for row in rows
    ]


@router.get("/api/accounts/subscriptions", tags=["accounts"])
def list_subscription_accounts(limit: int = Query(300, ge=1, le=1000)):
    with Session(engine) as s:
        pipelines = list(
            s.exec(
                sa_select(Pipeline)
                .where(Pipeline.preset.in_(["account_paid", "account_paid_with_codex_rt", "link_only"]))
                .order_by(Pipeline.id.desc())
                .limit(limit)
            ).scalars()
        )
        account_ids = [int(p.account_id or 0) for p in pipelines if p.account_id]
        link_ids = {int(p.payment_link_id or 0) for p in pipelines if p.payment_link_id}
        accounts = {
            int(row.id or 0): row
            for row in s.exec(sa_select(ChatGPTAccount).where(ChatGPTAccount.id.in_(account_ids))).scalars()
        } if account_ids else {}
        links = {
            int(row.id or 0): row
            for row in s.exec(sa_select(PaymentLink).where(PaymentLink.id.in_(list(link_ids)))).scalars()
        } if link_ids else {}
        rt_jobs = _latest_refresh_token_jobs(s, account_ids)
        codex_tokens = _codex_tokens_by_account(s, account_ids)
    return [
        _subscription_account_to_dict(
            pipeline,
            accounts.get(int(pipeline.account_id or 0)),
            links.get(int(pipeline.payment_link_id or 0)),
            rt_jobs.get(int(pipeline.account_id or 0)),
            codex_tokens.get(int(pipeline.account_id or 0)),
        )
        for pipeline in pipelines
    ]


@router.get("/api/accounts/{account_id}", tags=["accounts"])
def get_account(account_id: int):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        link_url = ""
        if row.last_payment_link_id:
            link = s.get(PaymentLink, row.last_payment_link_id)
            if link:
                link_url = link.checkout_url
        codex_token = _codex_tokens_by_account(s, [int(row.id or 0)]).get(int(row.id or 0))
    return _with_codex_token(account_to_dict(row, last_payment_link_url=link_url), codex_token)


@router.post("/api/accounts/{account_id}/refresh-token", tags=["accounts"])
def fetch_refresh_token(account_id: int):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        codex_token = _codex_tokens_by_account(s, [account_id]).get(account_id)
        if _codex_token_is_usable(codex_token):
            return {"job_id": None, "already_has_refresh_token": True, "already_running": False}
        running = s.exec(
            sa_select(Job)
            .where(Job.account_id == account_id)
            .where(Job.type == "oauth_codex")
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
        ).scalars().first()
        if running is not None:
            return {"job_id": int(running.id or 0), "already_has_refresh_token": False, "already_running": True}
    job_id = enqueue_job(
        type="oauth_codex",
        input={"account_id": account_id},
        account_id=account_id,
        proxy_id=row.proxy_id,
        proxy_url=row.proxy_url or "",
    )
    return {"job_id": job_id, "already_has_refresh_token": False, "already_running": False}


@router.post("/api/accounts/{account_id}/payment-link/retry", tags=["accounts"])
def retry_payment_link(account_id: int, body: RetryPaymentLinkRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    plan = (body.plan or "team").lower()
    default_country = "ID" if plan == "plus" else "US"
    job_id = enqueue_job(
        type="payment_link",
        input={
            "account_id": account_id,
            "plan": plan,
            "workspace_name": body.workspace_name or "MyWorkspace",
            "price_interval": body.price_interval or "month",
            "seat_quantity": int(body.seat_quantity or 2),
            "country": body.country or default_country,
            "currency": body.currency,
        },
        account_id=account_id,
        proxy_id=row.proxy_id,
        proxy_url=row.proxy_url or "",
    )
    return {"job_id": job_id}


@router.post("/api/accounts/{account_id}/read-email", tags=["accounts"])
def read_email(account_id: int, body: ReadEmailRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    from backend.api.emails import read_email as read_email_direct, ReadRequest

    return read_email_direct(ReadRequest(
        email=row.email,
        timeout_seconds=body.timeout_seconds,
        keyword=body.keyword,
        code_regex=body.code_regex,
    ))


@router.post("/api/accounts/{account_id}/debug-browser", tags=["accounts"])
def debug_browser(account_id: int, body: DebugBrowserRequest):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
    return open_debug_session(
        target_url=body.target_url or "https://chatgpt.com/",
        account_id=account_id,
        proxy_url=row.proxy_url or "",
        browser_type=body.browser_type,
        inject_cookies=body.inject_cookies,
        inject_local_storage=body.inject_local_storage,
        inject_fingerprint=body.inject_fingerprint,
        record_har=body.record_har,
        omit_har_content=body.omit_har_content,
    )


def _latest_refresh_token_jobs(s: Session, account_ids: list[int]) -> dict[int, Job]:
    if not account_ids:
        return {}
    jobs = list(
        s.exec(
            sa_select(Job)
            .where(Job.account_id.in_(account_ids))
            .where(Job.type == "oauth_codex")
            .order_by(Job.id.desc())
        ).scalars()
    )
    latest: dict[int, Job] = {}
    for job in jobs:
        aid = int(job.account_id or 0)
        if aid and aid not in latest:
            latest[aid] = job
    return latest


def _codex_tokens_by_account(s: Session, account_ids: list[int]) -> dict[int, CodexToken]:
    if not account_ids:
        return {}
    rows = list(
        s.exec(
            sa_select(CodexToken)
            .where(CodexToken.account_id.in_(account_ids))
            .order_by(CodexToken.id.desc())
        ).scalars()
    )
    latest: dict[int, CodexToken] = {}
    for row in rows:
        aid = int(row.account_id or 0)
        if aid and aid not in latest:
            latest[aid] = row
    return latest


def _codex_token_is_usable(codex_token: CodexToken | None) -> bool:
    if codex_token is None or not codex_token.refresh_token:
        return False
    if not codex_token.alive:
        return False
    return str(codex_token.sub2api_status or "").strip().lower() not in INVALID_SUB2API_STATUSES


def _with_codex_token(item: dict[str, Any], codex_token: CodexToken | None) -> dict[str, Any]:
    item.update({
        "has_refresh_token": _codex_token_is_usable(codex_token),
        "codex_token_id": codex_token.id if codex_token else None,
        "codex_token_alive": bool(codex_token.alive) if codex_token else False,
        "codex_token_has_refresh_token": bool(codex_token.refresh_token) if codex_token else False,
        "codex_token_last_error": codex_token.last_error if codex_token else "",
        "sub2api_external_id": codex_token.sub2api_external_id if codex_token else "",
        "sub2api_status": codex_token.sub2api_status if codex_token else "",
        "sub2api_uploaded_at": codex_token.uploaded_at.isoformat() if codex_token and codex_token.uploaded_at else None,
        "sub2api_status_checked_at": codex_token.status_checked_at.isoformat() if codex_token and codex_token.status_checked_at else None,
    })
    return item


def _subscription_account_to_dict(
    pipeline: Pipeline,
    account: ChatGPTAccount | None,
    payment_link: PaymentLink | None,
    refresh_job: Job | None,
    codex_token: CodexToken | None,
) -> dict:
    item = account_to_dict(account, last_payment_link_url=payment_link.checkout_url if payment_link else "") if account else {
        "id": None,
        "email": "",
        "password": "",
        "status": "pending_register",
        "account_id": "",
        "workspace_id": "",
        "proxy_id": pipeline.proxy_id,
        "proxy_url": pipeline.proxy_url,
        "last_error": pipeline.error,
        "last_payment_link_id": pipeline.payment_link_id,
        "last_payment_link_url": payment_link.checkout_url if payment_link else "",
        "user_agent": "",
        "has_access_token": False,
        "has_refresh_token": False,
        "has_session_token": False,
        "created_at": None,
        "registered_at": None,
        "updated_at": None,
    }
    item = _with_codex_token(item, codex_token)
    item.update({
        "subscription_pipeline_id": pipeline.id,
        "subscription_status": pipeline.status,
        "subscription_current_stage": pipeline.current_stage,
        "subscription_completed_steps": pipeline.completed_steps,
        "subscription_total_steps": pipeline.total_steps,
        "payment_link_status": payment_link.status if payment_link else "",
        "payment_link_error": payment_link.error if payment_link else "",
        "refresh_token_job_id": refresh_job.id if refresh_job else None,
        "refresh_token_job_status": refresh_job.status if refresh_job else "",
        "refresh_token_job_error": refresh_job.error if refresh_job else "",
    })
    return item


class IdsRequest(BaseModel):
    ids: list[int]


@router.delete("/api/accounts/{account_id}", tags=["accounts"])
def delete_account(account_id: int):
    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/accounts/batch-delete", tags=["accounts"])
def batch_delete_accounts(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for aid in ids:
            row = s.get(ChatGPTAccount, aid)
            if row is None:
                not_found.append(aid)
                continue
            s.delete(row)
            deleted.append(aid)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "not_found": not_found,
        "total_requested": len(ids),
    }
