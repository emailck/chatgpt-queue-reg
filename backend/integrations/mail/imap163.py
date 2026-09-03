"""163/NetEase IMAP mailbox support.

Import format:
  email@163.com----login_password----imap_authorization_code

The third segment is the IMAP/SMTP authorization code. We read via IMAP SSL
imap.163.com:993.
"""
from __future__ import annotations

import email as email_pkg
import html
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.settings import settings
from backend.models.email import EmailAccount, EmailMessage

DEFAULT_HOST = "imap.163.com"
DEFAULT_PORT = 993
_DEFAULT_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SEMANTIC_CODE_RES = (
    # Prefer real prose around the OTP.  OpenAI emails contain CSS colors such
    # as #202123/#353740 before the actual code, so never trust the first raw
    # six digits in an HTML document.
    re.compile(r"(?is)(?:enter\s+this\s+code|use\s+this\s+code|your\s+(?:temporary\s+)?(?:chatgpt\s+)?verification\s+code(?:\s+is)?|verification\s+code(?:\s+is)?|one[-\s]*time\s+(?:password|code)|security\s+code|login\s+code|验证码|校验码|动态码|認證碼|驗證碼)[^0-9]{0,220}(\d{6})"),
    re.compile(r"(?is)\bcode\b[^0-9]{0,120}(\d{6})"),
)
_STYLE_NUMBER_RE = re.compile(r"(?i)(?<![a-z0-9])#(?:202123|353740|[0-9a-f]{6})(?![a-z0-9])")
_URL_RE = re.compile(r"(?is)https?://\S+")


def is_163_address(addr: str) -> bool:
    return str(addr or "").strip().lower().endswith("@163.com")


