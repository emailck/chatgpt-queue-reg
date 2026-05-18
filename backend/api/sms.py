"""SMS project configuration APIs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.time_utils import utcnow
from backend.models.sms_project import SmsProject

router = APIRouter()


class SmsProjectCreate(BaseModel):
    name: str
    provider: str = "smstome"
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    note: str = ""


class SmsProjectUpdate(BaseModel):
    provider: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


@router.get("/api/sms/projects", tags=["sms"])
def list_sms_projects(limit: int = Query(500, ge=1, le=1000)):
    with Session(engine) as s:
        rows = list(s.exec(sa_select(SmsProject).order_by(SmsProject.id.desc()).limit(limit)).scalars())
    return [_sms_project_to_dict(row) for row in rows]


@router.post("/api/sms/projects", tags=["sms"])
def create_sms_project(body: SmsProjectCreate):
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="project name is required")
    with session_scope() as s:
        existing = s.exec(sa_select(SmsProject).where(SmsProject.name == name)).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="sms project already exists")
        row = SmsProject(
            name=name,
            provider=body.provider,
            config_json=json_dumps(body.config or {}),
            enabled=body.enabled,
            note=body.note,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return _sms_project_to_dict(row)


@router.patch("/api/sms/projects/{project_id}", tags=["sms"])
def update_sms_project(project_id: int, body: SmsProjectUpdate):
    with session_scope() as s:
        row = s.get(SmsProject, project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="sms project not found")
        if body.provider is not None:
            row.provider = body.provider
        if body.config is not None:
            row.config_json = json_dumps(body.config)
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.note is not None:
            row.note = body.note
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return _sms_project_to_dict(row)


@router.delete("/api/sms/projects/{project_id}", tags=["sms"])
def delete_sms_project(project_id: int):
    with session_scope() as s:
        row = s.get(SmsProject, project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="sms project not found")
        s.delete(row)
    return {"ok": True}


def _sms_project_to_dict(row: SmsProject) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "config": json_loads(row.config_json, fallback={}),
        "enabled": row.enabled,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
