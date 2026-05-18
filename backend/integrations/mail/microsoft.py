"""Microsoft (Outlook / Hotmail) mailbox helper.

Provides:

  - `MicrosoftOAuthTokenProvider`:  refresh-token -> access-token via the
    Microsoft Identity Platform.  Mirrors the scope-fallback strategy used in
    the legacy `core/base_mailbox.py`.
  - `MicrosoftMailbox`: high-level wrapper used by flows.  Talks to Microsoft
    Graph by default; raises if a refresh-token is invalid (so import-time
    `probe_oauth_availability()` and run-time fetches share one path).

Intentionally compact and self-contained — does not import the legacy
`core.base_mailbox` monolith.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from typing import Any, Callable, Iterable

import requests

from backend.core.proxy import build_requests_proxy_config

logger = logging.getLogger(__name__)


GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

OAUTH_TOKEN_ENDPOINTS = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
)

OAUTH_SCOPES = (
    "https://graph.microsoft.com/.default",
    "https://outlook.office.com/.default offline_access",
    "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "",
)

DEFAULT_FOLDERS = ("inbox", "junkemail", "deleteditems")

DEFAULT_OTP_PATTERN = r"(?<!\d)(\d{4,8})(?!\d)"
SEMANTIC_OTP_PATTERNS = (
    r"(?is)(?:verification\s+code|one[-\s]*time\s+(?:password|code)|security\s+code|"
    r"login\s+code|验证码|校验码|动态码|認證碼|驗證碼)[^0-9]{0,30}(\d{6})",
    r"(?is)\bcode\b[^0-9]{0,12}(\d{6})",
)


# ---- token --------------------------------------------------------------------


@dataclass
class TokenResult:
    access_token: str
    expires_in: int
    scope: str

    @property
    def expires_at(self) -> float:
        return time.time() + max(1, int(self.expires_in or 0))


class MicrosoftOAuthError(RuntimeError):
    pass


class MicrosoftOAuthTokenProvider:
    def __init__(self, *, proxy: str | None = None, timeout: float = 20.0) -> None:
        self._proxy = build_requests_proxy_config(proxy)
        self._timeout = timeout

    def fetch(self, *, client_id: str, refresh_token: str) -> TokenResult:
        if not client_id or not refresh_token:
            raise MicrosoftOAuthError("missing client_id or refresh_token")

        last_error: str = ""
        for endpoint in OAUTH_TOKEN_ENDPOINTS:
            for scope in OAUTH_SCOPES:
                payload = {
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                }
                if scope:
                    payload["scope"] = scope
                try:
                    resp = requests.post(
                        endpoint,
                        data=payload,
                        proxies=self._proxy,
                        timeout=self._timeout,
                        headers={"Accept": "application/json"},
                    )
                except Exception as exc:
                    last_error = f"{endpoint} request error: {exc}"
                    continue

                if resp.status_code == 200:
                    body = resp.json() or {}
                    token = str(body.get("access_token") or "").strip()
                    if not token:
                        last_error = f"{endpoint}: response missing access_token"
                        continue
                    return TokenResult(
                        access_token=token,
                        expires_in=int(body.get("expires_in") or 0),
                        scope=str(body.get("scope") or scope),
                    )
                # The token endpoint is chatty about why a token isn't valid.
                # Capture the message but keep trying alternate scopes/endpoints.
                last_error = f"{endpoint} status={resp.status_code} body={resp.text[:240]}"
        raise MicrosoftOAuthError(last_error or "unable to obtain Microsoft access_token")


# ---- mailbox -----------------------------------------------------------------


@dataclass
class MicrosoftMailMessage:
    id: str
    subject: str
    sender: str
    received_at: str
    body_text: str
    folder: str
    raw: dict[str, Any]


class MicrosoftMailbox:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        graph_api_base: str = GRAPH_API_BASE,
        token_provider: MicrosoftOAuthTokenProvider | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._graph_api_base = graph_api_base.rstrip("/")
        self._proxy = build_requests_proxy_config(proxy)
        self._token_provider = token_provider or MicrosoftOAuthTokenProvider(proxy=proxy)
        self._timeout = timeout

    # -- token

    def access_token(self, *, client_id: str, refresh_token: str) -> str:
        return self._token_provider.fetch(client_id=client_id, refresh_token=refresh_token).access_token

    def probe_oauth_availability(
        self, *, email: str, client_id: str, refresh_token: str
    ) -> dict[str, Any]:
        try:
            self._token_provider.fetch(client_id=client_id, refresh_token=refresh_token)
        except MicrosoftOAuthError as exc:
            return {"ok": False, "message": str(exc), "reason": "oauth_token_failed"}
        return {"ok": True, "email": email}

    # -- list / fetch

    def list_messages(
        self,
        *,
        client_id: str,
        refresh_token: str,
        folders: Iterable[str] = DEFAULT_FOLDERS,
        top: int = 20,
        since_iso: str | None = None,
    ) -> list[MicrosoftMailMessage]:
        access_token = self.access_token(client_id=client_id, refresh_token=refresh_token)
        out: list[MicrosoftMailMessage] = []
        for folder in folders:
            url = (
                f"{self._graph_api_base}/me/mailFolders/{folder}/messages"
                f"?$top={int(top)}&$orderby=receivedDateTime%20desc"
            )
            if since_iso:
                # Server-side filter: only mails received >= since_iso. Saves
                # a full inbox download every poll.
                from urllib.parse import quote

                url += f"&$filter=receivedDateTime%20ge%20{quote(since_iso, safe='')}"
            try:
                resp = requests.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "Prefer": 'outlook.body-content-type="text"',
                    },
                    proxies=self._proxy,
                    timeout=self._timeout,
                )
            except Exception as exc:
                logger.warning("graph %s listMessages error: %s", folder, exc)
                continue
            if resp.status_code != 200:
                logger.warning("graph %s listMessages status=%s body=%s", folder, resp.status_code, resp.text[:240])
                continue
            for entry in (resp.json() or {}).get("value", []) or []:
                out.append(_parse_graph_message(entry, folder))
        return out

    def fetch_latest_otp(
        self,
        *,
        client_id: str,
        refresh_token: str,
        keyword: str = "",
        code_pattern: str | None = None,
        folders: Iterable[str] = DEFAULT_FOLDERS,
        top: int = 20,
        since_iso: str | None = None,
        exclude_codes: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        messages = self.list_messages(
            client_id=client_id,
            refresh_token=refresh_token,
            folders=folders,
            top=top,
            since_iso=since_iso,
        )
        keyword_lower = (keyword or "").lower()
        excluded = {str(code or "").strip() for code in (exclude_codes or ()) if str(code or "").strip()}
        for message in messages:
            haystack = f"{message.subject}\n{message.body_text}"
            if keyword_lower and keyword_lower not in haystack.lower():
                continue
            code = _extract_otp(haystack, code_pattern)
            if code and code not in excluded:
                return {
                    "code": code,
                    "subject": message.subject,
                    "sender": message.sender,
                    "received_at": message.received_at,
                    "body_text": message.body_text,
                    "folder": message.folder,
                    "id": message.id,
                    "raw": message.raw,
                }
        return None


# ---- helpers ------------------------------------------------------------------


def _parse_graph_message(entry: dict[str, Any], folder: str) -> MicrosoftMailMessage:
    body = entry.get("body") or {}
    body_text = str(body.get("content") or "")
    from_obj = (entry.get("from") or {}).get("emailAddress") or {}
    sender = str(from_obj.get("address") or "")
    return MicrosoftMailMessage(
        id=str(entry.get("id") or ""),
        subject=str(entry.get("subject") or ""),
        sender=sender,
        received_at=str(entry.get("receivedDateTime") or ""),
        body_text=body_text,
        folder=folder,
        raw=entry,
    )


def _extract_otp(text: str, pattern: str | None) -> str:
    if not text:
        return ""
    patterns: list[str] = []
    if pattern:
        patterns.append(pattern)
    patterns.extend(SEMANTIC_OTP_PATTERNS)
    patterns.append(DEFAULT_OTP_PATTERN)
    for regex in patterns:
        m = re.search(regex, text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return ""


def parse_mime_bytes(raw: bytes) -> Message:
    """Useful when callers pull MIME from IMAP/file rather than Graph."""
    return message_from_bytes(raw)


def decode_b64url(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", "replace")
    except Exception:
        return value


# ---- public surface used by flows --------------------------------------------


def wait_for_otp(
    *,
    mailbox: MicrosoftMailbox,
    client_id: str,
    refresh_token: str,
    keyword: str = "",
    code_pattern: str | None = None,
    timeout: int = 180,
    poll_interval: float = 5.0,
    log: Callable[[str], None] | None = None,
    since_iso: str | None = None,
    exclude_codes: Iterable[str] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + max(1, int(timeout))
    last_seen_id: str = ""
    while time.time() < deadline:
        result = mailbox.fetch_latest_otp(
            client_id=client_id,
            refresh_token=refresh_token,
            keyword=keyword,
            code_pattern=code_pattern,
            since_iso=since_iso,
            exclude_codes=exclude_codes,
        )
        if result and result["id"] != last_seen_id:
            last_seen_id = result["id"]
            if log:
                log(f"received OTP from {result['sender']} subject={result['subject']!r}")
            return result
        if log:
            log("no matching OTP yet; polling again")
        time.sleep(poll_interval)
    raise TimeoutError(f"OTP not received within {timeout}s")
