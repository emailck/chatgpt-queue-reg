"""ChatGPT account list + per-row helper actions."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import account_to_dict
from backend.core.constants import (
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    PAYMENT_LINK_STATUS_PAID_UNKNOWN,
)
from backend.core.db import engine, session_scope
from backend.core.browser_debug import open_debug_session
from backend.core.queue import enqueue_job
from backend.core.time_utils import utcnow
from backend.integrations.sub2api import Sub2ApiNotConfigured, get_sub2api_client
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.models.job import Job
from backend.models.payment import PaymentLink
from backend.models.pipeline import Pipeline
from backend.models.sub2api_binding import Sub2ApiAccountBinding

INVALID_SUB2API_STATUSES = {"disabled", "error", "banned"}

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


class Sub2ApiExportRequest(BaseModel):
    ids: list[int]
    mark_sold: bool = True
    include_already_sold: bool = False
    sold_only: bool = False


class IdsRequest(BaseModel):
    ids: list[int]


@router.get("/api/accounts", tags=["accounts"])
def list_accounts(
    status: Optional[str] = None,
    paid_only: bool = False,
    sold: Optional[bool] = None,
    sub2api_status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
):
    with Session(engine) as s:
        stmt = sa_select(ChatGPTAccount)
        if paid_only:
            stmt = stmt.join(PaymentLink, PaymentLink.id == ChatGPTAccount.last_payment_link_id).where(
                PaymentLink.status == PAYMENT_LINK_STATUS_PAID_UNKNOWN
            )
        if status:
            stmt = stmt.where(ChatGPTAccount.status == status)
        if sold is not None:
            stmt = stmt.where(ChatGPTAccount.sold == sold)
        stmt = stmt.order_by(ChatGPTAccount.id.desc()).limit(limit)
        rows = list(s.exec(stmt).scalars().all())
        link_ids = {r.last_payment_link_id for r in rows if r.last_payment_link_id}
        urls: dict[int, str] = {}
        payment_statuses: dict[int, str] = {}
        if link_ids:
            for row in s.exec(
                sa_select(PaymentLink).where(PaymentLink.id.in_(list(link_ids)))
            ).scalars():
                urls[int(row.id or 0)] = row.checkout_url
                payment_statuses[int(row.id or 0)] = row.status
        account_ids = [int(row.id or 0) for row in rows]
        refresh_tokens = _refresh_tokens_by_account(s, account_ids)
        sub2api_bindings = _sub2api_bindings_by_account(s, account_ids)
        if sub2api_status:
            rows = [
                row for row in rows
                if _matches_sub2api_status_filter(
                    sub2api_bindings.get(int(row.id or 0)),
                    refresh_tokens.get(int(row.id or 0)),
                    sub2api_status,
                )
            ]
    return [
        _with_sub2api_binding(
            _with_refresh_token(
                {
                    **account_to_dict(
                        row,
                        last_payment_link_url=urls.get(row.last_payment_link_id or 0, ""),
                    ),
                    "last_payment_link_status": payment_statuses.get(row.last_payment_link_id or 0, ""),
                },
                refresh_tokens.get(int(row.id or 0)),
            ),
            sub2api_bindings.get(int(row.id or 0)),
        )
        for row in rows
    ]


@router.get("/api/accounts/subscriptions", tags=["accounts"])
def list_subscription_accounts(limit: int = Query(300, ge=1, le=1000)):
    with Session(engine) as s:
        pipelines = list(
            s.exec(
                sa_select(Pipeline)
                .where(Pipeline.preset.in_(["account_paid", "account_paid_with_sub2api", "account_paid_with_refresh_token", "link_only"]))
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
        refresh_tokens = _refresh_tokens_by_account(s, account_ids)
        sub2api_bindings = _sub2api_bindings_by_account(s, account_ids)
    return [
        _subscription_account_to_dict(
            pipeline,
            accounts.get(int(pipeline.account_id or 0)),
            links.get(int(pipeline.payment_link_id or 0)),
            rt_jobs.get(int(pipeline.account_id or 0)),
            refresh_tokens.get(int(pipeline.account_id or 0)),
            sub2api_bindings.get(int(pipeline.account_id or 0)),
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
        account_id_value = int(row.id or 0)
        refresh_token = _refresh_tokens_by_account(s, [account_id_value]).get(account_id_value)
        sub2api_binding = _sub2api_bindings_by_account(s, [account_id_value]).get(account_id_value)
    return _with_sub2api_binding(_with_refresh_token(account_to_dict(row, last_payment_link_url=link_url), refresh_token), sub2api_binding)


@router.post("/api/accounts/sub2api-status-refresh", tags=["accounts"])
def refresh_sub2api_account_statuses(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or [] if int(i or 0) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    client = get_sub2api_client()
    now = utcnow()
    results: list[dict[str, Any]] = []
    with Session(engine) as s:
        rows_by_id = {int(row.id or 0): row for row in s.exec(sa_select(ChatGPTAccount).where(ChatGPTAccount.id.in_(ids))).scalars()}
        missing = [aid for aid in ids if aid not in rows_by_id]
        if missing:
            raise HTTPException(status_code=404, detail={"missing": missing})
        bindings = _sub2api_bindings_by_account(s, ids)
        refresh_tokens = _refresh_tokens_by_account(s, ids)

    from backend.stages.sub2api_sync import parse_sub2api_status_response

    for aid in ids:
        binding = bindings.get(aid)
        refresh_token = refresh_tokens.get(aid)
        sub2api_account_id = str(binding.sub2api_account_id or "").strip() if binding else ""
        if not sub2api_account_id and refresh_token is not None:
            sub2api_account_id = str(refresh_token.sub2api_account_id or "").strip()
        if not sub2api_account_id:
            results.append({"account_id": aid, "ok": False, "error": "missing_sub2api_account_id"})
            continue
        try:
            status_resp = client.get_openai_account_status(sub2api_account_id)
            parsed = parse_sub2api_status_response(status_resp)
        except Sub2ApiNotConfigured:
            raise HTTPException(status_code=409, detail="sub2api 未配置")
        except Exception as exc:
            _record_sub2api_status_refresh_failure(aid, str(exc), now=now)
            results.append({"account_id": aid, "sub2api_account_id": sub2api_account_id, "ok": False, "error": str(exc)})
            continue
        _record_sub2api_status_refresh(aid, sub2api_account_id, parsed, now=now, refresh_token=refresh_token)
        results.append({
            "account_id": aid,
            "sub2api_account_id": sub2api_account_id,
            "ok": True,
            "status": parsed.get("status", ""),
            "schedulable": parsed.get("schedulable", True),
            "relogin_required": parsed.get("relogin_required", False),
            "error": parsed.get("error", ""),
        })
    return {
        "total": len(ids),
        "refreshed": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }


@router.post("/api/accounts/{account_id}/sub2api-status-refresh", tags=["accounts"])
def refresh_sub2api_account_status(account_id: int):
    result = refresh_sub2api_account_statuses(IdsRequest(ids=[account_id]))
    item = (result.get("results") or [{}])[0]
    return item


@router.post("/api/accounts/sub2api-export", tags=["accounts"])
def export_sub2api_accounts(body: Sub2ApiExportRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or [] if int(i or 0) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    account_docs: list[dict[str, Any]] = []
    sub2api_account_ids_by_account: dict[int, str] = {}
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(ChatGPTAccount)
                .where(ChatGPTAccount.id.in_(ids))
                .order_by(ChatGPTAccount.id.asc())
            ).scalars()
        )
        rows_by_id = {int(row.id or 0): row for row in rows}
        missing = [aid for aid in ids if aid not in rows_by_id]
        if missing:
            raise HTTPException(status_code=404, detail={"missing": missing})
        paid_account_ids = _paid_account_ids(s, ids)
        not_plus = [aid for aid in ids if aid not in paid_account_ids]
        if not_plus:
            raise HTTPException(status_code=409, detail={"not_plus": not_plus})
        already_sold = [aid for aid in ids if rows_by_id[aid].sold]
        if already_sold and not body.include_already_sold:
            raise HTTPException(status_code=409, detail={"already_sold": already_sold})
        if body.sold_only:
            not_sold = [aid for aid in ids if not rows_by_id[aid].sold]
            if not_sold:
                raise HTTPException(status_code=409, detail={"not_sold": not_sold})
        refresh_tokens = _refresh_tokens_by_account(s, ids)
        sub2api_bindings = _sub2api_bindings_by_account(s, ids)
        from backend.stages.sub2api_sync import _build_openai_import_payload, _exported_at, _first_payload_account, _snapshot_account, _snapshot_refresh_token

        for aid in ids:
            row = rows_by_id[aid]
            try:
                payload, _, _ = _build_openai_import_payload(
                    _snapshot_account(row),
                    _snapshot_refresh_token(refresh_tokens.get(aid)),
                )
                account_doc = _first_payload_account(payload)
                _strip_export_proxy(account_doc)
                account_docs.append(account_doc)
            except Exception as exc:
                raise HTTPException(status_code=409, detail={"account_id": aid, "error": str(exc)}) from exc
            binding = sub2api_bindings.get(aid)
            if binding and str(binding.sub2api_account_id or "").strip():
                sub2api_account_ids_by_account[aid] = str(binding.sub2api_account_id).strip()

    payload = {
        "exported_at": _exported_at(),
        "proxies": [],
        "accounts": account_docs,
    }
    body_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    moved_to_sold_group = False
    sold_group_id = 0
    if body.mark_sold:
        client = get_sub2api_client()
        sold_group_id = int(client.sold_group_id or 0)
        if not sold_group_id:
            raise HTTPException(status_code=409, detail="请先配置 sub2api_sold_group_id")
        missing_sub2api_ids = [aid for aid in ids if aid not in sub2api_account_ids_by_account]
        if missing_sub2api_ids:
            raise HTTPException(status_code=409, detail={"missing_sub2api_account_id": missing_sub2api_ids})
        try:
            client.move_openai_accounts_to_group(list(sub2api_account_ids_by_account.values()), sold_group_id)
            moved_to_sold_group = True
        except Sub2ApiNotConfigured:
            raise HTTPException(status_code=409, detail="sub2api 未配置，无法迁移已售出分组")
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"迁移 sub2api 已售出分组失败: {exc}") from exc
        now = utcnow()
        with session_scope() as s:
            for aid in ids:
                row = s.get(ChatGPTAccount, aid)
                if row is None:
                    raise HTTPException(status_code=404, detail=f"account {aid} not found")
                row.sold = True
                row.sold_at = now
                row.updated_at = now
                s.add(row)

    response = StreamingResponse(iter([body_text]), media_type="application/json")
    response.headers["Content-Disposition"] = 'attachment; filename="plus-sub2api-accounts.json"'
    response.headers["X-Exported-Count"] = str(len(account_docs))
    response.headers["X-Marked-Sold"] = "true" if body.mark_sold else "false"
    response.headers["X-Sub2Api-Sold-Group-Id"] = str(sold_group_id or "")
    response.headers["X-Sub2Api-Moved-To-Sold-Group"] = "true" if moved_to_sold_group else "false"
    return response


@router.post("/api/accounts/{account_id}/refresh-token", tags=["accounts"])
def fetch_refresh_token(account_id: int):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        refresh_token = _refresh_tokens_by_account(s, [account_id]).get(account_id)
        if _refresh_token_is_usable(refresh_token):
            return {"job_id": None, "already_has_refresh_token": True, "already_running": False}
        running = s.exec(
            sa_select(Job)
            .where(Job.account_id == account_id)
            .where(Job.type == "openai_oauth")
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
        ).scalars().first()
        if running is not None:
            return {"job_id": int(running.id or 0), "already_has_refresh_token": False, "already_running": True}
    job_id = enqueue_job(
        type="openai_oauth",
        input={"account_id": account_id},
        account_id=account_id,
        proxy_id=row.proxy_id,
        proxy_url=row.proxy_url or "",
    )
    return {"job_id": job_id, "already_has_refresh_token": False, "already_running": False}


@router.post("/api/accounts/access-token-refresh", tags=["accounts"])
def refresh_access_tokens(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or [] if int(i or 0) > 0))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    jobs: list[dict[str, Any]] = []
    with Session(engine) as s:
        rows_by_id = {int(row.id or 0): row for row in s.exec(sa_select(ChatGPTAccount).where(ChatGPTAccount.id.in_(ids))).scalars()}
        missing = [aid for aid in ids if aid not in rows_by_id]
        if missing:
            raise HTTPException(status_code=404, detail={"missing": missing})
        running_rows = list(
            s.exec(
                sa_select(Job)
                .where(Job.account_id.in_(ids))
                .where(Job.type == "chatgpt_session")
                .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
                .order_by(Job.id.desc())
            ).scalars()
        )
        running_by_account: dict[int, Job] = {}
        for job in running_rows:
            aid = int(job.account_id or 0)
            if aid and aid not in running_by_account:
                running_by_account[aid] = job
        for aid in ids:
            row = rows_by_id[aid]
            running = running_by_account.get(aid)
            if running is not None:
                jobs.append({"account_id": aid, "job_id": int(running.id or 0), "already_running": True})
                continue
            job_id = enqueue_job(
                type="chatgpt_session",
                input={"account_id": aid, "force_refresh": True, "sync_sub2api_after_refresh": True},
                account_id=aid,
                proxy_id=row.proxy_id,
                proxy_url=row.proxy_url or "",
            )
            jobs.append({"account_id": aid, "job_id": job_id, "already_running": False})
    return {
        "total": len(ids),
        "enqueued": sum(1 for item in jobs if not item.get("already_running")),
        "already_running": sum(1 for item in jobs if item.get("already_running")),
        "jobs": jobs,
    }


@router.post("/api/accounts/{account_id}/access-token-refresh", tags=["accounts"])
def refresh_access_token(account_id: int):
    result = refresh_access_tokens(IdsRequest(ids=[account_id]))
    job = (result.get("jobs") or [{}])[0]
    return {"job_id": job.get("job_id"), "already_running": bool(job.get("already_running"))}


@router.post("/api/accounts/{account_id}/sub2api-sync", tags=["accounts"])
def sync_sub2api_account(account_id: int):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        running = s.exec(
            sa_select(Job)
            .where(Job.account_id == account_id)
            .where(Job.type == "sub2api_sync")
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
            .order_by(Job.id.desc())
        ).scalars().first()
        if running is not None:
            return {"job_id": int(running.id or 0), "already_running": True}
        refresh_token = _refresh_tokens_by_account(s, [account_id]).get(account_id)
        payload: dict[str, Any] = {"account_id": account_id}
        if refresh_token is not None and refresh_token.id:
            payload["refresh_token_id"] = int(refresh_token.id)
        proxy_id = row.proxy_id
        proxy_url = row.proxy_url or ""
    job_id = enqueue_job(
        type="sub2api_sync",
        input=payload,
        account_id=account_id,
        proxy_id=proxy_id,
        proxy_url=proxy_url,
    )
    return {"job_id": job_id, "already_running": False}


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


@router.get("/api/accounts/{account_id}/email-history", tags=["accounts"])
def account_email_history(account_id: int, limit: int = Query(10, ge=1, le=50)):
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="account not found")
        email = row.email
    from backend.api.emails import email_history_for_address

    return email_history_for_address(email, limit=limit)


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


def _strip_export_proxy(account_doc: dict[str, Any]) -> None:
    account_doc.pop("proxy_key", None)


def _record_sub2api_status_refresh(
    account_id: int,
    sub2api_account_id: str,
    parsed: dict[str, Any],
    *,
    now,
    refresh_token: OpenAIRefreshToken | None,
) -> None:
    status = str(parsed.get("status") or "")
    error = str(parsed.get("error") or "")
    relogin_required = bool(parsed.get("relogin_required"))
    schedulable = bool(parsed.get("schedulable"))
    with session_scope() as s:
        binding = _get_or_create_sub2api_binding(s, account_id=account_id)
        binding.sub2api_account_id = sub2api_account_id or binding.sub2api_account_id
        binding.status = status
        binding.schedulable = schedulable
        binding.relogin_required = relogin_required
        binding.last_error = error if (error or relogin_required) else ""
        binding.last_status_check_at = now
        binding.updated_at = now
        s.add(binding)
        if refresh_token is not None and refresh_token.id:
            row = s.get(OpenAIRefreshToken, int(refresh_token.id))
            if row is not None:
                row.sub2api_account_id = sub2api_account_id or row.sub2api_account_id
                row.sub2api_status = status
                row.status_checked_at = now
                row.enabled = status.lower() not in INVALID_SUB2API_STATUSES and not relogin_required
                row.last_error = error if (error or relogin_required) else ""
                row.updated_at = now
                s.add(row)


def _record_sub2api_status_refresh_failure(account_id: int, error: str, *, now) -> None:
    with session_scope() as s:
        binding = _get_or_create_sub2api_binding(s, account_id=account_id)
        binding.status = "status_check_failed"
        binding.schedulable = False
        binding.last_error = str(error or "")
        binding.last_status_check_at = now
        binding.updated_at = now
        s.add(binding)


def _get_or_create_sub2api_binding(s: Session, *, account_id: int) -> Sub2ApiAccountBinding:
    base_url = _sub2api_base_url()
    row = s.exec(
        sa_select(Sub2ApiAccountBinding)
        .where(Sub2ApiAccountBinding.chatgpt_account_id == int(account_id))
        .where(Sub2ApiAccountBinding.platform == "openai")
        .where(Sub2ApiAccountBinding.sub2api_base_url == base_url)
        .order_by(Sub2ApiAccountBinding.id.desc())
    ).scalars().first()
    if row is not None:
        return row
    return Sub2ApiAccountBinding(
        chatgpt_account_id=int(account_id),
        platform="openai",
        sub2api_base_url=base_url,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _sub2api_base_url() -> str:
    try:
        return get_sub2api_client().base_url
    except Exception:
        return ""


def _paid_account_ids(s: Session, account_ids: list[int]) -> set[int]:
    if not account_ids:
        return set()
    rows = list(
        s.exec(
            sa_select(ChatGPTAccount.id)
            .join(PaymentLink, PaymentLink.id == ChatGPTAccount.last_payment_link_id)
            .where(ChatGPTAccount.id.in_(account_ids))
            .where(PaymentLink.status == PAYMENT_LINK_STATUS_PAID_UNKNOWN)
        ).scalars().all()
    )
    return {int(row) for row in rows if int(row or 0)}


def _latest_refresh_token_jobs(s: Session, account_ids: list[int]) -> dict[int, Job]:
    if not account_ids:
        return {}
    jobs = list(
        s.exec(
            sa_select(Job)
            .where(Job.account_id.in_(account_ids))
            .where(Job.type == "openai_oauth")
            .order_by(Job.id.desc())
        ).scalars()
    )
    latest: dict[int, Job] = {}
    for job in jobs:
        aid = int(job.account_id or 0)
        if aid and aid not in latest:
            latest[aid] = job
    return latest


def _refresh_tokens_by_account(s: Session, account_ids: list[int]) -> dict[int, OpenAIRefreshToken]:
    if not account_ids:
        return {}
    rows = list(
        s.exec(
            sa_select(OpenAIRefreshToken)
            .where(OpenAIRefreshToken.account_id.in_(account_ids))
            .order_by(OpenAIRefreshToken.id.desc())
        ).scalars()
    )
    latest: dict[int, OpenAIRefreshToken] = {}
    for row in rows:
        aid = int(row.account_id or 0)
        if aid and aid not in latest:
            latest[aid] = row
    return latest


def _sub2api_bindings_by_account(s: Session, account_ids: list[int]) -> dict[int, Sub2ApiAccountBinding]:
    if not account_ids:
        return {}
    rows = list(
        s.exec(
            sa_select(Sub2ApiAccountBinding)
            .where(Sub2ApiAccountBinding.chatgpt_account_id.in_(account_ids))
            .where(Sub2ApiAccountBinding.platform == "openai")
            .order_by(Sub2ApiAccountBinding.id.desc())
        ).scalars()
    )
    latest: dict[int, Sub2ApiAccountBinding] = {}
    for row in rows:
        aid = int(row.chatgpt_account_id or 0)
        if aid and aid not in latest:
            latest[aid] = row
    return latest


def _refresh_token_is_usable(refresh_token: OpenAIRefreshToken | None) -> bool:
    if refresh_token is None or not refresh_token.refresh_token:
        return False
    if not refresh_token.enabled:
        return False
    return str(refresh_token.sub2api_status or "").strip().lower() not in INVALID_SUB2API_STATUSES


def _matches_sub2api_status_filter(
    binding: Sub2ApiAccountBinding | None,
    refresh_token: OpenAIRefreshToken | None,
    expected: str,
) -> bool:
    expected_value = str(expected or "").strip().lower()
    actual = _effective_sub2api_status(binding, refresh_token)
    if expected_value == "pending_sync":
        return actual in {"pending_sync", "pending_upload", ""}
    return actual == expected_value


def _effective_sub2api_status(binding: Sub2ApiAccountBinding | None, refresh_token: OpenAIRefreshToken | None) -> str:
    if binding is not None and str(binding.status or "").strip():
        return str(binding.status or "").strip().lower()
    if refresh_token is not None and str(refresh_token.sub2api_status or "").strip():
        return str(refresh_token.sub2api_status or "").strip().lower()
    return ""


def _with_refresh_token(item: dict[str, Any], refresh_token: OpenAIRefreshToken | None) -> dict[str, Any]:
    item.update({
        "has_refresh_token": _refresh_token_is_usable(refresh_token),
        "refresh_token_id": refresh_token.id if refresh_token else None,
        "refresh_token_enabled": bool(refresh_token.enabled) if refresh_token else False,
        "refresh_token_has_token": bool(refresh_token.refresh_token) if refresh_token else False,
        "refresh_token_last_error": refresh_token.last_error if refresh_token else "",
        "sub2api_account_id": refresh_token.sub2api_account_id if refresh_token else "",
        "sub2api_status": refresh_token.sub2api_status if refresh_token else "",
        "sub2api_auth_mode": "",
        "sub2api_schedulable": None,
        "sub2api_relogin_required": False,
        "sub2api_last_error": "",
        "sub2api_uploaded_at": refresh_token.uploaded_at.isoformat() if refresh_token and refresh_token.uploaded_at else None,
        "sub2api_status_checked_at": refresh_token.status_checked_at.isoformat() if refresh_token and refresh_token.status_checked_at else None,
    })
    return item


def _with_sub2api_binding(item: dict[str, Any], binding: Sub2ApiAccountBinding | None) -> dict[str, Any]:
    if binding is None:
        return item
    item.update({
        "sub2api_account_id": binding.sub2api_account_id or item.get("sub2api_account_id") or "",
        "sub2api_status": binding.status or item.get("sub2api_status") or "",
        "sub2api_auth_mode": binding.auth_mode or "",
        "sub2api_schedulable": bool(binding.schedulable),
        "sub2api_relogin_required": bool(binding.relogin_required),
        "sub2api_last_error": "" if str(binding.last_error or "").strip().lower() in {"success", "ok", "synced", "active", "alive"} else binding.last_error or "",
        "sub2api_uploaded_at": binding.last_sync_at.isoformat() if binding.last_sync_at else item.get("sub2api_uploaded_at"),
        "sub2api_status_checked_at": binding.last_status_check_at.isoformat() if binding.last_status_check_at else item.get("sub2api_status_checked_at"),
    })
    return item


def _subscription_account_to_dict(
    pipeline: Pipeline,
    account: ChatGPTAccount | None,
    payment_link: PaymentLink | None,
    refresh_job: Job | None,
    refresh_token: OpenAIRefreshToken | None,
    sub2api_binding: Sub2ApiAccountBinding | None,
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
    item = _with_sub2api_binding(_with_refresh_token(item, refresh_token), sub2api_binding)
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
