"""SQLite engine, session helpers, and `init_db()` orchestration.

Reuses the WAL/busy_timeout pattern from the legacy project but keeps only
what the queue project needs.  Models are declared in `backend.models.*` and
imported lazily via `_import_models()` so circular imports stay easy.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chatgpt_queue.db")
SQLITE_BUSY_TIMEOUT_SECONDS = float(os.getenv("SQLITE_BUSY_TIMEOUT_SECONDS", "30"))


def _create_engine():
    kwargs: dict = {}
    if DATABASE_URL.startswith("sqlite"):
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        }
    return create_engine(DATABASE_URL, **kwargs)


engine = _create_engine()


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    if engine.url.get_backend_name() != "sqlite":
        return
    timeout_ms = int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)
    cur = dbapi_connection.cursor()
    try:
        cur.execute(f"PRAGMA busy_timeout={timeout_ms}")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        cur.close()


def _import_models() -> None:
    # Importing the model packages registers the SQLModel tables on metadata.
    from backend.models import access_token  # noqa: F401
    from backend.models import account  # noqa: F401
    from backend.models import browser_session  # noqa: F401
    from backend.models import codex_token  # noqa: F401
    from backend.models import email as _email_models  # noqa: F401
    from backend.models import job  # noqa: F401
    from backend.models import payment  # noqa: F401
    from backend.models import payment_card  # noqa: F401
    from backend.models import paypal_number  # noqa: F401
    from backend.models import pipeline  # noqa: F401
    from backend.models import proxy  # noqa: F401
    from backend.models import sms_project  # noqa: F401


def init_db() -> None:
    _import_models()
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager yielding a Session that commits on success."""
    s = Session(engine)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as s:
        yield s
