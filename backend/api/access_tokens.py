"""Access-token pool API: list / get / delete / batch-delete / export.

Export modes:
  - JSON  (full record incl. cookies, fp, localStorage)
  - CSV   (flat record + base64-encoded cookies / metadata)
  - TXT   (one line per row, configurable separator like the legacy
           `email----password----client_id----refresh_token` style)
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import access_token_account_to_dict
from backend.core.db import engine, session_scope
from backend.models.access_token import AccessTokenAccount

router = APIRouter()


class IdsRequest(BaseModel):
    ids: list[int]


class UpdateRequest(BaseModel):
    note: str | None = None


@router.get("/api/access-tokens", tags=["access-tokens"])
def list_access_tokens(
    include_secrets: bool = False,
    limit: int = Query(500, ge=1, le=2000),
):
    with Session(engine) as s:
        rows = list(
            s.exec(
                sa_select(AccessTokenAccount)
                .order_by(AccessTokenAccount.id.desc())
                .limit(limit)
            ).scalars()
        )
    return [access_token_account_to_dict(r, include_secrets=include_secrets) for r in rows]


@router.get("/api/access-tokens/export", tags=["access-tokens"])
def export_access_tokens(
    fmt: str = Query("json", pattern="^(json|csv|txt)$"),
    ids: Optional[str] = None,
    separator: str = "----",
    fields: str = "email,password,access_token,refresh_token",
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
        stmt = sa_select(AccessTokenAccount).order_by(AccessTokenAccount.id.asc())
        if id_filter is not None:
            stmt = stmt.where(AccessTokenAccount.id.in_(id_filter))
        rows = list(s.exec(stmt).scalars())

    if fmt == "json":
        body = json.dumps(
            [access_token_account_to_dict(r, include_secrets=True) for r in rows],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return _stream(body, "application/json", "access-tokens.json")

    if fmt == "csv":
        return _stream(_render_csv(rows), "text/csv; charset=utf-8", "access-tokens.csv")

    return _stream(
        _render_txt(rows, separator=separator, fields=fields),
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
    return access_token_account_to_dict(row, include_secrets=include_secrets)


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
        return access_token_account_to_dict(row)


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


def _render_csv(rows: list[AccessTokenAccount]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_FIELDS)
    for row in rows:
        writer.writerow([
            row.id,
            row.pipeline_id or "",
            row.chatgpt_account_id or "",
            row.email,
            row.password,
            row.account_id,
            row.workspace_id,
            row.access_token,
            row.refresh_token,
            row.id_token,
            row.session_token,
            row.proxy_url,
            row.user_agent,
            row.note,
            row.created_at.isoformat() if row.created_at else "",
            row.updated_at.isoformat() if row.updated_at else "",
        ])
    return buf.getvalue()


ALLOWED_TXT_FIELDS = {
    "email": lambda r: r.email,
    "password": lambda r: r.password,
    "account_id": lambda r: r.account_id,
    "workspace_id": lambda r: r.workspace_id,
    "access_token": lambda r: r.access_token,
    "refresh_token": lambda r: r.refresh_token,
    "id_token": lambda r: r.id_token,
    "session_token": lambda r: r.session_token,
    "proxy_url": lambda r: r.proxy_url,
    "user_agent": lambda r: r.user_agent,
}


def _render_txt(rows: list[AccessTokenAccount], *, separator: str, fields: str) -> str:
    keys = [f.strip() for f in str(fields or "").split(",") if f.strip()]
    if not keys:
        keys = ["email", "password", "access_token", "refresh_token"]
    invalid = [k for k in keys if k not in ALLOWED_TXT_FIELDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"不支持的字段: {','.join(invalid)}")
    sep = separator if separator else "----"
    lines: list[str] = []
    for row in rows:
        parts = [str(ALLOWED_TXT_FIELDS[k](row) or "") for k in keys]
        lines.append(sep.join(parts))
    return "\n".join(lines) + ("\n" if lines else "")
