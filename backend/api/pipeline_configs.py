"""CRUD API for saved pipeline stage configurations."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.core.db import engine
from backend.models.pipeline_config import PipelineConfig

router = APIRouter()


class PipelineConfigCreate(BaseModel):
    name: str
    stages: List[str]
    stop_after: str = ""
    note: str = ""


class PipelineConfigUpdate(BaseModel):
    stages: List[str] | None = None
    stop_after: str | None = None
    note: str | None = None


class PipelineConfigOut(BaseModel):
    id: int
    name: str
    stages: list[str]
    stop_after: str
    note: str


@router.get("/api/pipeline-configs", tags=["pipeline-configs"])
def list_configs() -> list[PipelineConfigOut]:
    with Session(engine) as s:
        rows = s.exec(select(PipelineConfig).order_by(PipelineConfig.created_at.desc())).all()
        return _serialize(rows)


@router.post("/api/pipeline-configs", tags=["pipeline-configs"])
def create_config(body: PipelineConfigCreate) -> PipelineConfigOut:
    with Session(engine) as s:
        if s.exec(select(PipelineConfig).where(PipelineConfig.name == body.name)).first():
            raise HTTPException(status_code=409, detail=f"{body.name!r} 已存在")
        import json
        row = PipelineConfig(
            name=body.name.strip(),
            stages_json=json.dumps(body.stages),
            stop_after=body.stop_after,
            note=body.note,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return _serialize_one(row)


@router.delete("/api/pipeline-configs/{config_id}", tags=["pipeline-configs"])
def delete_config(config_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(PipelineConfig, config_id)
        if not row:
            raise HTTPException(status_code=404, detail="配置不存在")
        s.delete(row)
        s.commit()
        return {"ok": True}


import json as _json


def _serialize(rows) -> list[PipelineConfigOut]:
    out: list[PipelineConfigOut] = []
    for r in rows:
        out.append(_serialize_one(r))
    return out


def _serialize_one(r: PipelineConfig) -> PipelineConfigOut:
    return PipelineConfigOut(
        id=r.id or 0,
        name=r.name,
        stages=_json.loads(r.stages_json or "[]"),
        stop_after=r.stop_after or "",
        note=r.note or "",
    )
