from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import parse_qs, urlencode, unquote, urlparse, urlunparse


ACCOUNT_TYPE_MICROSOFT_OAUTH = "microsoft_oauth"
ACCOUNT_TYPE_MAILAPI_URL = "mailapi_url"
ACCOUNT_TYPE_NETEASE_163_IMAP = "netease_163_imap"
ACCOUNT_TYPE_GMAIL_IMAP = "gmail_imap"
ACCOUNT_TYPE_TINKMAIL_IMAP = "tinkmail_imap"


@dataclass
class MicrosoftMailImportRecord:
    line_number: int
    email: str
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""
    account_type: str = ACCOUNT_TYPE_MICROSOFT_OAUTH
    mailapi_url: str = ""
    imap_auth_code: str = ""


class MicrosoftMailImportRule(Protocol):
    def evaluate(
        self,
        record: MicrosoftMailImportRecord,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class MicrosoftImportRowParser(Protocol):
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        ...


def _is_valid_email(email: str) -> bool:
    return "@" in str(email or "").strip()


def _is_valid_mailapi_url(url: str) -> bool:
    text = str(url or "").strip()
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_email_from_mailapi_url(url: str) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    candidates: list[str] = []
    try:
        query = parse_qs(parsed.query, keep_blank_values=True)
    except Exception:
        query = {}
    for key in ("email", "alias", "mail", "to", "address"):
        for value in query.get(key) or []:
            candidates.append(value)
    for segment in reversed([part for part in parsed.path.split("/") if part]):
        candidates.append(segment)
    for value in candidates:
        candidate = unquote(str(value or "").strip())
        if _is_valid_email(candidate):
            return candidate
    return ""


def _normalize_mailapi_url(url: str, email: str = "") -> str:
    text = str(url or "").strip()
    try:
        parsed = urlparse(text)
        if parsed.netloc.lower() != "ic.oai-gpt.com":
            return text
        query = parse_qs(parsed.query, keep_blank_values=True)
        email_value = str(email or "").strip() or _extract_email_from_mailapi_url(text)
        if email_value and "email" not in query and "alias" in query:
            query["email"] = [email_value]
            query.pop("alias", None)
        normalized_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=normalized_query))
    except Exception:
        return text


def _icloud_public_link_error(url: str) -> str:
    text = str(url or "").strip()
    try:
        parsed = urlparse(text)
        if parsed.netloc.lower() != "ic.oai-gpt.com":
            return ""
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.rstrip("/") != "/public/inbox":
            return "ic.oai-gpt.com 取件链接必须是 /public/inbox 公共链接，不能使用 /inbox 管理台页面链接"
        if not (query.get("account_id") and (query.get("email") or query.get("alias")) and query.get("mac")):
            return "ic.oai-gpt.com 公共取件链接缺少 account_id/email/mac 参数"
    except Exception:
        return ""
    return ""


class MicrosoftOAuthRowParser:
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) < 4:
            raise ValueError(
                f"行 {line_number}: 格式错误，微软 OAuth 导入需为 邮箱----密码----client_id----refresh_token"
            )

        email = parts[0]
        password = parts[1]
        client_id = parts[2]
        refresh_token = parts[3]

        if not _is_valid_email(email):
            raise ValueError(f"行 {line_number}: 无效的邮箱地址: {email}")
        if not password:
            raise ValueError(f"行 {line_number}: 缺少密码")
        if not client_id or not refresh_token:
            raise ValueError(
                f"行 {line_number}: 缺少 client_id 或 refresh_token，无法通过微软邮箱可用性检测"
            )

        return MicrosoftMailImportRecord(
            line_number=line_number,
            email=email,
            password=password,
            client_id=client_id,
            refresh_token=refresh_token,
            account_type=ACCOUNT_TYPE_MICROSOFT_OAUTH,
            mailapi_url="",
        )


