"""Minimal auth endpoint stub.

Keeps the API surface compatible with the legacy frontend's
`/api/auth/status` probe so the existing auth-bypass branch keeps working
unchanged in the new project.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/auth/status", tags=["auth"])
def auth_status():
    return {"has_password": False}