def build_metadata(*, auth_code: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict[str, Any]:
    return {
        "account_type": "netease_163_imap",
        "imap_host": host,
        "imap_port": int(port),
        "imap_auth_code": str(auth_code or "").strip(),
    }


class NetEase163EmailService:
    service_type = type("_ServiceType", (), {"value": "netease_163"})()

    def __init__(self, *, extra_config: dict[str, Any] | None = None) -> None:
        self._extra = dict(extra_config or {})
        self._fixed_email = str(self._extra.get("fixed_email") or "").strip()
        self._claimed_email: str | None = None

    @property
    def claimed_email(self) -> str | None:
        return self._claimed_email

    def create_email(self) -> dict[str, str]:
        from backend.integrations.mail.pool import claim as pool_claim
        row = pool_claim(fixed_email=self._fixed_email or None, provider="netease_163")
        if row is None:
            raise RuntimeError("163 邮箱账号池为空，请先导入" if not self._fixed_email else f"指定邮箱 {self._fixed_email} 不在启用池中")
        self._claimed_email = row.email
        return {"email": row.email}

    def get_verification_code(
        self,
        email: str,
        *,
        keyword: str = "",
        timeout: int = 300,
        code_pattern: str | None = None,
        otp_sent_at: float | datetime | None = None,
        exclude_codes: set[str] | list[str] | tuple[str, ...] | None = None,
        **_kwargs: Any,
    ) -> str | None:
        skip_initial_delay = bool(_kwargs.get("skip_initial_delay"))
        initial_delay = max(0, min(settings.get_int("email_otp_initial_delay_seconds", 5), 60))
        if initial_delay and not skip_initial_delay:
            time.sleep(initial_delay)
        row = get_163_account(email)
        request_dt = _otp_request_datetime(otp_sent_at)
        try:
            data = wait_for_163_otp(
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
        persist_163_message(row, data)
        return str(data.get("code") or "") or None


def get_163_account(addr: str) -> EmailAccount:
    value = str(addr or "").strip()
    with Session(engine) as s:
        row = s.exec(
            sa_select(EmailAccount)
            .where(EmailAccount.provider == "netease_163")
            .where(EmailAccount.email == value)
        ).scalars().first()
        if row is None:
            raise RuntimeError(f"163 邮箱不在池中: {value}")
        s.expunge(row)
        return row


def list_163_messages(row: EmailAccount, *, limit: int = 10) -> list[dict[str, Any]]:
    meta = json_loads(row.metadata_json, fallback={}) or {}
    host = str(meta.get("imap_host") or DEFAULT_HOST)
    port = int(meta.get("imap_port") or DEFAULT_PORT)
    auth_code = str(meta.get("imap_auth_code") or row.refresh_token or "").strip()
    if not auth_code:
        raise RuntimeError("163 邮箱缺少授权码")
    with _imap_login(row.email, auth_code, host, port) as client:
        client.select("INBOX", readonly=True)
        typ, payload = client.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")
        ids = (payload[0] or b"").split()[-max(1, int(limit or 10)):]
        out: list[dict[str, Any]] = []
        for msg_id in reversed(ids):
            msg = _fetch_message(client, msg_id)
            out.append(_message_to_data(row.email, msg, raw_id=msg_id.decode(errors="ignore")))
        return out


def wait_for_163_otp(
    row: EmailAccount,
    *,
    keyword: str = "",
    code_pattern: str | None = None,
    timeout: int = 180,
    poll_interval: float = 5.0,
    since_dt: datetime | None = None,
    exclude_codes=None,
) -> dict[str, Any]:
    deadline = time.time() + max(1, int(timeout or 180))
    excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
    last_error = ""
    while time.time() < deadline:
        try:
            for data in list_163_messages(row, limit=30):
                received = _parse_dt(data.get("received_at"))
                if since_dt is not None and received is not None and received < since_dt.astimezone(timezone.utc):
                    continue
                hay = f"{data.get('subject') or ''}\n{data.get('sender') or ''}\n{data.get('body_text') or ''}"
                if keyword and keyword.lower() not in hay.lower():
                    continue
                code = _extract_code(hay, code_pattern=code_pattern, exclude_codes=excluded)
                if code:
                    data["code"] = code
                    return data
        except Exception as exc:
            last_error = str(exc)
        time.sleep(float(poll_interval or 5.0))
    raise TimeoutError(f"163 IMAP OTP not received within {timeout}s" + (f": {last_error}" if last_error else ""))


def persist_163_message(row: EmailAccount, data: dict[str, Any]) -> None:
    with Session(engine) as s:
        s.add(EmailMessage(
            account_id=row.id,
            email=row.email,
            provider="netease_163",
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=__import__("json").dumps(data.get("raw") or {}, ensure_ascii=False, default=str),
            received_at=_parse_dt(data.get("received_at")),
        ))
        s.commit()


class _IMAPSession:
    def __init__(self, client: imaplib.IMAP4_SSL):
        self.client = client
    def __enter__(self):
        return self.client
    def __exit__(self, *_exc):
        try:
            self.client.logout()
        except Exception:
            pass


def _imap_login(email_addr: str, auth_code: str, host: str, port: int) -> _IMAPSession:
    client = imaplib.IMAP4_SSL(host, port, timeout=30)
    client.login(email_addr, auth_code)

    # NetEase/163 sometimes accepts LOGIN but then refuses SELECT/EXAMINE with
    # "Unsafe Login" unless the client identifies itself.  This mirrors the
    # official sample-style ID command used by many 163 IMAP clients.
    imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")
    try:
        client._simple_command(
            "ID",
            '("name" "moltbot" "version" "0.0.1" "vendor" "netease" "support-email" "kefu@188.com")',
        )
    except Exception:
        pass
    return _IMAPSession(client)


def _fetch_message(client: imaplib.IMAP4_SSL, msg_id: bytes) -> Message:
    typ, payload = client.fetch(msg_id, "(RFC822)")
    if typ != "OK" or not payload:
        raise RuntimeError(f"IMAP fetch failed: {typ}")
    raw = b""
    for item in payload:
        if isinstance(item, tuple):
            raw += item[1] or b""
    return email_pkg.message_from_bytes(raw)


def _message_to_data(target_email: str, msg: Message, *, raw_id: str = "") -> dict[str, Any]:
    subject = _decode_header(msg.get("Subject", ""))
    sender = _decode_header(msg.get("From", ""))
    body = _extract_body(msg)
    received_at = _message_datetime(msg)
    return {
        "id": raw_id,
        "email": target_email,
        "provider": "netease_163",
        "subject": subject,
        "sender": sender,
        "body_text": body,
        "code": _extract_code(f"{subject}\n{body}"),
        "received_at": received_at.isoformat() if received_at else None,
        "created_at": None,
        "folder": "INBOX",
        "raw": {"message_id": msg.get("Message-ID", ""), "date": msg.get("Date", "")},
    }


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def _extract_body(msg: Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype not in {"text/plain", "text/html"}:
                continue
            text = _payload_text(part)
            # Some 163/OpenAI messages put HTML markup inside a text/plain part,
            # so normalize every textual part, not only declared text/html.
            text = _html_to_visible_text(text)
            if text:
                parts.append(text)
    else:
        parts.append(_html_to_visible_text(_payload_text(msg)))
    return re.sub(r"\s+", " ", "\n".join(parts)).strip()


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return str(part.get_payload() or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


def _message_datetime(msg: Message) -> datetime | None:
    val = msg.get("Date")
    if val:
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _otp_request_datetime(value: float | datetime | None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value is not None:
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _html_to_visible_text(text: str) -> str:
    value = str(text or "")
    # Drop non-visible CSS/JS first; otherwise OpenAI colors like #202123 can
    # be mistaken for a 6-digit verification code.
    value = re.sub(r"(?is)<!--.*?-->", " ", value)
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", value)
    value = _STYLE_NUMBER_RE.sub(" ", value)
    value = _URL_RE.sub(" ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = _STYLE_NUMBER_RE.sub(" ", value)
    value = _URL_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_code(text: str, *, code_pattern: str | None = None, exclude_codes=None) -> str:
    excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
    visible = _html_to_visible_text(str(text or ""))
    search_spaces = [visible]
    # Custom patterns are caller-owned, but still run them over visible text
    # first to avoid matching HTML/CSS artifacts.
    patterns = [re.compile(code_pattern)] if code_pattern else list(_SEMANTIC_CODE_RES)
    for pat in patterns:
        for hay in search_spaces:
            for m in pat.finditer(hay):
                code = next((g for g in m.groups() if g), m.group(0))
                code = re.sub(r"\D", "", str(code or ""))
                if len(code) == 6 and code not in excluded and not _looks_like_template_number(code):
                    return code

    # Last resort: choose the first plausible standalone 6-digit number from
    # visible text, after excluding known template/style/link numbers.
    for m in _DEFAULT_CODE_RE.finditer(visible):
        code = m.group(1)
        if code not in excluded and not _looks_like_template_number(code):
            return code
    return ""


def _looks_like_template_number(code: str) -> bool:
    # OpenAI email template colors / SendGrid tracking host fragments that are
    # not OTPs. Keep this deliberately small; primary defense is HTML cleanup.
    return code in {"202123", "353740", "202167"}
