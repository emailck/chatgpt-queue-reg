"""Settings (key-value config) CRUD."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.queue import get_pool
from backend.core.settings import settings

router = APIRouter()


class SettingsUpdate(BaseModel):
    data: dict[str, str]


@router.get("/api/settings", tags=["settings"])
def get_settings():
    return settings.get_all()


@router.put("/api/settings", tags=["settings"])
def update_settings(body: SettingsUpdate):
    safe = {str(k): str(v if v is not None else "") for k, v in (body.data or {}).items()}
    settings.set_many(safe)
    resized = _apply_concurrency_updates(safe)
    return {"ok": True, "updated": list(safe.keys()), "resized_concurrency": resized}


def _apply_concurrency_updates(values: dict[str, str]) -> dict[str, int]:
    resized: dict[str, int] = {}
    pool = get_pool()
    for key, value in values.items():
        if not key.startswith("worker_concurrency."):
            continue
        stage = key.removeprefix("worker_concurrency.").strip()
        if not stage:
            continue
        try:
            parsed = int(value or 0)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid concurrency for {stage}: {value!r}") from exc
        try:
            resized[stage] = pool.set_concurrency(stage, parsed)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return resized