class MailApiUrlRowParser:
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) == 1 and _is_valid_mailapi_url(parts[0]):
            mailapi_url = parts[0]
            email = _extract_email_from_mailapi_url(mailapi_url)
            if not email:
                raise ValueError(
                    f"行 {line_number}: URL 中未解析出邮箱，请使用 邮箱----mailapi_url 格式"
                )
        elif len(parts) >= 2:
            email = parts[0]
            mailapi_url = parts[1]
        else:
            raise ValueError(
                f"行 {line_number}: 格式错误，MailAPI URL 导入需为 邮箱----mailapi_url 或 单独 mailapi_url（URL 内含 email 参数）"
            )

        if not _is_valid_email(email):
            raise ValueError(f"行 {line_number}: 无效的邮箱地址: {email}")
        if not _is_valid_mailapi_url(mailapi_url):
            raise ValueError(
                f"行 {line_number}: 无效的 mailapi_url（需为 http/https）：{mailapi_url}"
            )
        mailapi_url = _normalize_mailapi_url(mailapi_url, email)
        icloud_error = _icloud_public_link_error(mailapi_url)
        if icloud_error:
            raise ValueError(f"行 {line_number}: {icloud_error}")

        return MicrosoftMailImportRecord(
            line_number=line_number,
            email=email,
            password="",
            client_id="",
            refresh_token="",
            account_type=ACCOUNT_TYPE_MAILAPI_URL,
            mailapi_url=mailapi_url,
        )


class GmailImapRowParser:
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) != 2:
            raise ValueError(f"行 {line_number}: Gmail 导入需为 邮箱----AppPassword")
        email, app_password = parts
        if not _is_valid_email(email) or not email.lower().endswith("@gmail.com"):
            raise ValueError(f"行 {line_number}: 不是有效的 Gmail 邮箱: {email}")
        if not app_password:
            raise ValueError(f"行 {line_number}: 缺少 Gmail App Password")
        return MicrosoftMailImportRecord(
            line_number=line_number,
            email=email,
            password=app_password,
            client_id="",
            refresh_token=app_password,
            account_type=ACCOUNT_TYPE_GMAIL_IMAP,
            mailapi_url="",
            imap_auth_code=app_password,
        )


class TinkMailImapRowParser:
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) != 2:
            raise ValueError(f"行 {line_number}: TinkMail 导入需为 邮箱----ClientToken")
        email, client_token = parts
        if not _is_valid_email(email) or not email.lower().endswith("@tinkmail.me"):
            raise ValueError(f"行 {line_number}: 不是有效的 TinkMail 邮箱: {email}")
        if not client_token:
            raise ValueError(f"行 {line_number}: 缺少 TinkMail Client Token")
        return MicrosoftMailImportRecord(
            line_number=line_number,
            email=email,
            password=client_token,
            client_id="",
            refresh_token=client_token,
            account_type=ACCOUNT_TYPE_TINKMAIL_IMAP,
            mailapi_url="",
            imap_auth_code=client_token,
        )


class NetEase163ImapRowParser:
    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) != 3:
            raise ValueError(f"行 {line_number}: 163 导入需为 邮箱----密码----授权码")
        email, password, auth_code = parts
        if not _is_valid_email(email) or not email.lower().endswith("@163.com"):
            raise ValueError(f"行 {line_number}: 不是有效的 163 邮箱: {email}")
        if not password:
            raise ValueError(f"行 {line_number}: 缺少密码")
        if not auth_code:
            raise ValueError(f"行 {line_number}: 缺少 163 IMAP 授权码")
        return MicrosoftMailImportRecord(
            line_number=line_number,
            email=email,
            password=password,
            client_id="",
            refresh_token=auth_code,
            account_type=ACCOUNT_TYPE_NETEASE_163_IMAP,
            mailapi_url="",
            imap_auth_code=auth_code,
        )


