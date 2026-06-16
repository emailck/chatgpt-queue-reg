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
    from backend.models import openai_refresh_token  # noqa: F401
    from backend.models import email as _email_models  # noqa: F401
    from backend.models import job  # noqa: F401
    from backend.models import payment  # noqa: F401
    from backend.models import payment_card  # noqa: F401
    from backend.models import paypal_number  # noqa: F401
    from backend.models import pipeline  # noqa: F401
    from backend.models import pipeline_config  # noqa: F401
    from backend.models import proxy  # noqa: F401
    from backend.models import sms_project  # noqa: F401
    from backend.models import sub2api_binding  # noqa: F401


def init_db() -> None:
    _import_models()
    _migrate_codex_tokens_to_openai_refresh_tokens()
    SQLModel.metadata.create_all(engine)
    _migrate_openai_refresh_token_columns()
    _migrate_chatgpt_account_session_columns()
    _migrate_paypal_number_columns()
    _migrate_paypal_number_statuses()


def _sqlite_table_exists(conn, table_name: str) -> bool:
    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_columns(conn, table_name: str) -> set[str]:
    if not _sqlite_table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()}


def _migrate_codex_tokens_to_openai_refresh_tokens() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        has_old = _sqlite_table_exists(conn, "codex_tokens")
        has_new = _sqlite_table_exists(conn, "openai_refresh_tokens")
        if has_old and not has_new:
            conn.exec_driver_sql("ALTER TABLE codex_tokens RENAME TO openai_refresh_tokens")
            has_new = True
        if not has_new:
            return


def _migrate_openai_refresh_token_columns() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    columns = {
        "refresh_token": "VARCHAR DEFAULT ''",
        "oauth_access_token": "VARCHAR DEFAULT ''",
        "oauth_id_token": "VARCHAR DEFAULT ''",
        "oauth_access_expires_at": "DATETIME",
        "next_sync_at": "DATETIME",
        "last_sync_at": "DATETIME",
        "consecutive_failures": "INTEGER DEFAULT 0",
        "enabled": "BOOLEAN DEFAULT 1",
        "last_error": "VARCHAR DEFAULT ''",
        "sub2api_account_id": "VARCHAR DEFAULT ''",
        "sub2api_status": "VARCHAR DEFAULT 'pending_upload'",
        "sub2api_payload_json": "VARCHAR DEFAULT '{}'",
        "uploaded_at": "DATETIME",
        "status_checked_at": "DATETIME",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    legacy_map = {
        "oauth_access_token": "access_token",
        "oauth_id_token": "id_token",
        "oauth_access_expires_at": "expires_at",
        "next_sync_at": "next_refresh_at",
        "last_sync_at": "last_refreshed_at",
        "enabled": "alive",
        "sub2api_account_id": "sub2api_external_id",
    }
    with engine.begin() as conn:
        if not _sqlite_table_exists(conn, "openai_refresh_tokens"):
            return
        existing = _sqlite_columns(conn, "openai_refresh_tokens")
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE openai_refresh_tokens ADD COLUMN {name} {ddl}")
                existing.add(name)
        for target, source in legacy_map.items():
            if source in existing and target in existing:
                conn.exec_driver_sql(
                    f"UPDATE openai_refresh_tokens SET {target} = {source} "
                    f"WHERE ({target} IS NULL OR {target} = '') AND {source} IS NOT NULL AND {source} != ''"
                )
        if "alive" in existing and "enabled" in existing:
            conn.exec_driver_sql("UPDATE openai_refresh_tokens SET enabled = COALESCE(alive, enabled)")
        if _sqlite_table_exists(conn, "codex_tokens"):
            old_columns = _sqlite_columns(conn, "codex_tokens")
            required_old = {"id", "account_id", "refresh_token"}
            if required_old.issubset(old_columns):
                select_cols = {
                    "id": "id",
                    "account_id": "account_id",
                    "refresh_token": "refresh_token",
                    "oauth_access_token": "access_token" if "access_token" in old_columns else "''",
                    "oauth_id_token": "id_token" if "id_token" in old_columns else "''",
                    "oauth_access_expires_at": "expires_at" if "expires_at" in old_columns else "NULL",
                    "next_sync_at": "next_refresh_at" if "next_refresh_at" in old_columns else "NULL",
                    "last_sync_at": "last_refreshed_at" if "last_refreshed_at" in old_columns else "NULL",
                    "consecutive_failures": "consecutive_failures" if "consecutive_failures" in old_columns else "0",
                    "enabled": "alive" if "alive" in old_columns else "1",
                    "last_error": "last_error" if "last_error" in old_columns else "''",
                    "sub2api_account_id": "sub2api_external_id" if "sub2api_external_id" in old_columns else "''",
                    "sub2api_status": "sub2api_status" if "sub2api_status" in old_columns else "'pending_upload'",
                    "sub2api_payload_json": "sub2api_payload_json" if "sub2api_payload_json" in old_columns else "'{}'",
                    "uploaded_at": "uploaded_at" if "uploaded_at" in old_columns else "NULL",
                    "status_checked_at": "status_checked_at" if "status_checked_at" in old_columns else "NULL",
                    "created_at": "created_at" if "created_at" in old_columns else "CURRENT_TIMESTAMP",
                    "updated_at": "updated_at" if "updated_at" in old_columns else "CURRENT_TIMESTAMP",
                }
                target_cols = ", ".join(select_cols)
                source_exprs = ", ".join(select_cols.values())
                conn.exec_driver_sql(
                    f"INSERT OR IGNORE INTO openai_refresh_tokens ({target_cols}) "
                    f"SELECT {source_exprs} FROM codex_tokens"
                )


def _migrate_chatgpt_account_session_columns() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    columns = {
        "session_expires_at": "DATETIME",
        "session_refresh_status": "VARCHAR DEFAULT ''",
        "last_session_refresh_at": "DATETIME",
        "plan_type": "VARCHAR DEFAULT ''",
        "sold": "BOOLEAN DEFAULT 0",
        "sold_at": "DATETIME",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(chatgpt_accounts)").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE chatgpt_accounts ADD COLUMN {name} {ddl}")


def _migrate_paypal_number_columns() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    columns = {
        "otp_failure_count": "INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        if not _sqlite_table_exists(conn, "paypal_numbers"):
            return
        existing = _sqlite_columns(conn, "paypal_numbers")
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE paypal_numbers ADD COLUMN {name} {ddl}")



def _migrate_paypal_number_statuses() -> None:
    from sqlmodel import select

    from backend.models.paypal_number import (
        PAYPAL_NUMBER_LEGACY_TO_COOLING,
        PAYPAL_NUMBER_STATUS_COOLING,
        PayPalNumber,
    )

    with Session(engine) as s:
        rows = list(s.exec(
            select(PayPalNumber).where(PayPalNumber.status.in_(PAYPAL_NUMBER_LEGACY_TO_COOLING))
        ).all())
        if not rows:
            return
        for row in rows:
            row.status = PAYPAL_NUMBER_STATUS_COOLING
            s.add(row)
        s.commit()


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
