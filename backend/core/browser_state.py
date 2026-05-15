"""Reusable helpers for assembling browser-context state.

These functions are pure-data only — they don't touch playwright.  They are
shared between flows (which need cookies for headless requests) and the
BrowserDebugService (which injects them into a real browser context).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.json_utils import json_loads


def cookies_for_playwright(
    cookies_source: Any,
    *,
    default_domain: str = ".chatgpt.com",
    allow_domain_substring: str = "chatgpt.com",
) -> list[dict[str, Any]]:
    """Normalize a cookie source into a list of playwright add_cookies entries.

    Accepts:
      - a JSON string of [{name, value, domain, path}, ...]
      - a list of cookie dicts
      - the legacy "name=value; name2=value2" header form
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []

    def add(name: str, value: str, domain: str | None = None, path: str | None = None) -> None:
        name = str(name or "").strip()
        value = str(value or "")
        domain_str = str(domain or default_domain).strip() or default_domain
        path_str = str(path or "/").strip() or "/"
        if not name or not value:
            return
        if len(value) > 4096:
            return
        if allow_domain_substring and allow_domain_substring not in domain_str:
            return
        if domain_str.startswith("."):
            normalized_domain = domain_str
        else:
            normalized_domain = "." + domain_str if "." in domain_str else default_domain
        key = (name, normalized_domain, path_str)
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name, "value": value, "domain": normalized_domain, "path": path_str})

    parsed: list[dict[str, Any]] | None = None
    if isinstance(cookies_source, list):
        parsed = [c for c in cookies_source if isinstance(c, dict)]
    elif isinstance(cookies_source, str):
        text = cookies_source.strip()
        if text.startswith("["):
            data = json_loads(text, fallback=[])
            if isinstance(data, list):
                parsed = [c for c in data if isinstance(c, dict)]
        if parsed is None and text:
            for part in text.split(";"):
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                add(k.strip(), v.strip())
    elif isinstance(cookies_source, dict):
        # raw {name: value} mapping
        for name, value in cookies_source.items():
            add(str(name), str(value))

    if parsed is not None:
        for entry in parsed:
            add(
                str(entry.get("name") or ""),
                str(entry.get("value") or ""),
                domain=entry.get("domain"),
                path=entry.get("path"),
            )

    return out


def normalize_local_storage(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        data = json_loads(value, fallback={}) or {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    return {}


def cookie_names(cookies: list[dict[str, Any]]) -> str:
    names = [str(c.get("name") or "") for c in cookies if c.get("name")]
    return ", ".join(names) if names else "none"


def resolve_har_path(har_dir: str | None = None) -> str:
    base = Path(har_dir).expanduser() if har_dir else Path("logs") / "har"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"debug-{datetime.now().strftime('%Y%m%d-%H%M%S')}.har")
