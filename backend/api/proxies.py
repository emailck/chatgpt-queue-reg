"""Proxy CRUD + pool actions."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.api.schemas import proxy_to_dict
from backend.core.db import engine, session_scope
from backend.core.proxy import normalize_proxy_url
from backend.core.proxy_pool import proxy_pool
from backend.core.time_utils import utcnow
from backend.models.proxy import Proxy

router = APIRouter()


class ProxyCreate(BaseModel):
    url: str
    label: str = ""
    region: str = ""
    enabled: bool = True


class ProxyUpdate(BaseModel):
    label: str | None = None
    region: str | None = None
    enabled: bool | None = None


class ProxyBulkCreate(BaseModel):
    proxies: list[str]
    region: str = ""


class ProxyBatchDelete(BaseModel):
    ids: list[int]


@router.get("/api/proxies", tags=["proxies"])
def list_proxies(limit: int = Query(500, ge=1, le=1000)):
    with Session(engine) as s:
        rows = list(s.exec(sa_select(Proxy).order_by(Proxy.id.desc()).limit(limit)).scalars())
    return [proxy_to_dict(r) for r in rows]


@router.post("/api/proxies", tags=["proxies"])
def create_proxy(body: ProxyCreate):
    url = normalize_proxy_url(body.url) or ""
    if not url:
        raise HTTPException(status_code=400, detail="proxy url is required")
    with session_scope() as s:
        existing = s.exec(sa_select(Proxy).where(Proxy.url == url)).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="proxy already exists")
        row = Proxy(url=url, label=body.label, region=body.region, enabled=body.enabled)
        s.add(row)
        s.commit()
        s.refresh(row)
        return proxy_to_dict(row)


@router.post("/api/proxies/bulk", tags=["proxies"])
def bulk_add(body: ProxyBulkCreate):
    added = 0
    skipped = 0
    with session_scope() as s:
        for raw in body.proxies:
            url = normalize_proxy_url(raw or "")
            if not url:
                skipped += 1
                continue
            existing = s.exec(sa_select(Proxy).where(Proxy.url == url)).scalars().first()
            if existing is not None:
                skipped += 1
                continue
            s.add(Proxy(url=url, region=body.region or "", enabled=True))
            added += 1
    return {"added": added, "skipped": skipped}


@router.patch("/api/proxies/{proxy_id}", tags=["proxies"])
def update_proxy(proxy_id: int, body: ProxyUpdate):
    with session_scope() as s:
        row = s.get(Proxy, proxy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="proxy not found")
        if body.label is not None:
            row.label = body.label
        if body.region is not None:
            row.region = body.region
        if body.enabled is not None:
            row.enabled = body.enabled
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return proxy_to_dict(row)


@router.patch("/api/proxies/{proxy_id}/toggle", tags=["proxies"])
def toggle_proxy(proxy_id: int):
    with session_scope() as s:
        row = s.get(Proxy, proxy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="proxy not found")
        row.enabled = not row.enabled
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"enabled": row.enabled}


@router.delete("/api/proxies/{proxy_id}", tags=["proxies"])
def delete_proxy(proxy_id: int):
    with session_scope() as s:
        row = s.get(Proxy, proxy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="proxy not found")
        s.delete(row)
    return {"ok": True}


@router.post("/api/proxies/batch-delete", tags=["proxies"])
def batch_delete_proxies(body: ProxyBatchDelete):
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    ids = list(dict.fromkeys(int(i) for i in body.ids))
    with session_scope() as s:
        rows = list(s.exec(sa_select(Proxy).where(Proxy.id.in_(ids))).scalars())
        found = {row.id for row in rows if row.id is not None}
        for row in rows:
            s.delete(row)
    return {
        "deleted": len(found),
        "not_found": [pid for pid in ids if pid not in found],
        "total_requested": len(ids),
    }


@router.post("/api/proxies/check", tags=["proxies"])
def check_proxies(background_tasks: BackgroundTasks):
    background_tasks.add_task(proxy_pool.check_all)
    return {"message": "检测任务已启动"}


@router.get("/api/proxies/next", tags=["proxies"])
def next_proxy(region: str = ""):
    url = proxy_pool.get_next(region=region or "")
    return {"url": url}

