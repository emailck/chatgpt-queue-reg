"""Resource pool introspection APIs."""
from __future__ import annotations

from backend.core.pools import all_resource_pools
from backend.core.stages import all_stages, to_dict as stage_to_dict

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/pools", tags=["pools"])
def list_pools():
    out: dict[str, dict] = {}
    for name, pool in all_resource_pools().items():
        try:
            stats = pool.stats()
        except Exception as exc:
            stats = {"error": str(exc)}
        out[name] = stats
    return out


@router.get("/api/stages", tags=["stages"])
def list_stages():
    return {name: stage_to_dict(meta) for name, meta in all_stages().items()}
