"""email_read flow.

Triggered from the UI's "收邮件" button.  Pulls the OTP-bearing message that
arrived after the moment we were called (the user typically clicks this
right after a re-send-OTP) and writes a row into `email_messages`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from backend.core.constants import JOB_TYPE_EMAIL_READ
from backend.core.db import engine, session_scope
from backend.core.flow_registry import register_flow
from backend.core.job_context import JobContext
from backend.core.json_utils import json_dumps, json_loads
from backend.integrations.mail.microsoft import MicrosoftMailbox, wait_for_otp
from backend.models.email import EmailAccount, EmailMessage


# tolerate a small clock drift between us and Microsoft Graph
OTP_REQUEST_GRACE_SECONDS = 30


def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    email = str(payload.get("email") or "").strip()
    keyword = str(payload.get("keyword") or "")
    code_pattern = payload.get("code_regex") or None
    timeout = int(payload.get("timeout_seconds") or 120)
    poll_interval = float(payload.get("poll_interval_seconds") or 5)
    # Caller may pin the OTP-request moment (e.g. when this flow is enqueued
    # immediately before the OTP send).  Otherwise we use "now".
    explicit_since = str(payload.get("since_iso") or "").strip()

    if not email:
        raise RuntimeError("email_read flow requires an `email` field in input")

    with Session(engine) as s:
        row = (
            s.exec(
                __import__("sqlalchemy", fromlist=["select"])
                .select(EmailAccount)
                .where(EmailAccount.provider == "microsoft")
                .where(EmailAccount.email == email)
            ).scalars().first()
        )
    if row is None:
        raise RuntimeError(f"email {email} is not in the Microsoft pool")

    meta = json_loads(row.metadata_json, fallback={}) or {}
    client_id = str(meta.get("client_id") or "")
    refresh_token = row.refresh_token
    if not (client_id and refresh_token):
        raise RuntimeError(f"email {email} is missing OAuth client_id/refresh_token")

    if explicit_since:
        since_iso = explicit_since
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS)
        since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    ctx.log(
        f"reading microsoft mailbox {email}",
        payload={"keyword": keyword, "timeout": timeout, "since_iso": since_iso},
    )

    mailbox = MicrosoftMailbox()

    def _emit(msg: str) -> None:
        ctx.log(str(msg or ""))

    data = wait_for_otp(
        mailbox=mailbox,
        client_id=client_id,
        refresh_token=refresh_token,
        keyword=keyword,
        code_pattern=code_pattern,
        timeout=timeout,
        poll_interval=poll_interval,
        log=_emit,
        since_iso=since_iso,
    )

    with session_scope() as s:
        s.add(EmailMessage(
            account_id=row.id,
            job_id=ctx.job_id,
            email=row.email,
            provider="microsoft",
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=json_dumps(data.get("raw") or {}),
        ))

    ctx.update_result({
        "email": email,
        "code": data.get("code"),
        "subject": data.get("subject"),
        "sender": data.get("sender"),
        "received_at": data.get("received_at"),
    })


register_flow(JOB_TYPE_EMAIL_READ, run)
