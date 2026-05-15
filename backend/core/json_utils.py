from __future__ import annotations

import json
from typing import Any


def json_dumps(value: Any, fallback: Any = None) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(fallback if fallback is not None else {}, ensure_ascii=False, default=str)


def json_loads(raw: str | None, fallback: Any = None) -> Any:
    try:
        if raw is None or str(raw).strip() == "":
            return fallback if fallback is not None else {}
        return json.loads(raw)
    except Exception:
        return fallback if fallback is not None else {}
