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

import html
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

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

    def peek_current_code(self, email: str) -> str:
        """Get the current direct HTTP code for mailapi_url accounts only.

        This is intentionally limited to direct-code HTTP providers.  IMAP and
        mailbox-list providers should keep their normal received-time filtering.
        """
        with Session(engine) as s:
            account = s.exec(
                __import__("sqlalchemy", fromlist=["select"])
                .select(EmailAccount)
                .where(EmailAccount.email == email)
            ).scalars().first()
        if account is None:
            return ""
        meta = json_loads(account.metadata_json, fallback={}) or {}
        account_type = str(meta.get("account_type") or "").strip().lower()
        mailapi_url = str(meta.get("mailapi_url") or "").strip()
        if account_type != "mailapi_url" and not mailapi_url:
            return ""
        if not mailapi_url:
            return ""
        try:
            return _direct_mailapi_code(_fetch_mailapi_payload(mailapi_url))
        except Exception as exc:
            logger.info("[email %s] mailapi prefetch current code failed: %s", email, exc)
            return ""

    # -- API expected by the legacy engine ---------------------------------

    def create_email(self) -> dict[str, str]:
        with self._lock:
            account = pool_claim(
                fixed_email=self._fixed_email or None,
                provider="any",
                wait_seconds=float(settings.get_int("gmail_same_mailbox_claim_wait_seconds", 1800)),
                poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
            )
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
        otp_sent_at: float | datetime | None = None,
        exclude_codes: set[str] | list[str] | tuple[str, ...] | None = None,
        **_kwargs: Any,
    ) -> str | None:
        skip_initial_delay = bool(_kwargs.get("skip_initial_delay"))
        initial_delay = max(0, min(settings.get_int("email_otp_initial_delay_seconds", 5), 60))
        if initial_delay and not skip_initial_delay:
            logger.info("[email %s] OTP fetch initial delay %ss before polling", email, initial_delay)
            time.sleep(initial_delay)
        request_dt = _otp_request_datetime(otp_sent_at)
        since_iso = (request_dt - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")

        with Session(engine) as s:
            account = s.exec(
                __import__("sqlalchemy", fromlist=["select"])
                .select(EmailAccount)
                .where(EmailAccount.email == email)
            ).scalars().first()
        if account is None:
            logger.warning("email service: %s not found in pool", email)
            return None

        meta = json_loads(account.metadata_json, fallback={}) or {}

        if str(account.provider or "") == "netease_163":
            try:
                from backend.integrations.mail.imap163 import wait_for_163_otp, persist_163_message
                data = wait_for_163_otp(
                    account,
                    keyword=keyword,
                    code_pattern=code_pattern,
                    timeout=int(timeout or 300),
                    poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                    since_dt=request_dt - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS),
                    exclude_codes=exclude_codes,
                )
            except TimeoutError:
                return None
            except Exception as exc:
                logger.warning("163 IMAP OTP fetch error: %s", exc)
                return None
            persist_163_message(account, data)
            return str(data.get("code") or "") or None

        if str(account.provider or "") == "gmail_imap":
            try:
                from backend.integrations.mail.gmail import wait_for_gmail_otp, persist_gmail_message
                data = wait_for_gmail_otp(
                    account,
                    keyword=keyword,
                    code_pattern=code_pattern,
                    timeout=int(timeout or 300),
                    poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                    since_dt=request_dt - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS),
                    exclude_codes=exclude_codes,
                )
            except TimeoutError:
                return None
            except Exception as exc:
                logger.warning("Gmail IMAP OTP fetch error: %s", exc)
                return None
            persist_gmail_message(account, data)
            return str(data.get("code") or "") or None

        if str(account.provider or "") == "tinkmail":
            try:
                from backend.integrations.mail.tinkmail import wait_for_tinkmail_otp, persist_tinkmail_message
                data = wait_for_tinkmail_otp(
                    account,
                    keyword=keyword,
                    code_pattern=code_pattern,
                    timeout=int(timeout or 300),
                    poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                    since_dt=request_dt - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS),
                    exclude_codes=exclude_codes,
                    log=lambda msg: logger.info("[email %s] %s", email, msg),
                )
            except TimeoutError:
                return None
            except Exception as exc:
                logger.warning("TinkMail OTP fetch error: %s", exc)
                return None
            persist_tinkmail_message(account, data)
            return str(data.get("code") or "") or None

        account_type = str(meta.get("account_type") or "").strip().lower()
        mailapi_url = str(meta.get("mailapi_url") or "").strip()

        if account_type == "mailapi_url" or mailapi_url:
            if not mailapi_url:
                logger.warning("mailapi OTP fetch error: %s missing mailapi_url in metadata", email)
                return None
            try:
                data = wait_for_mailapi_otp(
                    mailapi_url=mailapi_url,
                    keyword=keyword,
                    code_pattern=code_pattern,
                    timeout=int(timeout or 300),
                    poll_interval=float(settings.get_int("email_poll_interval_seconds", 5)),
                    log=lambda msg: logger.info("[email %s] %s", email, msg),
                    since_dt=request_dt - timedelta(seconds=OTP_REQUEST_GRACE_SECONDS),
                    exclude_codes=exclude_codes,
                )
            except TimeoutError:
                return None
            except Exception as exc:
                logger.warning("mailapi OTP fetch error: %s", exc)
                return None
            _persist_message(account, data)
            return str(data.get("code") or "") or None

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
                exclude_codes=exclude_codes,
            )
        except TimeoutError:
            return None
        except Exception as exc:
            logger.warning("email OTP fetch error: %s", exc)
            return None

        _persist_message(account, data)
        return str(data.get("code") or "") or None

    # -- helpers -----------------------------------------------------------


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


