"""Codex referral invitation stage.

Implements the protocol flow from ../gptinvite/codex_invitation_helper.py
inside the queue WorkPool system. Given a source account/email id, it derives
that row's email domain, generates random same-domain recipient emails, checks
remaining invite quota, and sends the referral invite request.
"""
from __future__ import annotations

import json
import os
import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine
from backend.core.json_utils import json_loads
from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.core.stages import stage
from backend.models.access_token import AccessTokenAccount
from backend.models.account import ChatGPTAccount
from backend.models.email import EmailAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.schemas.stage_io import CodexInvitationInput, CodexInvitationOutput

INVITE_URL = "https://chatgpt.com/backend-api/wham/referrals/invite"
ELIGIBILITY_URL = "https://chatgpt.com/backend-api/wham/referrals/eligibility_rules"
REFERRAL_KEY = "codex_referral_workspace_out_of_credits"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
EMAIL_ALPHABET = string.ascii_lowercase + string.digits


@dataclass
class SourceCredentials:
    source_type: str
    source_id: int
    email: str
    access_token: str
    chatgpt_account_id: str


@stage(
    name="codex_invitation",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=CodexInvitationInput,
    output_schema=CodexInvitationOutput,
    description="Use a source ChatGPT/Codex account token to generate same-domain random emails and send Codex referral invites.",
)
def run(ctx):
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.codex_invitation."), **extra_config}

    source = _resolve_source(payload, config)
    domain = str(payload.get("domain") or "").strip().lstrip("@") or _domain_from_email(source.email)
    if not domain:
        raise RuntimeError(f"cannot infer email domain from source email={source.email!r}")

    invite_count = _read_int(payload.get("invite_count", payload.get("generate", config.get("invite_count"))), default=1, minimum=1, maximum=200)
    prefix_len = _read_int(payload.get("prefix_len", config.get("prefix_len")), default=20, minimum=3, maximum=64)
    dry_run = _as_bool(payload.get("dry_run", config.get("dry_run", False)))
    check_eligibility_enabled = _as_bool(payload.get("check_eligibility", config.get("check_eligibility", True)), default=True)
    verify_tls = not _as_bool(payload.get("insecure", config.get("insecure", False)))
    referral_key = str(payload.get("referral_key") or config.get("referral_key") or REFERRAL_KEY).strip() or REFERRAL_KEY

    explicit_emails = payload.get("emails")
    if isinstance(explicit_emails, str):
        recipients = [e.strip() for e in explicit_emails.split(",") if e.strip()]
    elif isinstance(explicit_emails, list):
        recipients = [str(e).strip() for e in explicit_emails if str(e).strip()]
    else:
        recipients = _random_emails(domain, invite_count, prefix_len)
    if not recipients:
        raise RuntimeError("recipient email list is empty")

    proxy_url = str(payload.get("proxy_url") or ctx.effective_proxy_url() or config.get("proxy_url") or "").strip()
    if not proxy_url and _as_bool(payload.get("acquire_proxy", config.get("acquire_proxy", False))):
        try:
            proxy_resource = ctx.acquire("proxy_pool", hint={"stage": "codex_invitation"})
            proxy_payload = proxy_resource.payload or {}
            proxy_url = str(proxy_payload.get("url") or proxy_resource.id or "").strip()
            ctx.attach_proxy(proxy_id=int(proxy_payload.get("proxy_id") or 0) or None, proxy_url=proxy_url)
        except Exception as exc:
            ctx.log(f"proxy_pool acquire failed, continue without proxy: {exc}", level="warning")

    ctx.log("starting codex_invitation", payload={
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_email": source.email,
        "domain": domain,
        "invite_count": len(recipients),
        "dry_run": dry_run,
        "proxy_provided": bool(proxy_url),
        "check_eligibility": check_eligibility_enabled,
    })

    session_type, session = _build_session(proxy_url)
    ctx.log(f"codex_invitation session initialized: {session_type}")

    remaining: Optional[int] = None
    if check_eligibility_enabled:
        ctx.check_cancelled()
        ctx.log("checking Codex invite eligibility")
        remaining = _check_eligibility(session, source.access_token, source.chatgpt_account_id, referral_key, verify_tls=verify_tls)
        if remaining is not None:
            ctx.log("eligibility checked", payload={"remaining_invites": remaining})
            if remaining <= 0:
                raise RuntimeError("source account has no remaining Codex referral invite quota")
            if len(recipients) > remaining:
                ctx.log(f"recipient count {len(recipients)} exceeds remaining {remaining}; trimming", level="warning")
                recipients = recipients[:remaining]
        else:
            ctx.log("eligibility check did not return a quota; will continue", level="warning")

    invited_email = recipients[0] if recipients else ""
    result_base = {
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_email": source.email,
        "domain": domain,
        "emails": recipients,
        "invited_email": invited_email,
        "email": invited_email,
        "sso_email": invited_email,
        "invite_count": len(recipients),
        "remaining_invites": remaining,
        "dry_run": dry_run,
    }

    if dry_run:
        ctx.update_result({**result_base, "sent": False, "status_code": 0, "response": {}})
        ctx.log("codex_invitation dry-run completed", payload={"emails": recipients})
        return

    ctx.check_cancelled()
    ctx.log("sending Codex referral invites", payload={"count": len(recipients)})
    resp = session.post(
        INVITE_URL,
        headers=_headers(source.access_token, source.chatgpt_account_id, is_json=True),
        json={"referral_key": referral_key, "emails": recipients},
        timeout=_read_int(config.get("timeout_seconds"), default=30, minimum=5, maximum=180),
        verify=verify_tls,
    )
    response_payload: Any
    try:
        response_payload = resp.json()
    except Exception:
        response_payload = {"text": (resp.text or "")[:5000]}

    if resp.status_code != 200:
        ctx.update_result({**result_base, "sent": False, "status_code": resp.status_code, "response": response_payload})
        raise RuntimeError(f"Codex invitation failed: HTTP {resp.status_code} {(resp.text or '')[:300]}")

    invites = response_payload.get("invites", []) if isinstance(response_payload, dict) else []
    ctx.update_result({
        **result_base,
        "sent": True,
        "status_code": resp.status_code,
        "response": response_payload,
        "invites": invites,
    })
    ctx.log("codex_invitation succeeded", payload={"sent": len(recipients), "server_invites": len(invites) if isinstance(invites, list) else 0})


