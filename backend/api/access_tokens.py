"""Access-token pool API: list / get / delete / batch-delete / export.

Export modes:
  - JSON  (full record incl. cookies, fp, localStorage)
  - CSV   (flat record + base64-encoded cookies / metadata)
  - TXT   (one line per row, configurable separator)
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import access_token_account_to_dict
from backend.core.constants import JOB_STATUS_QUEUED, JOB_STATUS_RUNNING
from backend.core.db import engine, session_scope
from backend.core.queue import enqueue_job
from backend.models.access_token import AccessTokenAccount
from backend.models.codex_token import CodexToken
from backend.models.job import Job

INVALID_SUB2API_STATUSES = {"dead", "disabled", "banned", "invalid", "expired"}

router = APIRouter()


class IdsRequest(BaseModel):
    ids: list[int]


class UpdateRequest(BaseModel):
    note: str | None = None


@router.get("/api/access-tokens", tags=["access-tokens"])
def list_access_tokens(
    include_secrets: bool = False,
    pool: str = Query("all", pattern="^(all|at|rt)$"),
    limit: int = Query(500, ge=1, le=2000),
):
    with Session(engine) as s:
        stmt = sa_select(AccessTokenAccount)
        if pool == "at":
            stmt = stmt.where(AccessTokenAccount.access_token != "")
        rows = list(s.exec(stmt.order_by(AccessTokenAccount.id.desc()).limit(limit)).scalars())
        codex_by_account = _codex_tokens_by_account(s, [int(r.chatgpt_account_id or 0) for r in rows])
    items = [_with_codex_token(access_token_account_to_dict(r, include_secrets=include_secrets), codex_by_account.get(int(r.chatgpt_account_id or 0)), include_secrets=include_secrets) for r in rows]
    if pool == "rt":
        items = [item for item in items if item.get("has_refresh_token")]
    elif pool == "at":
        items = [item for item in items if not item.get("has_refresh_token")]
    return items


@router.get("/api/access-tokens/export", tags=["access-tokens"])
def export_access_tokens(
    fmt: str = Query("json", pattern="^(json|csv|txt)$"),
    ids: Optional[str] = None,
    pool: str = Query("all", pattern="^(all|at|rt)$"),
    separator: str = "----",
    fields: str = "email,password,access_token,refresh_token,session_token",
):
    """Export rows in JSON/CSV/TXT.

    - `ids`: optional comma-separated ID list; when omitted exports everything.
    - `separator`: TXT field separator, defaults to legacy `----`.
    - `fields`: TXT field order, comma-separated. Allowed:
        email, password, account_id, workspace_id,
        access_token, refresh_token, id_token, session_token,
        proxy_url, user_agent.
    """
    id_filter = _parse_id_filter(ids)

    with Session(engine) as s:
        stmt = sa_select(AccessTokenAccount)
        if id_filter is not None:
            stmt = stmt.where(AccessTokenAccount.id.in_(id_filter))
        elif pool == "at":
            stmt = stmt.where(AccessTokenAccount.access_token != "")
        rows = list(s.exec(stmt.order_by(AccessTokenAccount.id.asc())).scalars())
        codex_by_account = _codex_tokens_by_account(s, [int(r.chatgpt_account_id or 0) for r in rows])

    if pool in {"at", "rt"} and id_filter is None:
        rows = [
            row for row in rows
            if _codex_token_is_usable(_codex_for_row(row, codex_by_account)) == (pool == "rt")
        ]

    if fmt == "json":
        body = json.dumps(
            [_with_codex_token(access_token_account_to_dict(r, include_secrets=True), codex_by_account.get(int(r.chatgpt_account_id or 0)), include_secrets=True) for r in rows],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return _stream(body, "application/json", "access-tokens.json")

    if fmt == "csv":
        return _stream(_render_csv(rows, codex_by_account=codex_by_account), "text/csv; charset=utf-8", "access-tokens.csv")

    return _stream(
        _render_txt(rows, separator=separator, fields=fields, codex_by_account=codex_by_account),
        "text/plain; charset=utf-8",
        "access-tokens.txt",
    )


@router.post("/api/access-tokens/batch-delete", tags=["access-tokens"])
def batch_delete_access_tokens(body: IdsRequest):
    ids = list(dict.fromkeys(int(i) for i in body.ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    deleted: list[int] = []
    not_found: list[int] = []
    with session_scope() as s:
        for at_id in ids:
            row = s.get(AccessTokenAccount, at_id)
            if row is None:
                not_found.append(at_id)
                continue
            s.delete(row)
            deleted.append(at_id)
    return {
        "deleted": len(deleted),
        "deleted_ids": deleted,
        "not_found": not_found,
        "total_requested": len(ids),
    }


@router.get("/api/access-tokens/{at_id}", tags=["access-tokens"])
def get_access_token(at_id: int, include_secrets: bool = False):
    with Session(engine) as s:
        row = s.get(AccessTokenAccount, at_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        codex_token = _codex_tokens_by_account(s, [int(row.chatgpt_account_id or 0)]).get(int(row.chatgpt_account_id or 0))
    return _with_codex_token(access_token_account_to_dict(row, include_secrets=include_secrets), codex_token, include_secrets=include_secrets)


@router.post("/api/access-tokens/{at_id}/refresh-token", tags=["access-tokens"])
def fetch_access_token_refresh_token(at_id: int):
    with Session(engine) as s:
        row = s.get(AccessTokenAccount, at_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        account_id = int(row.chatgpt_account_id or 0)
        if not account_id:
            raise HTTPException(status_code=409, detail="该 AT 行未关联 ChatGPT 账号，无法获取 RT")
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
            return {
                "job_id": int(running.id or 0),
                "already_has_refresh_token": False,
                "already_running": True,
            }
        proxy_id = row.proxy_id
        proxy_url = row.proxy_url or ""
    job_id = enqueue_job(
        type="oauth_codex",
        input={"account_id": account_id, "access_token_account_id": at_id},
        account_id=account_id,
        proxy_id=proxy_id,
        proxy_url=proxy_url,
    )
    return {"job_id": job_id, "already_has_refresh_token": False, "already_running": False}


@router.patch("/api/access-tokens/{at_id}", tags=["access-tokens"])
def update_access_token(at_id: int, body: UpdateRequest):
    with session_scope() as s:
        row = s.get(AccessTokenAccount, at_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        if body.note is not None:
            row.note = body.note
        s.add(row)
        s.commit()
        s.refresh(row)
        account_id = int(row.chatgpt_account_id or 0)
        codex_token = _codex_tokens_by_account(s, [account_id]).get(account_id)
        return _with_codex_token(access_token_account_to_dict(row), codex_token)


@router.delete("/api/access-tokens/{at_id}", tags=["access-tokens"])
def delete_access_token(at_id: int):
    with session_scope() as s:
        row = s.get(AccessTokenAccount, at_id)
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        s.delete(row)
    return {"ok": True}


# ---- helpers ---------------------------------------------------------------


def _parse_id_filter(ids: str | None) -> list[int] | None:
    if not ids:
        return None
    out: list[int] = []
    for token in str(ids).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"非法 id: {token}")
    return out or None


def _stream(body: str, media_type: str, filename: str) -> StreamingResponse:
    response = StreamingResponse(iter([body]), media_type=media_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


CSV_FIELDS = (
    "id",
    "pipeline_id",
    "chatgpt_account_id",
    "email",
    "password",
    "account_id",
    "workspace_id",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "proxy_url",
    "user_agent",
    "note",
    "created_at",
    "updated_at",
)


def _codex_tokens_by_account(s: Session, account_ids: list[int]) -> dict[int, CodexToken]:
    ids = [int(i or 0) for i in account_ids if int(i or 0)]
    if not ids:
        return {}
    rows = list(
        s.exec(
            sa_select(CodexToken)
            .where(CodexToken.account_id.in_(ids))
            .order_by(CodexToken.id.desc())
        ).scalars()
    )
    latest: dict[int, CodexToken] = {}
    for row in rows:
        aid = int(row.account_id or 0)
        if aid and aid not in latest:
            latest[aid] = row
    return latest


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text if len(text) <= 24 else f"{text[:24]}..."


def _codex_token_is_usable(codex_token: CodexToken | None) -> bool:
    if codex_token is None or not codex_token.refresh_token:
        return False
    if not codex_token.alive:
        return False
    return str(codex_token.sub2api_status or "").strip().lower() not in INVALID_SUB2API_STATUSES


def _with_codex_token(item: dict[str, Any], codex_token: CodexToken | None, *, include_secrets: bool = False) -> dict[str, Any]:
    if codex_token is None:
        item.update({
            "refresh_token": "",
            "has_refresh_token": False,
            "codex_token_id": None,
            "codex_token_alive": False,
            "codex_token_has_refresh_token": False,
            "codex_token_last_error": "",
            "sub2api_external_id": "",
            "sub2api_status": "",
            "sub2api_uploaded_at": None,
            "sub2api_status_checked_at": None,
        })
        return item
    refresh_token = str(codex_token.refresh_token or "")
    item.update({
        "refresh_token": refresh_token if include_secrets else _mask(refresh_token),
        "has_refresh_token": _codex_token_is_usable(codex_token),
        "codex_token_id": codex_token.id,
        "codex_token_alive": bool(codex_token.alive),
        "codex_token_has_refresh_token": bool(refresh_token),
        "codex_token_last_error": codex_token.last_error,
        "sub2api_external_id": codex_token.sub2api_external_id,
        "sub2api_status": codex_token.sub2api_status,
        "sub2api_uploaded_at": codex_token.uploaded_at.isoformat() if codex_token.uploaded_at else None,
        "sub2api_status_checked_at": codex_token.status_checked_at.isoformat() if codex_token.status_checked_at else None,
    })
    return item


def _codex_for_row(row: AccessTokenAccount, codex_by_account: dict[int, CodexToken]) -> CodexToken | None:
    return codex_by_account.get(int(row.chatgpt_account_id or 0))


def _render_csv(rows: list[AccessTokenAccount], *, codex_by_account: dict[int, CodexToken]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_FIELDS)
    for row in rows:
        codex_token = _codex_for_row(row, codex_by_account)
        writer.writerow([
            row.id,
            row.pipeline_id or "",
            row.chatgpt_account_id or "",
            row.email,
            row.password,
            row.account_id,
            row.workspace_id,
            row.access_token,
            codex_token.refresh_token if codex_token else "",
            codex_token.id_token if codex_token and codex_token.id_token else row.id_token,
            row.session_token,
            row.proxy_url,
            row.user_agent,
            row.note,
            row.created_at.isoformat() if row.created_at else "",
            row.updated_at.isoformat() if row.updated_at else "",
        ])
    return buf.getvalue()


ALLOWED_TXT_FIELDS = {
    "email": lambda r, c: r.email,
    "password": lambda r, c: r.password,
    "account_id": lambda r, c: r.account_id,
    "workspace_id": lambda r, c: r.workspace_id,
    "access_token": lambda r, c: r.access_token,
    "refresh_token": lambda r, c: c.refresh_token if c else "",
    "id_token": lambda r, c: c.id_token if c and c.id_token else r.id_token,
    "session_token": lambda r, c: r.session_token,
    "proxy_url": lambda r, c: r.proxy_url,
    "user_agent": lambda r, c: r.user_agent,
}


def _render_txt(rows: list[AccessTokenAccount], *, separator: str, fields: str, codex_by_account: dict[int, CodexToken]) -> str:
    keys = [f.strip() for f in str(fields or "").split(",") if f.strip()]
    if not keys:
        keys = ["email", "password", "access_token", "refresh_token", "session_token"]
    invalid = [k for k in keys if k not in ALLOWED_TXT_FIELDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"不支持的字段: {','.join(invalid)}")
    sep = separator if separator else "----"
    lines: list[str] = []
    for row in rows:
        codex_token = _codex_for_row(row, codex_by_account)
        parts = [str(ALLOWED_TXT_FIELDS[k](row, codex_token) or "") for k in keys]
        lines.append(sep.join(parts))
    return "\n".join(lines) + ("\n" if lines else "")