def _persist_message(account: EmailAccount, data: dict[str, Any]) -> None:
    with Session(engine) as s:
        s.add(EmailMessage(
            account_id=account.id,
            email=account.email,
            provider=str(account.provider or "microsoft"),
            subject=str(data.get("subject") or ""),
            sender=str(data.get("sender") or ""),
            body_text=str(data.get("body_text") or ""),
            code=str(data.get("code") or ""),
            raw_json=__import__("json").dumps(data.get("raw") or {}, ensure_ascii=False, default=str),
        ))
        s.commit()


# ---- mailapi_url polling ----------------------------------------------------

_INITIAL_PAYLOAD_RE = re.compile(r"const\s+INITIAL_PAYLOAD\s*=\s*(\{.*?\})\s*;", re.DOTALL)
_DEFAULT_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SEMANTIC_CODE_RES = (
    re.compile(r"(?is)(?:verification\s+code|temporary\s+verification\s+code|one[-\s]*time\s+(?:password|code)|security\s+code|login\s+code|temporary\s+code|验证码|校验码|动态码|临时代码|登录代码|認證碼|驗證碼)[^0-9]{0,80}(\d{6})"),
    re.compile(r"(?is)\bcode\b[^0-9]{0,40}(\d{6})"),
)
_MAIL_CARD_RE = re.compile(
    r"<article\b[^>]*class=[\"'][^\"']*\bmail-card\b[^\"']*[\"'][^>]*>(.*?)</article>",
    re.IGNORECASE | re.DOTALL,
)
_MAIL_FIELD_RES = {
    "subject": re.compile(r"<span\b[^>]*class=[\"'][^\"']*\bsubject\b[^\"']*[\"'][^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL),
    "date": re.compile(r"<span\b[^>]*class=[\"'][^\"']*\bdate\b[^\"']*[\"'][^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL),
    "meta": re.compile(r"<div\b[^>]*class=[\"'][^\"']*\bmeta\b[^\"']*[\"'][^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL),
    "body": re.compile(r"<pre\b[^>]*class=[\"'][^\"']*\bbody\b[^\"']*[\"'][^>]*>(.*?)</pre>", re.IGNORECASE | re.DOTALL),
}


def wait_for_mailapi_otp(
    *,
    mailapi_url: str,
    keyword: str = "",
    code_pattern: str | None = None,
    timeout: int = 180,
    poll_interval: float = 5.0,
    log=None,
    since_dt: datetime | None = None,
    exclude_codes=None,
) -> dict[str, Any]:
    if not mailapi_url:
        raise RuntimeError("missing mailapi_url")
    deadline = time.time() + max(1, int(timeout or 180))
    excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
    last_error = ""
    while time.time() < deadline:
        try:
            payload = _fetch_mailapi_payload(mailapi_url)
            data = _select_mailapi_otp(payload, keyword=keyword, code_pattern=code_pattern, since_dt=since_dt, exclude_codes=excluded)
            if data:
                if log:
                    log(f"mailapi received OTP from {data.get('sender') or data.get('from') or '-'} subject={data.get('subject')!r}")
                return data
            if log:
                count = len((payload or {}).get("emails") or []) if isinstance(payload, dict) else 0
                log(f"mailapi no matching OTP yet; emails={count}; polling again")
        except Exception as exc:
            last_error = str(exc)
            if log:
                log(f"mailapi poll error: {last_error}")
        time.sleep(float(poll_interval or 5.0))
    raise TimeoutError(f"mailapi OTP not received within {timeout}s" + (f": {last_error}" if last_error else ""))


def _fetch_mailapi_payload(mailapi_url: str) -> dict[str, Any]:
    resp = requests.get(mailapi_url, headers={"Accept": "application/json,text/html,*/*", "User-Agent": "Mozilla/5.0"}, timeout=25)
    resp.raise_for_status()
    text = resp.text or ""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        body = resp.json()
        return _normalize_mailapi_payload(body, mailapi_url=mailapi_url)
    match = _INITIAL_PAYLOAD_RE.search(text)
    if match:
        return _normalize_mailapi_payload(json.loads(html.unescape(match.group(1))), mailapi_url=mailapi_url)
    # Some deployments expose raw JSON even with text/html.
    stripped = text.strip()
    if stripped.startswith("{"):
        return _normalize_mailapi_payload(json.loads(stripped), mailapi_url=mailapi_url)

    # Some mailbox dashboards render HTML by default and expose the same latest
    # message as JSON via `format=json` (e.g. QQ mailbox gateway /v1/messages).
    json_url = _mailapi_json_url(mailapi_url)
    if json_url and json_url != mailapi_url:
        try:
            json_resp = requests.get(json_url, headers={"Accept": "application/json,*/*", "User-Agent": "Mozilla/5.0"}, timeout=25)
            json_resp.raise_for_status()
            json_text = (json_resp.text or "").strip()
            if "application/json" in (json_resp.headers.get("content-type") or "").lower() or json_text.startswith("{"):
                return _normalize_mailapi_payload(json_resp.json() if hasattr(json_resp, "json") else json.loads(json_text), mailapi_url=json_url)
        except Exception:
            pass

    html_payload = _parse_mailapi_html(text, mailapi_url=mailapi_url)
    if html_payload.get("emails") or re.search(r"(?is)<(?:!doctype\s+html|html)\b", stripped):
        return html_payload
    raise RuntimeError("mailapi response missing INITIAL_PAYLOAD/json/html messages")


def _mailapi_json_url(mailapi_url: str) -> str:
    try:
        parts = urlsplit(str(mailapi_url or "").strip())
        if not parts.scheme or not parts.netloc:
            return ""
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if str(query.get("format") or "").lower() == "json":
            return mailapi_url
        query["format"] = "json"
        return urlunsplit(parts._replace(query=urlencode(query)))
    except Exception:
        return ""


def _normalize_mailapi_payload(body: Any, *, mailapi_url: str = "") -> dict[str, Any]:
    if isinstance(body, list):
        return {"emails": [_normalize_mailapi_message(item, mailapi_url=mailapi_url) for item in body if isinstance(item, dict)]}
    if not isinstance(body, dict):
        return {"emails": []}

    if isinstance(body.get("message"), dict):
        return {
            "emails": [_normalize_mailapi_message(body["message"], mailapi_url=mailapi_url, envelope=body)],
            "raw": body,
        }

    for key in ("emails", "messages", "items", "list"):
        value = body.get(key)
        if isinstance(value, list):
            out = dict(body)
            out["emails"] = [_normalize_mailapi_message(item, mailapi_url=mailapi_url, envelope=body) for item in value if isinstance(item, dict)]
            return out

    data = body.get("data")
    if isinstance(data, list):
        out = dict(body)
        out["emails"] = [_normalize_mailapi_message(item, mailapi_url=mailapi_url, envelope=body) for item in data if isinstance(item, dict)]
        return out
    if isinstance(data, dict) and any(k in data for k in ("subject", "body", "body_text", "body_html", "from", "date")):
        out = dict(body)
        out["emails"] = [_normalize_mailapi_message(data, mailapi_url=mailapi_url, envelope=body)]
        return out

    return body


def _normalize_mailapi_message(item: dict[str, Any], *, mailapi_url: str = "", envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = dict(item or {})
    body_text = str(msg.get("body") or msg.get("body_text") or "")
    body_html = str(msg.get("body_html") or msg.get("html") or "")
    if not body_text and body_html:
        body_text = _html_to_text(body_html)
    msg.setdefault("body", body_text)
    msg.setdefault("body_preview", body_text[:500])
    msg.setdefault("received_at", msg.get("date") or "")
    msg.setdefault("sender", msg.get("from") or "")
    msg.setdefault("source_url", mailapi_url)
    if envelope:
        msg.setdefault("raw_envelope", envelope)
        if not msg.get("to") and isinstance(envelope.get("mailbox"), dict):
            msg["to"] = str(envelope["mailbox"].get("email") or "")
    return msg


def _html_to_text(fragment: str) -> str:
    fragment = html.unescape(str(fragment or ""))
    fragment = re.sub(r"(?is)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)</(?:p|div|tr|td|h[1-6]|li)>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    return html.unescape(fragment).strip()


def _parse_mailapi_html(text: str, *, mailapi_url: str = "") -> dict[str, Any]:
    """Parse simple public mailbox HTML pages (e.g. mail.ai1998.xyz).

    The provider renders messages as HTML cards instead of JSON:

      <article class="mail-card">
        <span class="subject">...</span>
        <span class="date">2026-08-02 10:00:53</span>
        <div class="meta">发件人：...</div>
        <pre class="body">...临时代码： 194431...</pre>
      </article>

    We normalize it to the same {"emails": [...]} structure as the JSON
    providers so the existing OTP selector, baseline-code exclusion and
    persistence logic continue to work.
    """
    if not text:
        return {"emails": []}

    def clean(fragment: str) -> str:
        return _html_to_text(fragment)

    emails: list[dict[str, Any]] = []
    for idx, card in enumerate(_MAIL_CARD_RE.findall(text), start=1):
        values: dict[str, str] = {}
        for name, regex in _MAIL_FIELD_RES.items():
            match = regex.search(card)
            values[name] = clean(match.group(1)) if match else ""
        sender = values.get("meta") or ""
        sender = re.sub(r"^\s*(?:发件人|寄件人|from)\s*[:：]\s*", "", sender, flags=re.IGNORECASE).strip()
        body = values.get("body") or clean(card)
        subject = values.get("subject") or ""
        date = values.get("date") or ""
        if subject or body:
            emails.append({
                "id": f"html-{idx}-{date or subject}",
                "subject": subject,
                "from": sender,
                "sender": sender,
                "date": date,
                "received_at": date,
                "body": body,
                "body_preview": body[:500],
                "folder": "",
                "source_url": mailapi_url,
            })

    if emails:
        return {"emails": emails}

    # Last-resort fallback for providers that render a single page without
    # recognizable cards but still contain the visible mail/code text.
    body = clean(text)
    if _extract_mailapi_code(body, None):
        return {"emails": [{
            "id": "html-page",
            "subject": "",
            "from": "",
            "date": "",
            "received_at": "",
            "body": body,
            "body_preview": body[:500],
            "folder": "",
            "source_url": mailapi_url,
        }]}
    return {"emails": []}


def _select_mailapi_otp(payload: dict[str, Any], *, keyword: str, code_pattern: str | None, since_dt: datetime | None, exclude_codes) -> dict[str, Any] | None:
    # Some mail APIs expose the latest OTP directly, e.g.
    #   {"code": 0, "message": "SUCCESS", "data": {"code": "044176"}}
    # Older logic only understood list-style payloads ({"emails": [...]}),
    # so direct-code APIs looked empty and timed out.
    for item in _direct_mailapi_items(payload):
        code = _direct_mailapi_code(item)
        if code and code not in exclude_codes:
            return {
                "code": code,
                "subject": str(item.get("subject") or "mailapi direct code"),
                "sender": str(item.get("from") or item.get("sender") or "mailapi"),
                "received_at": str(item.get("date") or item.get("received_at") or ""),
                "body_text": str(item.get("body") or item.get("body_preview") or item.get("message") or code),
                "folder": str(item.get("folder") or ""),
                "id": str(item.get("id") or item.get("uid") or code),
                "raw": payload,
            }

    emails = list((payload or {}).get("emails") or [])
    emails.sort(key=lambda item: _mail_dt(item).timestamp() if _mail_dt(item) else 0, reverse=True)
    keyword_lower = str(keyword or "").lower().strip()
    for item in emails:
        if not isinstance(item, dict):
            continue
        mail_dt = _mail_dt(item)
        if since_dt and mail_dt and mail_dt < since_dt.astimezone(timezone.utc):
            continue
        # Search normalized visible text only.  Raw body_html often contains
        # CSS colors/order ids such as #333333 / #008000 that look like OTPs
        # to the generic 6-digit fallback.
        haystack = "\n".join(str(item.get(k) or "") for k in ("subject", "body", "body_text", "body_preview", "from", "sender", "to", "message"))
        if keyword_lower and keyword_lower not in haystack.lower():
            continue
        verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
        code = _normalize_otp_code(verification.get("verification_code") or verification.get("formatted") or "")
        if not code:
            code = _extract_mailapi_code(haystack, code_pattern)
        if code and code not in exclude_codes:
            return {
                "code": code,
                "subject": str(item.get("subject") or ""),
                "sender": str(item.get("from") or item.get("sender") or ""),
                "received_at": str(item.get("date") or ""),
                "body_text": str(item.get("body") or item.get("body_text") or item.get("body_preview") or ""),
                "folder": str(item.get("folder") or ""),
                "id": str(item.get("id") or item.get("date") or code),
                "raw": item,
            }
    return None


def _direct_mailapi_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        data_obj = payload.get("data")
        if isinstance(data_obj, dict):
            items.append(data_obj)
        items.append(payload)
    return items


def _direct_mailapi_code(payload: dict[str, Any]) -> str:
    for item in _direct_mailapi_items(payload):
        code = _normalize_otp_code(
            item.get("code")
            or item.get("verification_code")
            or item.get("otp")
            or item.get("value")
            or ""
        )
        if code:
            return code
    return ""


def _mail_dt(item: dict[str, Any]) -> datetime | None:
    ts = item.get("timestamp")
    if ts not in (None, ""):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except Exception:
            pass
    raw = str(item.get("date") or item.get("received_at") or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except Exception:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_otp_code(value: Any) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{6}", raw):
        return raw
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
    return match.group(1) if match else ""


def _extract_mailapi_code(text: str, pattern: str | None) -> str:
    if not text:
        return ""
    patterns = []
    if pattern:
        patterns.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
    patterns.extend(_SEMANTIC_CODE_RES)
    patterns.append(_DEFAULT_CODE_RE)
    for regex in patterns:
        m = regex.search(text)
        if m:
            return _normalize_otp_code(m.group(1) if m.groups() else m.group(0))
    return ""
