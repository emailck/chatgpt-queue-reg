"""Microsoft email service adapted for the ChatGPT registration engine.

The legacy engine expects something with:

  - `service_type.value`
  - `create_email() -> {"email": ...}` (used to take a free mailbox from the pool)
  - `get_verification_code(email, *, keyword="", timeout=...) -> str | None`

We satisfy that surface by pulling enabled rows from `email_accounts` and
delegating OTP retrieval to `MicrosoftMailbox`.

Each call to `get_verification_code()` snapshots the current UTC timestamp
(minus a small grace window) as the OTP request moment.  The mailbox poll
then asks Graph for `receivedDateTime ge <since>` only — older inbox
messages (e.g. a previous run's expired OTP) are filtered out server-side.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.settings import settings
from backend.integrations.mail.microsoft import MicrosoftMailbox, wait_for_otp
from backend.integrations.mail.pool import claim as pool_claim
from backend.models.email import EmailAccount, EmailMessage

logger = logging.getLogger(__name__)

OTP_REQUEST_GRACE_SECONDS = 30  # tolerate small clock drift between us and Graph


@dataclass
class _ServiceType:
    value: str = "microsoft"


class MicrosoftEmailService:
    service_type = _ServiceType()

    def __init__(self, *, extra_config: dict[str, Any] | None = None) -> None:
        self._extra = dict(extra_config or {})
        self._lock = threading.Lock()
        self._claimed_account_id: int | None = None
        self._claimed_email: str | None = None
        self._fixed_email = str(self._extra.get("fixed_email") or "").strip()

    @property
    def claimed_email(self) -> str | None:
        return self._claimed_email

    # -- API expected by the legacy engine ---------------------------------

    def create_email(self) -> dict[str, str]:
        with self._lock:
            account = pool_claim(fixed_email=self._fixed_email or None)
            if account is None:
                raise RuntimeError(
                    "微软邮箱账号池为空，请先在“邮箱”页导入"
                    if not self._fixed_email
                    else f"指定邮箱 {self._fixed_email} 不在启用池中"
                )
            self._claimed_account_id = int(account.id or 0)
            self._claimed_email = account.email
            return {"email": account.email}

    def get_verification_code(
        self,
        email: str,
        *,
        keyword: str = "",
        timeout: int = 300,
        code_pattern: str | None = None,
        **_kwargs: Any,
    ) -> str | None:
        # The caller invokes this method right after triggering the OTP send,
        # so "now" is the request timestamp.  Drop a grace window for clock
        # drift and use it as the server-side $filter cutoff.
        request_dt = datetime.now(timezone.utc) - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS)
        since_iso = request_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        with Session(engine) as s:
            account = s.exec(
                __import__("sqlalchemy", fromlist=["select"])
                .select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.email == email)
            ).scalars().first()
        if account is None:
            logger.warning("email service: %s not found in pool", email)
            return None

        meta = json_loads(account.metadata_json, fallback={}) or {}
        client_id = str(meta.get("client_id") or "")
        refresh_token = account.refresh_token

        mailbox = MicrosoftMailbox()
        try:
            data = wait_for_otp(
                mailbox=mailbox,
                client_id=client_id,
                refresh_token=refresh_token,
                keyword=keyword,
                code_pattern=code_pattern,
                timeout=int(timeout or 300),
                poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                log=lambda msg: logger.info("[email %s] %s", email, msg),
                since_iso=since_iso,
            )
        except TimeoutError:
            return None
        except Exception as exc:
            logger.warning("email OTP fetch error: %s", exc)
            return None

        _persist_message(account, data)
        return str(data.get("code") or "") or None

    # -- helpers -----------------------------------------------------------


def _persist_message(account: EmailAccount, data: dict[str, Any]) -> None:
    with Session(engine) as s:
        s.add(EmailMessage(
            account_id=account.id,
            email=account.email,
            provider="microsoft",
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=__import__("json").dumps(data.get("raw") or {}, ensure_ascii=False, default=str),
        ))
        s.commit()