def _resolve_source(payload: dict[str, Any], config: dict[str, Any]) -> SourceCredentials:
    raw_source_type = payload.get("source_type")
    # User-facing field is named email_id; treat it as email_accounts.id unless
    # source_type is explicitly changed to a non-auto value. source_id keeps
    # the old auto behavior. Direct email with auto searches account pools.
    default_source_type = "email_account" if payload.get("email_id") not in (None, "") and str(raw_source_type or "").strip().lower() in {"", "auto"} else "auto"
    source_type = str(raw_source_type or default_source_type).strip().lower()
    if payload.get("email_id") not in (None, "") and source_type == "auto":
        source_type = "email_account"
    # inviter_* aliases are the UI-facing names. Keep email/email_id for
    # backward compatibility and pipeline carry-over.
    if payload.get("inviter_account_id") not in (None, "") and payload.get("email_id") in (None, "") and payload.get("source_id") in (None, ""):
        payload["source_id"] = payload.get("inviter_account_id")
    source_id = _read_int(payload.get("email_id", payload.get("source_id")), default=0, minimum=0, maximum=2_000_000_000)
    direct_email = str(payload.get("inviter_email") or payload.get("email") or "").strip()
    file_access_token, file_account_id = _load_auth_tokens_from_file(str(payload.get("auth_file") or config.get("auth_file") or ""))
    direct_access_token = (
        str(payload.get("access_token") or "").strip()
        or str(config.get("access_token") or config.get("chatgpt_access_token") or "").strip()
        or file_access_token
    )
    direct_account_id = (
        str(payload.get("chatgpt_account_id") or payload.get("codex_account_id") or payload.get("account_id") or "").strip()
        or str(config.get("chatgpt_account_id") or config.get("codex_account_id") or config.get("account_id") or "").strip()
        or file_account_id
    )

    if direct_email and direct_access_token and direct_account_id and not source_id:
        return SourceCredentials("direct", 0, direct_email, direct_access_token, direct_account_id)

    is_auto = source_type == "auto"
    order = [source_type] if not is_auto else ["chatgpt_account", "access_token_account", "email_account"]
    errors: list[str] = []
    with Session(engine) as s:
        # Direct email without explicit token: resolve it from account pools.
        # Priority mirrors source_type when provided; auto checks ChatGPT account
        # first, then Free AT pool. EmailAccount only supplies a domain and still
        # needs token/account id from settings/auth_file.
        if direct_email and not source_id:
            for typ in order:
                try:
                    if typ in {"chatgpt", "account", "chatgpt_account"}:
                        row = s.exec(sa_select(ChatGPTAccount).where(ChatGPTAccount.email == direct_email).limit(1)).scalars().first()
                        if not row:
                            errors.append("chatgpt_account_email:not_found")
                            continue
                        at, acct = _token_from_chatgpt_account_or_rt(s, row)
                        cred = SourceCredentials("chatgpt_account", int(row.id or 0), row.email, at, acct or row.account_id)
                        return _with_overrides_or_validate(cred, direct_access_token, direct_account_id)
                    if typ in {"access_token", "access_token_account", "at"}:
                        row = s.exec(sa_select(AccessTokenAccount).where(AccessTokenAccount.email == direct_email).limit(1)).scalars().first()
                        if not row:
                            errors.append("access_token_account_email:not_found")
                            continue
                        cred = SourceCredentials("access_token_account", int(row.id or 0), row.email, row.access_token, row.account_id)
                        return _with_overrides_or_validate(cred, direct_access_token, direct_account_id)
                    if typ in {"email", "email_account", "email_pool"}:
                        row = s.exec(sa_select(EmailAccount).where(EmailAccount.email == direct_email).limit(1)).scalars().first()
                        if not row:
                            errors.append("email_account_email:not_found")
                            continue
                        meta = json_loads(row.metadata_json, fallback={}) or {}
                        access_token = direct_access_token or str(meta.get("access_token") or meta.get("chatgpt_access_token") or "").strip()
                        account_id = direct_account_id or str(meta.get("account_id") or meta.get("chatgpt_account_id") or meta.get("codex_account_id") or "").strip()
                        cred = SourceCredentials("email_account", int(row.id or 0), row.email, access_token, account_id)
                        return _with_overrides_or_validate(cred, "", "")
                except RuntimeError as exc:
                    errors.append(f"{typ}:{exc}")
                    if not is_auto:
                        raise
                    continue
            raise RuntimeError(f"email {direct_email!r} could not be resolved from account pools ({'; '.join(errors)})")

        if not source_id:
            raise RuntimeError("codex_invitation requires email/email_id/source_id, or direct email + access_token + chatgpt_account_id")

        for typ in order:
            try:
                if typ in {"chatgpt", "account", "chatgpt_account"}:
                    row = s.get(ChatGPTAccount, source_id)
                    if not row:
                        errors.append("chatgpt_account:not_found")
                        continue
                    at, acct = _token_from_chatgpt_account_or_rt(s, row)
                    cred = SourceCredentials("chatgpt_account", source_id, row.email, at, acct or row.account_id)
                    return _with_overrides_or_validate(cred, direct_access_token, direct_account_id)
                if typ in {"access_token", "access_token_account", "at"}:
                    row = s.get(AccessTokenAccount, source_id)
                    if not row:
                        errors.append("access_token_account:not_found")
                        continue
                    cred = SourceCredentials("access_token_account", source_id, row.email, row.access_token, row.account_id)
                    return _with_overrides_or_validate(cred, direct_access_token, direct_account_id)
                if typ in {"email", "email_account", "email_pool"}:
                    row = s.get(EmailAccount, source_id)
                    if not row:
                        errors.append("email_account:not_found")
                        continue
                    meta = json_loads(row.metadata_json, fallback={}) or {}
                    access_token = direct_access_token or str(meta.get("access_token") or meta.get("chatgpt_access_token") or "").strip()
                    account_id = direct_account_id or str(meta.get("account_id") or meta.get("chatgpt_account_id") or meta.get("codex_account_id") or "").strip()
                    cred = SourceCredentials("email_account", source_id, row.email, access_token, account_id)
                    return _with_overrides_or_validate(cred, "", "")
                errors.append(f"{typ}:unsupported")
            except RuntimeError as exc:
                errors.append(f"{typ}:{exc}")
                if not is_auto:
                    raise
                continue
    raise RuntimeError(f"source id {source_id} could not be resolved ({'; '.join(errors)})")




