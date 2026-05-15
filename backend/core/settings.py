"""Single source of truth for application settings.

Mirrors the legacy `core/config_store.py` pattern: a `settings` SQLite table
(string key -> string value) with optional environment-variable fallback.
Used by both flows and the API.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable, Optional

from sqlmodel import Field, Session, SQLModel, select

from backend.core.db import engine

_DOTENV_LOCK = threading.Lock()
_DOTENV_LOADED = False
_LOAD_DOTENV_DEFAULT = os.getenv("APP_LOAD_DOTENV", "1").lower() in {"1", "true", "yes"}


class SettingItem(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = ""


def _ensure_table() -> None:
    SQLModel.metadata.create_all(engine, tables=[SettingItem.__table__])


def _maybe_load_dotenv() -> None:
    global _DOTENV_LOADED
    if not _LOAD_DOTENV_DEFAULT or _DOTENV_LOADED:
        return
    with _DOTENV_LOCK:
        if _DOTENV_LOADED:
            return
        _DOTENV_LOADED = True
        path = Path(os.getcwd()) / ".env"
        if not path.exists():
            return
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


class Settings:
    """Thin wrapper around the `settings` table."""

    def __init__(self) -> None:
        _ensure_table()

    def get(self, key: str, default: str = "") -> str:
        _maybe_load_dotenv()
        with Session(engine) as s:
            row = s.get(SettingItem, key)
            if row and row.value != "":
                return row.value
        env_val = os.getenv(key)
        if env_val is not None and env_val != "":
            return env_val
        return default

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, "1" if default else "0")
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def get_all(self) -> dict[str, str]:
        with Session(engine) as s:
            rows = s.exec(select(SettingItem)).all()
            return {r.key: r.value for r in rows}

    def set(self, key: str, value: str) -> None:
        with Session(engine) as s:
            row = s.get(SettingItem, key)
            if row is None:
                s.add(SettingItem(key=key, value=str(value or "")))
            else:
                row.value = str(value or "")
                s.add(row)
            s.commit()

    def set_many(self, items: dict[str, str]) -> None:
        if not items:
            return
        with Session(engine) as s:
            for key, value in items.items():
                row = s.get(SettingItem, key)
                if row is None:
                    s.add(SettingItem(key=key, value=str(value or "")))
                else:
                    row.value = str(value or "")
                    s.add(row)
            s.commit()

    def delete_many(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        with Session(engine) as s:
            for key in keys:
                row = s.get(SettingItem, key)
                if row is not None:
                    s.delete(row)
            s.commit()


settings = Settings()
