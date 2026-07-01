"""Codex invitation history summary APIs."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.pipeline import create_pipeline
from backend.models.job import Job

router = APIRouter()


class ActivateCodexInvitesRequest(BaseModel):
    emails: list[str]
    dry_run: bool = False



def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


@router.get("/api/codex-invites", tags=["codex-invites"])
def list_codex_invites(
    inviter: Optional[str] = None,
    sent_only: bool = False,
    include_dry_run: bool = True,
    limit: int = Query(500, ge=1, le=2000),
):
    rows: list[dict[str, Any]] = []
    with Session(engine) as s:
        jobs = list(
            s.exec(
                sa_select(Job)
                .where(Job.type.in_(["codex_invitation", "codex_batch_invite"]))
                .order_by(Job.id.desc())
                .limit(limit)
            ).scalars()
        )

    for job in jobs:
        result = json_loads(job.result_json, fallback={}) or {}
        input_data = json_loads(job.input_json, fallback={}) or {}
        if job.type == "codex_invitation":
            rows.extend(_rows_from_single(job, input_data, result))
        elif job.type == "codex_batch_invite":
            rows.extend(_rows_from_batch(job, input_data, result))

    if inviter:
        needle = str(inviter).strip().lower()
        rows = [r for r in rows if needle in str(r.get("source_email") or r.get("inviter") or "").lower() or needle == str(r.get("source_id") or "")]
    if sent_only:
        rows = [r for r in rows if r.get("sent") is True]
    if not include_dry_run:
        rows = [r for r in rows if not r.get("dry_run")]

    rows.sort(key=lambda r: int(r.get("job_id") or 0), reverse=True)
    total_emails = sum(len(r.get("emails") or []) for r in rows)
    sent_emails = sum(len(r.get("emails") or []) for r in rows if r.get("sent") is True)
    dry_run_emails = sum(len(r.get("emails") or []) for r in rows if r.get("dry_run"))
    failed_rows = sum(1 for r in rows if r.get("status") == "failed" or r.get("ok") is False)
    inviters = sorted({str(r.get("source_email") or r.get("inviter") or "") for r in rows if str(r.get("source_email") or r.get("inviter") or "")})
    return {
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "inviters": len(inviters),
            "inviter_emails": inviters,
            "total_emails": total_emails,
            "sent_emails": sent_emails,
            "dry_run_emails": dry_run_emails,
            "failed_rows": failed_rows,
        },
    }



@router.post("/api/codex-invites/activate", tags=["codex-invites"])
def activate_codex_invites(body: ActivateCodexInvitesRequest):
    emails = []
    seen = set()
    for raw in body.emails or []:
        email = str(raw or "").strip().lower()
        if not email or "@" not in email or email in seen:
            continue
        seen.add(email)
        emails.append(email)
    if not emails:
        return {"created": 0, "pipeline_ids": [], "emails": []}

    pipeline_ids: list[int] = []
    for email in emails:
        stage_inputs = {
            "sso_oauth": {"sso_email": email, "email": email},
            "active": {"email": email, "sso_email": email, "dry_run": bool(body.dry_run)},
        }
        pid = create_pipeline(
            stages=["sso_oauth", "active"],
            preset="codex_activation_manual",
            stage_inputs=stage_inputs,
            resource_bindings={},
            request_payload={"email": email, "sso_email": email, "dry_run": bool(body.dry_run)},
        )
        pipeline_ids.append(pid)
    return {"created": len(pipeline_ids), "pipeline_ids": pipeline_ids, "emails": emails, "dry_run": bool(body.dry_run)}

def _rows_from_single(job: Job, input_data: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    emails = _as_list(result.get("emails"))
    source_email = str(result.get("source_email") or input_data.get("inviter_email") or input_data.get("email") or "")
    return [{
        "job_id": int(job.id or 0),
        "pipeline_id": job.pipeline_id,
        "type": job.type,
        "status": job.status,
        "ok": job.status == "succeeded",
        "sent": bool(result.get("sent")),
        "dry_run": bool(result.get("dry_run")),
        "source_type": result.get("source_type") or input_data.get("source_type") or "",
        "source_id": result.get("source_id") or input_data.get("inviter_account_id") or input_data.get("email_id") or input_data.get("source_id") or "",
        "source_email": source_email,
        "domain": result.get("domain") or "",
        "emails": emails,
        "invited_email": result.get("invited_email") or (emails[0] if emails else ""),
        "remaining_invites": result.get("remaining_invites"),
        "status_code": result.get("status_code"),
        "error": job.error or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }]


def _rows_from_batch(job: Job, input_data: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in result.get("per_inviter") or []:
        if not isinstance(item, dict):
            continue
        emails = _as_list(item.get("emails"))
        out.append({
            "job_id": int(job.id or 0),
            "pipeline_id": job.pipeline_id,
            "type": job.type,
            "status": job.status,
            "ok": bool(item.get("ok", job.status == "succeeded")),
            "sent": bool(item.get("sent")),
            "dry_run": bool(item.get("dry_run", result.get("dry_run"))),
            "source_type": item.get("source_type") or input_data.get("source_type") or "",
            "source_id": item.get("source_id") or "",
            "source_email": item.get("source_email") or item.get("inviter") or "",
            "domain": item.get("domain") or "",
            "emails": emails,
            "invited_email": item.get("invited_email") or (emails[0] if emails else ""),
            "remaining_invites": item.get("remaining_invites"),
            "status_code": item.get("status_code"),
            "error": item.get("error") or job.error or "",
            "activation_pipeline_ids": result.get("activation_pipeline_ids") or [],
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        })
    if not out and result.get("invited_emails"):
        out.append({
            "job_id": int(job.id or 0),
            "pipeline_id": job.pipeline_id,
            "type": job.type,
            "status": job.status,
            "ok": job.status == "succeeded",
            "sent": False,
            "dry_run": bool(result.get("dry_run")),
            "source_type": input_data.get("source_type") or "",
            "source_id": "",
            "source_email": "",
            "domain": "",
            "emails": _as_list(result.get("invited_emails")),
            "invited_email": "",
            "remaining_invites": None,
            "status_code": None,
            "error": job.error or "",
            "activation_pipeline_ids": result.get("activation_pipeline_ids") or [],
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        })
    return out