def _token_from_chatgpt_account_or_rt(session: Session, account: ChatGPTAccount) -> tuple[str, str]:
    """Return bearer token + chatgpt-account-id for invitation.

    SSO-created accounts may have empty chatgpt_accounts.access_token while
    their OAuth tokens live in openai_refresh_tokens. Use oauth_access_token as
    fallback for Codex Desktop-style referral requests.
    """
    access_token = str(account.access_token or "").strip()
    account_id = str(account.account_id or "").strip()
    if access_token and account_id:
        return access_token, account_id

    rt = session.exec(
        sa_select(OpenAIRefreshToken)
        .where(OpenAIRefreshToken.account_id == int(account.id or 0))
        .limit(1)
    ).scalars().first()
    if not rt:
        return access_token, account_id

    oauth_at = str(rt.oauth_access_token or "").strip()
    oauth_id = str(rt.oauth_id_token or "").strip()
    if oauth_at and not access_token:
        access_token = oauth_at
    if not account_id:
        account_id = _extract_account_id_from_jwt(oauth_at, oauth_id)
    return access_token, account_id


def _extract_account_id_from_jwt(*tokens: str) -> str:
    import base64
    for token in tokens:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            continue
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        try:
            data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore"))
        except Exception:
            continue
        auth = data.get("https://api.openai.com/auth", {}) if isinstance(data, dict) else {}
        account_id = auth.get("chatgpt_account_id") or auth.get("account_id") or data.get("account_id")
        if account_id:
            return str(account_id)
    return ""

