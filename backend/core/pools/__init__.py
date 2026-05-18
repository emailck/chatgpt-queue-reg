"""Resource pool registry.

A *resource pool* hands out raw materials (emails, cards, sms numbers,
proxies) to running jobs. The registry is queried by `JobContext.acquire()`
to look up the appropriate pool by name. Concrete pool implementations
land in P2.
"""
from __future__ import annotations

from typing import Optional

from backend.core.pools.base import (
    AcquireOutcome,
    AcquiredResource,
    Resource,
    ResourcePool,
)

RESOURCE_REGISTRY: dict[str, ResourcePool] = {}


def register_resource(pool: ResourcePool) -> None:
    name = pool.name
    if not name:
        raise ValueError("resource pool must have a name")
    if name in RESOURCE_REGISTRY and RESOURCE_REGISTRY[name] is not pool:
        raise ValueError(f"resource pool {name!r} already registered")
    RESOURCE_REGISTRY[name] = pool


def get_resource_pool(name: str) -> Optional[ResourcePool]:
    return RESOURCE_REGISTRY.get(name)


def all_resource_pools() -> dict[str, ResourcePool]:
    return dict(RESOURCE_REGISTRY)


__all__ = [
    "RESOURCE_REGISTRY",
    "register_resource",
    "get_resource_pool",
    "all_resource_pools",
    "AcquireOutcome",
    "AcquiredResource",
    "Resource",
    "ResourcePool",
]


# --- v1 concrete pools ---
from .email_pool import email_pool
from .card_pool import card_pool
from .sms_pool import sms_pool
from .proxy_pool import proxy_pool as proxy_resource_pool

register_resource(email_pool)
register_resource(card_pool)
register_resource(sms_pool)
register_resource(proxy_resource_pool)
