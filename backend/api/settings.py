"""Settings (key-value config) CRUD."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

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
    return {"ok": True, "updated": list(safe.keys())}