def _load_auth_tokens_from_file(auth_file: str) -> tuple[str, str]:
    """Load Codex Desktop auth JSON, compatible with codex_invitation_helper.py.

    Returns (access_token, account_id). Missing/invalid files are ignored so
    explicit DB tokens or WorkPool settings can still be used.
    """
    path_value = str(auth_file or "").strip() or os.path.expanduser("~/.codex/auth.json")
    path = Path(os.path.expanduser(path_value))
    if not path.exists():
        return "", ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tokens = data.get("tokens", {}) if isinstance(data, dict) else {}
        access_token = str(tokens.get("access_token") or data.get("access_token") or "").strip()
        account_id = str(tokens.get("account_id") or data.get("account_id") or "").strip()
        return access_token, account_id
    except Exception:
        return "", ""

def _with_overrides_or_validate(cred: SourceCredentials, access_token_override: str, account_id_override: str) -> SourceCredentials:
    if access_token_override:
        cred.access_token = access_token_override
    if account_id_override:
        cred.chatgpt_account_id = account_id_override
    if not cred.email:
        raise RuntimeError(f"source {cred.source_type}#{cred.source_id} has empty email")
    if not cred.access_token:
        raise RuntimeError(f"source {cred.source_type}#{cred.source_id} is missing access_token")
    if not cred.chatgpt_account_id:
        raise RuntimeError(f"source {cred.source_type}#{cred.source_id} is missing chatgpt account_id")
    return cred


def _domain_from_email(email: str) -> str:
    email = str(email or "").strip()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()


def _random_emails(domain: str, count: int, prefix_len: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    domain = domain.lstrip("@").strip().lower()
    while len(out) < count:
        prefix = "".join(secrets.choice(EMAIL_ALPHABET) for _ in range(prefix_len))
        email = f"{prefix}@{domain}"
        if email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _build_session(proxy_url: str = ""):
    """Build invite HTTP session.

    Keep this intentionally identical to ~/Codex_team_auto/codex_invitation_helper.py:
    plain requests.Session, trust_env=False, and direct proxy assignment.  The
    referral endpoint is sensitive to client/header differences; using
    cloudscraper here caused systematic 403s in batch invite tests.
    """
    session = requests.Session()
    session.trust_env = False
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return "requests", session


def _headers(access_token: str, account_id: str, *, is_json: bool = False) -> dict[str, str]:
    headers = {
        "Host": "chatgpt.com",
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "originator": "Codex Desktop",
        "oai-language": "zh-CN",
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "empty",
    }
    if is_json:
        headers["Content-Type"] = "application/json"
    return headers


def _check_eligibility(session, access_token: str, account_id: str, referral_key: str, *, verify_tls: bool = True) -> Optional[int]:
    try:
        resp = session.get(
            ELIGIBILITY_URL,
            headers=_headers(access_token, account_id),
            params={"referral_key": referral_key},
            timeout=20,
            verify=verify_tls,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        rules = data.get("time_frame_rules", []) if isinstance(data, dict) else []
        remaining: list[int] = []
        for rule in rules:
            sent = rule.get("invites_sent") if isinstance(rule, dict) else None
            total = rule.get("invites_total") if isinstance(rule, dict) else None
            if sent is not None and total is not None:
                remaining.append(max(0, int(total) - int(sent)))
        return min(remaining) if remaining else None
    except Exception:
        return None


def _workpool_config(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in settings.get_all().items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
    return out


def _read_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(minimum, min(maximum, n))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
