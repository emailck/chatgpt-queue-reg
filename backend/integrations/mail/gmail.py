"""Gmail IMAP mailbox support.

Import format:
  alias@gmail.com----gmail_app_password

For Gmail dot aliases, login uses the canonical address with dots removed from
local-part (and plus suffix removed), while message filtering keeps the imported
alias as target email.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.settings import settings
from backend.models.email import EmailAccount, EmailMessage
from backend.integrations.mail import imap163 as common

DEFAULT_HOST = "imap.gmail.com"
DEFAULT_PORT = 993
PROVIDER = "gmail_imap"


def canonical_gmail_address(addr: str) -> str:
    email = str(addr or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if domain not in {"gmail.com", "googlemail.com"}:
        return email
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@gmail.com"


def build_metadata(*, alias_email: str, app_password: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict[str, Any]:
    return {
        "account_type": "gmail_imap",
        "imap_host": host,
        "imap_port": int(port),
        "imap_auth_code": str(app_password or "").strip().replace(" ", ""),
        "imap_login_email": canonical_gmail_address(alias_email),
        "alias_email": str(alias_email or "").strip(),
    }


def get_gmail_account(addr: str) -> EmailAccount:
    value = str(addr or "").strip()
    with Session(engine) as s:
        row = s.exec(
            sa_select(EmailAccount)
            .where(EmailAccount.provider == PROVIDER)
            .where(EmailAccount.email == value)
        ).scalars().first()
        if row is None:
            raise RuntimeError(f"Gmail 邮箱不在池中: {value}")
        s.expunge(row)
        return row


def list_gmail_messages(row: EmailAccount, *, limit: int = 10) -> list[dict[str, Any]]:
    meta = json_loads(row.metadata_json, fallback={}) or {}
    host = str(meta.get("imap_host") or DEFAULT_HOST)
    port = int(meta.get("imap_port") or DEFAULT_PORT)
    auth_code = str(meta.get("imap_auth_code") or row.refresh_token or "").strip().replace(" ", "")
    login_email = str(meta.get("imap_login_email") or canonical_gmail_address(row.email)).strip()
    if not auth_code:
        raise RuntimeError("Gmail 缺少 App Password")
    with common._imap_login(login_email, auth_code, host, port) as client:  # noqa: SLF001
        client.select("INBOX", readonly=True)
        typ, payload = client.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError(f"Gmail IMAP search failed: {typ}")
        ids = (payload[0] or b"").split()[-max(1, int(limit or 10)):]
        out: list[dict[str, Any]] = []
        for msg_id in reversed(ids):
            msg = common._fetch_message(client, msg_id)  # noqa: SLF001
            data = common._message_to_data(row.email, msg, raw_id=msg_id.decode(errors="ignore"))  # noqa: SLF001
            data["provider"] = PROVIDER
            data["email"] = row.email
            out.append(data)
        return out


def wait_for_gmail_otp(
    row: EmailAccount,
    *,
    keyword: str = "",
    code_pattern: str | None = None,
    timeout: int = 180,
    poll_interval: float = 5.0,
    since_dt: datetime | None = None,
    exclude_codes=None,
) -> dict[str, Any]:
    import time
    deadline = time.time() + max(1, int(timeout or 180))
    excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
    last_error = ""
    while time.time() < deadline:
        try:
            for data in list_gmail_messages(row, limit=30):
                received = common._parse_dt(data.get("received_at"))  # noqa: SLF001
                if since_dt is not None and received is not None and received < since_dt.astimezone(timezone.utc):
                    continue
                hay = f"{data.get('subject') or ''}\n{data.get('sender') or ''}\n{data.get('body_text') or ''}"
                if keyword and keyword.lower() not in hay.lower():
                    continue
                code = common._extract_code(hay, code_pattern=code_pattern, exclude_codes=excluded)  # noqa: SLF001
                if code:
                    data["code"] = code
                    return data
        except Exception as exc:
            last_error = str(exc)
        time.sleep(float(poll_interval or 5.0))
    raise TimeoutError(f"Gmail IMAP OTP not received within {timeout}s" + (f": {last_error}" if last_error else ""))


def persist_gmail_message(row: EmailAccount, data: dict[str, Any]) -> None:
    import json
    with Session(engine) as s:
        s.add(EmailMessage(
            account_id=row.id,
            email=row.email,
            provider=PROVIDER,
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=json.dumps(data.get("raw") or {}, ensure_ascii=False, default=str),
            received_at=common._parse_dt(data.get("received_at")),  # noqa: SLF001
        ))
        s.commit()


class GmailEmailService:
    service_type = type("_ServiceType", (), {"value": PROVIDER})()

    def __init__(self, *, extra_config: dict[str, Any] | None = None) -> None:
        self._extra = dict(extra_config or {})
        self._fixed_email = str(self._extra.get("fixed_email") or "").strip()
        self._claimed_email: str | None = None

    @property
    def claimed_email(self) -> str | None:
        return self._claimed_email

    def create_email(self) -> dict[str, str]:
        from backend.integrations.mail.pool import claim as pool_claim
        row = pool_claim(
            fixed_email=self._fixed_email or None,
            provider=PROVIDER,
            wait_seconds=float(settings.get_int("gmail_same_mailbox_claim_wait_seconds", 1800)),
            poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
        )
        if row is None:
            raise RuntimeError("Gmail 邮箱账号池为空，请先导入" if not self._fixed_email else f"指定邮箱 {self._fixed_email} 不在启用池中")
        self._claimed_email = row.email
        return {"email": row.email}

    def get_verification_code(self, email: str, *, keyword: str = "", timeout: int = 300, code_pattern: str | None = None, otp_sent_at: float | datetime | None = None, exclude_codes=None, **_kwargs: Any) -> str | None:
        row = get_gmail_account(email)
        request_dt = common._otp_request_datetime(otp_sent_at)  # noqa: SLF001
        try:
            data = wait_for_gmail_otp(
                row,
                keyword=keyword,
                code_pattern=code_pattern,
                timeout=int(timeout or 300),
                poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                since_dt=request_dt - timedelta(seconds=30),
                exclude_codes=exclude_codes,
            )
        except TimeoutError:
            return None
        except Exception:
            return None
        persist_gmail_message(row, data)
        return str(data.get("code") or "") or None
