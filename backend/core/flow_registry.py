"""Flow registry.

Each flow registers itself with `register_flow(type_name, fn)`.  The runner
looks the function up by `Job.type` and calls it with `(ctx)`.
"""
from __future__ import annotations

from typing import Callable

from backend.core.job_context import JobContext

FlowFn = Callable[[JobContext], None]

_FLOWS: dict[str, FlowFn] = {}


def register_flow(job_type: str, fn: FlowFn) -> None:
    _FLOWS[job_type] = fn


def get_flow(job_type: str) -> FlowFn | None:
    return _FLOWS.get(job_type)


def all_flows() -> dict[str, FlowFn]:
    return dict(_FLOWS)