class AutoDetectRowParser:
    def __init__(
        self,
        oauth_parser: Optional[MicrosoftImportRowParser] = None,
        mailapi_parser: Optional[MicrosoftImportRowParser] = None,
        netease163_parser: Optional[MicrosoftImportRowParser] = None,
        gmail_parser: Optional[MicrosoftImportRowParser] = None,
        tinkmail_parser: Optional[MicrosoftImportRowParser] = None,
    ):
        self._oauth_parser = oauth_parser or MicrosoftOAuthRowParser()
        self._mailapi_parser = mailapi_parser or MailApiUrlRowParser()
        self._netease163_parser = netease163_parser or NetEase163ImapRowParser()
        self._gmail_parser = gmail_parser or GmailImapRowParser()
        self._tinkmail_parser = tinkmail_parser or TinkMailImapRowParser()

    def parse(self, line_number: int, line: str) -> MicrosoftMailImportRecord:
        parts = [part.strip() for part in str(line or "").split("----")]
        if len(parts) == 1 and _is_valid_mailapi_url(parts[0]):
            return self._mailapi_parser.parse(line_number, line)
        if len(parts) == 2 and _is_valid_mailapi_url(parts[1]):
            return self._mailapi_parser.parse(line_number, line)
        if len(parts) == 2 and parts[0].lower().endswith("@gmail.com"):
            return self._gmail_parser.parse(line_number, line)
        if len(parts) == 2 and parts[0].lower().endswith("@tinkmail.me"):
            return self._tinkmail_parser.parse(line_number, line)
        if len(parts) == 2:
            return self._mailapi_parser.parse(line_number, line)
        if len(parts) == 3 and parts[0].lower().endswith("@163.com"):
            return self._netease163_parser.parse(line_number, line)
        if len(parts) >= 4:
            return self._oauth_parser.parse(line_number, line)
        raise ValueError(
            f"行 {line_number}: 格式错误，支持 单独 mailapi_url(URL 内含 email) / 邮箱----mailapi_url / 邮箱----AppPassword(Gmail) / 邮箱----ClientToken(TinkMail) / 邮箱----密码----授权码(163) / 邮箱----密码----client_id----refresh_token"
        )


class MicrosoftMailImportRuleEngine:
    def __init__(self, rules: list[MicrosoftMailImportRule]):
        self._rules = list(rules)

    def evaluate(
        self,
        record: MicrosoftMailImportRecord,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        for rule in self._rules:
            result = rule.evaluate(record, context)
            if not result.get("ok"):
                return result
        return {"ok": True, "message": "ok"}


class DuplicateMicrosoftMailboxRule:
    def evaluate(
        self,
        record: MicrosoftMailImportRecord,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        existing_emails = context.get("existing_emails") or set()
        if record.email in existing_emails:
            return {"ok": False, "message": f"行 {record.line_number}: 邮箱已存在: {record.email}"}
        return {"ok": True, "message": "ok"}


class MailApiUrlFormatRule:
    def evaluate(
        self,
        record: MicrosoftMailImportRecord,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if record.account_type != ACCOUNT_TYPE_MAILAPI_URL:
            return {"ok": True, "message": "ok"}
        if not _is_valid_mailapi_url(record.mailapi_url):
            return {
                "ok": False,
                "message": f"行 {record.line_number}: 无效的 mailapi_url（需为 http/https）：{record.mailapi_url}",
            }
        icloud_error = _icloud_public_link_error(record.mailapi_url)
        if icloud_error:
            return {
                "ok": False,
                "message": f"行 {record.line_number}: {icloud_error}",
            }
        return {"ok": True, "message": "ok"}


class MicrosoftMailboxAvailabilityRule:
    def __init__(self, mailbox: Any):
        self._mailbox = mailbox

    def evaluate(
        self,
        record: MicrosoftMailImportRecord,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if record.account_type != ACCOUNT_TYPE_MICROSOFT_OAUTH:
            return {"ok": True, "message": "ok"}
        result = self._mailbox.probe_oauth_availability(
            email=record.email,
            client_id=record.client_id,
            refresh_token=record.refresh_token,
        )
        if result.get("ok"):
            return {"ok": True, "message": "ok"}
        return {
            "ok": False,
            "message": f"行 {record.line_number}: {result.get('message') or '微软邮箱可用性检测未通过'}",
            "reason": result.get("reason", "oauth_token_failed"),
        }


def parse_microsoft_import_record(line_number: int, line: str) -> MicrosoftMailImportRecord:
    """兼容旧调用：仅按微软 OAuth 四段格式解析。"""
    parts = [part.strip() for part in str(line or "").split("----")]
    if len(parts) >= 2 and len(parts) < 4:
        raise ValueError(
            f"行 {line_number}: 缺少 client_id 或 refresh_token，无法通过微软邮箱可用性检测"
        )
    return MicrosoftOAuthRowParser().parse(line_number, line)


def parse_microsoft_import_line(line_number: int, line: str) -> MicrosoftMailImportRecord:
    """按行格式自动识别：2 段=MailAPI URL，4 段=微软 OAuth。"""
    return AutoDetectRowParser().parse(line_number, line)
