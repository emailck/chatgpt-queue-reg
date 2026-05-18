"""Stage registry.

A *stage* is the unit a worker pool consumes. Each stage is registered exactly
once via the `@stage(...)` decorator and exposes:

  * `name`   — the canonical stage id (also `Job.type`)
  * `handler`— the callable `(JobContext) -> None` (may be None until P3)
  * `requires_resources` / `optional_resources` — names of `ResourcePool`s
  * `default_concurrency` — initial worker pool size for this stage
  * `rate_limit_per_min` — optional per-stage rate limit (None = no limit)
  * `retry_policy` — placeholder for P3
  * `input_schema` / `output_schema` — Pydantic models, optional in P1

The registry is the single source of truth for "which stage names exist".
The queue dispatcher, pipeline runner and API layer all consult it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from backend.core.job_context import JobContext

StageHandler = Callable[[JobContext], None]

ALLOWED_STAGE_NAMES: tuple[str, ...] = (
    "register",
    "payment_link",
    "payment",
    "oauth_codex",
    "rt_keepalive",
)
ALLOWED_STAGE_SET = set(ALLOWED_STAGE_NAMES)


@dataclass
class RetryPolicy:
    max_attempts: int = 1


@dataclass
class StageMeta:
    name: str
    handler: Optional[StageHandler] = None
    requires_resources: tuple[str, ...] = ()
    optional_resources: tuple[str, ...] = ()
    default_concurrency: int = 1
    rate_limit_per_min: Optional[int] = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    input_schema: Optional[type] = None
    output_schema: Optional[type] = None
    description: str = ""

    def is_implemented(self) -> bool:
        return self.handler is not None


STAGE_REGISTRY: dict[str, StageMeta] = {}


def stage(
    name: str,
    *,
    requires_resources: tuple[str, ...] | list[str] = (),
    optional_resources: tuple[str, ...] | list[str] = (),
    default_concurrency: int = 1,
    rate_limit_per_min: Optional[int] = None,
    retry_policy: Optional[RetryPolicy] = None,
    input_schema: Optional[type] = None,
    output_schema: Optional[type] = None,
    description: str = "",
) -> Callable[[StageHandler], StageHandler]:
    """Decorator to register a stage handler.

    Usage:
        @stage(name="register", requires_resources=["email_pool"], default_concurrency=3)
        def run_register(ctx: JobContext) -> None: ...
    """

    def _decorator(fn: StageHandler) -> StageHandler:
        register_stage(
            StageMeta(
                name=name,
                handler=fn,
                requires_resources=tuple(requires_resources),
                optional_resources=tuple(optional_resources),
                default_concurrency=default_concurrency,
                rate_limit_per_min=rate_limit_per_min,
                retry_policy=retry_policy or RetryPolicy(),
                input_schema=input_schema,
                output_schema=output_schema,
                description=description,
            )
        )
        return fn

    return _decorator


def register_stage(meta: StageMeta) -> None:
    """Insert (or replace handler-only) a stage in the registry.

    Re-registering the same name with a handler is allowed (idempotent re-import
    during dev reload). Re-registering with conflicting resource lists raises.
    """
    if meta.name not in ALLOWED_STAGE_SET:
        raise ValueError(f"stage {meta.name!r} is not one of the 5 WorkPool stages")
    existing = STAGE_REGISTRY.get(meta.name)
    if existing is None:
        STAGE_REGISTRY[meta.name] = meta
        return

    # Allow upgrading a handler-less placeholder with a real handler.
    if not existing.is_implemented() and meta.handler is not None:
        # Merge: keep existing meta (resources etc) but adopt the handler and
        # any provided override fields.
        existing.handler = meta.handler
        if meta.requires_resources:
            existing.requires_resources = meta.requires_resources
        if meta.optional_resources:
            existing.optional_resources = meta.optional_resources
        if meta.default_concurrency:
            existing.default_concurrency = meta.default_concurrency
        if meta.rate_limit_per_min is not None:
            existing.rate_limit_per_min = meta.rate_limit_per_min
        if meta.input_schema is not None:
            existing.input_schema = meta.input_schema
        if meta.output_schema is not None:
            existing.output_schema = meta.output_schema
        if meta.description:
            existing.description = meta.description
        return

    # Same handler being re-registered (e.g. import-twice during reload). OK.
    if existing.handler is meta.handler and meta.handler is not None:
        return

    raise ValueError(f"stage {meta.name!r} is already registered")


def declare_stage(
    name: str,
    *,
    requires_resources: tuple[str, ...] | list[str] = (),
    optional_resources: tuple[str, ...] | list[str] = (),
    default_concurrency: int = 1,
    rate_limit_per_min: Optional[int] = None,
    description: str = "",
) -> StageMeta:
    """Reserve a stage name without providing a handler yet.

    Used to register `payment` / `oauth_codex` / `rt_keepalive` in P1 even
    though their handlers land in P3. The queue dispatcher will refuse to run
    a job whose stage has no handler, so it stays safe.
    """
    meta = StageMeta(
        name=name,
        handler=None,
        requires_resources=tuple(requires_resources),
        optional_resources=tuple(optional_resources),
        default_concurrency=default_concurrency,
        rate_limit_per_min=rate_limit_per_min,
        description=description,
    )
    register_stage(meta)
    return STAGE_REGISTRY[name]


def get_stage(name: str) -> Optional[StageMeta]:
    return STAGE_REGISTRY.get(name)


def all_stages() -> dict[str, StageMeta]:
    return dict(STAGE_REGISTRY)


def stage_names() -> list[str]:
    return [name for name in ALLOWED_STAGE_NAMES if name in STAGE_REGISTRY]


def implemented_stage_names() -> list[str]:
    return [n for n, m in STAGE_REGISTRY.items() if m.is_implemented()]


def _schema_name(schema: Optional[type]) -> str:
    return schema.__name__ if schema is not None else ""


def to_dict(meta: StageMeta) -> dict[str, Any]:
    return {
        "name": meta.name,
        "implemented": meta.is_implemented(),
        "requires_resources": list(meta.requires_resources),
        "optional_resources": list(meta.optional_resources),
        "default_concurrency": meta.default_concurrency,
        "rate_limit_per_min": meta.rate_limit_per_min,
        "input_schema": _schema_name(meta.input_schema),
        "output_schema": _schema_name(meta.output_schema),
        "description": meta.description,
    }
