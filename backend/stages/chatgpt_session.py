"""chatgpt_session stage."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.constants import ACCOUNT_STATUS_BANNED, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING
from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.integrations.chatgpt.account_state import is_account_deactivated_message
from backend.integrations.chatgpt.fingerprint import BrowserFingerprint
from backend.integrations.chatgpt.utils import decode_jwt_payload, seed_oai_device_cookie
from backend.models.account import ChatGPTAccount
from backend.models.job import Job
from backend.schemas.stage_io import ChatGPTSessionInput, ChatGPTSessionOutput


@stage(
    name="chatgpt_session",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=ChatGPTSessionInput,
    output_schema=ChatGPTSessionOutput,
    description="Normalize or refresh a registered ChatGPT web session.",
)
def run(ctx) -> None:
    account_id = ctx.account_id or int(ctx.input.get("account_id") or 0) or None
    if not account_id:
        raise RuntimeError("chatgpt_session stage requires account_id")
    ctx.attach_account(account_id)

    mode = str(
        ctx.input.get("mode")
        or settings.get("workpool.chatgpt_session.mode", "session")
        or "session"
    ).strip().lower()
    if mode not in {"", "session", "web_session"}:
        raise RuntimeError(f"unsupported chatgpt_session mode: {mode}")

    force_refresh = _truthy(ctx.input.get("force_refresh"))
    refresh_before_seconds = _read_int_setting("workpool.chatgpt_session.refresh_before_seconds", 300, minimum=0, maximum=86400)
    max_attempts = _read_int_setting("workpool.chatgpt_session.max_attempts", 3, minimum=1, maximum=10)

    with Session(engine) as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        snapshot = _account_snapshot(account)

    ctx.log("validating ChatGPT access token", payload={"account_id": account_id, "force_refresh": force_refresh})
    with Session(engine) as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        client = _build_client_from_account(account, ctx)

    if snapshot["access_token"]:
        ok, me_or_error = _validate_access_token(client, snapshot["access_token"], ctx, label="cached")
        if ok:
            snapshot = _record_session_valid(account_id, me_or_error if isinstance(me_or_error, dict) else {}, "current")
            ctx.update_result(_output_payload(snapshot, session_refresh_status="current"))
            _maybe_enqueue_sub2api_sync(ctx, account_id, snapshot)
            ctx.log("chatgpt_session current", payload={"account_id": account_id, "expires_at": _iso(snapshot["session_expires_at"])})
            return
        ctx.log("cached ChatGPT access token invalid", level="warning", payload={"account_id": account_id, "error": str(me_or_error or "")[:240]})

    old_access_token = snapshot.get("access_token", "")
    ctx.log("refreshing ChatGPT web session from cached cookies", payload={"account_id": account_id, "force_refresh": force_refresh})
    ok, session_or_error = client.fetch_chatgpt_session(max_attempts=max_attempts, retry_delay=1.2)
    if ok:
        session_data = session_or_error if isinstance(session_or_error, dict) else {}
        new_access_token = str(session_data.get("accessToken") or "").strip()
        changed = _token_fingerprint(old_access_token) != _token_fingerprint(new_access_token)
        ctx.log("/api/auth/session returned access token", payload={"account_id": account_id, "changed": changed})
        if changed:
            valid, me_or_error = _validate_access_token(client, new_access_token, ctx, label="session")
            if valid:
                identity_state = client.export_identity_state()
                snapshot = _persist_session_data(account_id, session_data, identity_state, me_or_error if isinstance(me_or_error, dict) else {}, status="refreshed_from_session")
                ctx.update_result(_output_payload(snapshot, session_refresh_status="refreshed_from_session"))
                _maybe_enqueue_sub2api_sync(ctx, account_id, snapshot)
                ctx.log("chatgpt_session refreshed from cached cookies", payload={"account_id": account_id, "expires_at": _iso(snapshot["session_expires_at"])})
                return
            ctx.log("/api/auth/session access token invalid", level="warning", payload={"account_id": account_id, "error": str(me_or_error or "")[:240]})
        else:
            ctx.log("/api/auth/session returned the same access token", level="warning", payload={"account_id": account_id})
    else:
        ctx.log("cached cookie session refresh failed", level="warning", payload={"account_id": account_id, "error": str(session_or_error or "")[:240]})

    ctx.log("relogin ChatGPT web session via email OTP", payload={"account_id": account_id})
    with Session(engine) as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        email = str(account.email or "").strip()
        password = str(account.password or "").strip()
        relogin_proxy_id, relogin_proxy_url, relogin_proxy_region = _acquire_relogin_proxy(ctx, account)
        client = _build_client_from_account(account, ctx, load_cookies=False, proxy_url_override=relogin_proxy_url)
    ctx.attach_proxy(proxy_id=relogin_proxy_id, proxy_url=relogin_proxy_url)
    ctx.log("chatgpt_session relogin proxy acquired", payload={"account_id": account_id, "proxy_id": relogin_proxy_id, "region": relogin_proxy_region})
    otp_provider = _build_otp_provider(email)
    ok, session_or_error = client.relogin_existing_user(email, password=password, otp_provider=otp_provider, max_steps=16)
    if not ok:
        error = str(session_or_error or "ChatGPT relogin failed")
        _record_session_failure(account_id, error)
        ctx.log(f"chatgpt_session failed: {error}", level="error")
        raise RuntimeError(error)

    session_data = session_or_error if isinstance(session_or_error, dict) else {}
    new_access_token = str(session_data.get("accessToken") or "").strip()
    changed = _token_fingerprint(old_access_token) != _token_fingerprint(new_access_token)
    valid, me_or_error = _validate_access_token(client, new_access_token, ctx, label="relogin")
    if not valid:
        error = f"relogin access token invalid: {me_or_error}"
        _record_session_failure(account_id, error)
        ctx.log(f"chatgpt_session failed: {error}", level="error")
        raise RuntimeError(error)

    identity_state = client.export_identity_state()
    snapshot = _persist_session_data(account_id, session_data, identity_state, me_or_error if isinstance(me_or_error, dict) else {}, status="relogin_refreshed")
    ctx.update_result(_output_payload(snapshot, session_refresh_status="relogin_refreshed"))
    _maybe_enqueue_sub2api_sync(ctx, account_id, snapshot)
    ctx.log("chatgpt_session relogin refreshed", payload={"account_id": account_id, "changed": changed, "expires_at": _iso(snapshot["session_expires_at"])})


# ---- helpers ---------------------------------------------------------------


def _maybe_enqueue_sub2api_sync(ctx: Any, account_id: int, snapshot: dict[str, Any]) -> None:
    if not _truthy(ctx.input.get("sync_sub2api_after_refresh")):
        return
    with Session(engine) as s:
        running = s.exec(
            sa_select(Job)
            .where(Job.account_id == account_id)
            .where(Job.type == "sub2api_sync")
            .where(Job.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
            .order_by(Job.id.desc())
        ).scalars().first()
        if running is not None:
            ctx.log("sub2api_sync already queued after AT refresh", payload={"account_id": account_id, "job_id": int(running.id or 0)})
            return
    from backend.core.queue import enqueue_job

    job_id = enqueue_job(
        type="sub2api_sync",
        input={"account_id": account_id, "force_upload": True},
        account_id=account_id,
        proxy_id=snapshot.get("proxy_id"),
        proxy_url=str(snapshot.get("proxy_url") or ""),
    )
    ctx.log("sub2api_sync queued after AT refresh", payload={"account_id": account_id, "job_id": job_id})


def _account_snapshot(account: ChatGPTAccount) -> dict[str, Any]:
    metadata = json_loads(account.metadata_json, fallback={}) or {}
    user = metadata.get("user") if isinstance(metadata.get("user"), dict) else {}
    raw_account = metadata.get("account") if isinstance(metadata.get("account"), dict) else {}
    chatgpt_user_id = str(metadata.get("user_id") or user.get("id") or "")
    plan_type = str(account.plan_type or raw_account.get("planType") or raw_account.get("plan_type") or "")
    return {
        "account_id": int(account.id or 0),
        "chatgpt_account_id": str(account.account_id or raw_account.get("id") or ""),
        "chatgpt_user_id": chatgpt_user_id,
        "access_token": str(account.access_token or ""),
        "id_token": str(account.id_token or ""),
        "session_token": str(account.session_token or ""),
        "session_expires_at": account.session_expires_at,
        "session_refresh_status": str(account.session_refresh_status or ""),
        "plan_type": plan_type,
        "proxy_id": account.proxy_id,
        "proxy_url": str(account.proxy_url or ""),
    }


def _output_payload(snapshot: dict[str, Any], *, session_refresh_status: str) -> dict[str, Any]:
    return {
        "account_id": snapshot["account_id"],
        "chatgpt_account_id": snapshot.get("chatgpt_account_id", ""),
        "chatgpt_user_id": snapshot.get("chatgpt_user_id", ""),
        "access_token": snapshot.get("access_token", ""),
        "id_token": snapshot.get("id_token", ""),
        "session_token": snapshot.get("session_token", ""),
        "session_expires_at": _iso(snapshot.get("session_expires_at")),
        "session_refresh_status": session_refresh_status,
        "plan_type": snapshot.get("plan_type", ""),
        "proxy_id": snapshot.get("proxy_id"),
        "proxy_url": snapshot.get("proxy_url", ""),
    }


def _build_client_from_account(
    account: ChatGPTAccount,
    ctx,
    *,
    load_cookies: bool = True,
    proxy_url_override: str = "",
):
    from backend.integrations.chatgpt.chatgpt_client import ChatGPTClient, curl_requests

    proxy_url = str(proxy_url_override or account.proxy_url or "").strip()
    client = ChatGPTClient(
        proxy=proxy_url or None,
        verbose=False,
        browser_mode=str(settings.get("chatgpt_browser_mode", "protocol") or "protocol"),
    )
    client._log = lambda msg: ctx.log(str(msg or ""))

    metadata = json_loads(account.metadata_json, fallback={}) or {}
    fingerprint = json_loads(account.browser_fingerprint_json, fallback={}) or {}
    fp = _fingerprint_from_dict(fingerprint, account.user_agent)
    if fp is not None:
        client._apply_fingerprint(fp)
        client.device_id = str(metadata.get("device_id") or client.device_id)
        client.session = curl_requests.Session(impersonate=client.impersonate)
        proxies = build_requests_proxy_config(proxy_url)
        if proxies:
            client.session.proxies = proxies
        client.session.headers.update(fp.base_headers())
        seed_oai_device_cookie(client.session, client.device_id)

    if load_cookies:
        cookies = json_loads(account.cookies_json, fallback=[]) or []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if not name or not value:
                continue
            client.session.cookies.set(
                name,
                value,
                domain=str(cookie.get("domain") or ".chatgpt.com"),
                path=str(cookie.get("path") or "/"),
            )
    return client


def _acquire_relogin_proxy(ctx: Any, account: ChatGPTAccount) -> tuple[int | None, str, str]:
    proxy_region = str(
        ctx.input.get("proxy_region")
        or ctx.input.get("region")
        or settings.get("workpool.chatgpt_session.proxy_region", "")
        or ""
    ).strip()
    resource = ctx.acquire(
        "proxy_pool",
        hint={
            "stage": "chatgpt_session",
            "account_id": int(account.id or 0),
            "region": proxy_region,
            "exclude_proxy_id": int(account.proxy_id or 0),
            "exclude_url": str(account.proxy_url or ""),
        },
    )
    payload = resource.payload or {}
    proxy_url = str(payload.get("url") or resource.id or "").strip()
    proxy_id = int(payload.get("proxy_id") or 0) or None
    region = str(payload.get("region") or proxy_region or "")
    if not proxy_url:
        raise RuntimeError("chatgpt_session relogin proxy_pool returned empty proxy url")
    return proxy_id, proxy_url, region


def _fingerprint_from_dict(value: dict[str, Any], fallback_user_agent: str) -> BrowserFingerprint | None:
    if not isinstance(value, dict) or not value:
        return None
    fields = {
        "browser_type": str(value.get("browser_type") or ("firefox" if "Firefox/" in str(fallback_user_agent or "") else "chrome")),
        "impersonate": str(value.get("impersonate") or ""),
        "user_agent": str(value.get("user_agent") or fallback_user_agent or ""),
        "sec_ch_ua": str(value.get("sec_ch_ua") or ""),
        "accept_language": str(value.get("accept_language") or "en-US,en;q=0.9"),
        "platform": str(value.get("platform") or "Windows"),
        "platform_version": str(value.get("platform_version") or ""),
        "chrome_full": str(value.get("chrome_full") or ""),
        "viewport_width": int(value.get("viewport_width") or 1920),
        "viewport_height": int(value.get("viewport_height") or 1080),
    }
    if not fields["impersonate"] or not fields["user_agent"]:
        return None
    return BrowserFingerprint(**fields)


def _persist_session_data(
    account_id: int,
    session_data: dict[str, Any],
    identity_state: dict[str, Any],
    me_data: dict[str, Any] | None = None,
    *,
    status: str = "refreshed",
) -> dict[str, Any]:
    access_token = str(session_data.get("accessToken") or "").strip()
    if not access_token:
        raise RuntimeError("/api/auth/session response missing accessToken")
    me_data = me_data if isinstance(me_data, dict) else {}
    user = session_data.get("user") if isinstance(session_data.get("user"), dict) else {}
    account = session_data.get("account") if isinstance(session_data.get("account"), dict) else {}
    auth_payload = (decode_jwt_payload(access_token).get("https://api.openai.com/auth") or {})
    chatgpt_account_id = str(account.get("id") or auth_payload.get("chatgpt_account_id") or "").strip()
    chatgpt_user_id = str(user.get("id") or auth_payload.get("chatgpt_user_id") or auth_payload.get("user_id") or "").strip()
    expires_at = _parse_datetime(session_data.get("expires"))
    plan_type = _plan_from_session_or_me(account, me_data)
    session_token = str(session_data.get("sessionToken") or "").strip()
    now = utcnow()

    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise RuntimeError(f"account {account_id} not found")
        metadata = json_loads(row.metadata_json, fallback={}) or {}
        row.access_token = access_token
        if chatgpt_account_id:
            row.account_id = chatgpt_account_id
            row.workspace_id = chatgpt_account_id
        if session_token:
            row.session_token = session_token
        if identity_state.get("cookies"):
            row.cookies_json = json_dumps(identity_state.get("cookies") or [])
        if identity_state.get("local_storage") is not None:
            row.local_storage_json = json_dumps(identity_state.get("local_storage") or {})
        if identity_state.get("browser_fingerprint"):
            row.browser_fingerprint_json = json_dumps(identity_state.get("browser_fingerprint") or {})
        if identity_state.get("user_agent"):
            row.user_agent = str(identity_state.get("user_agent") or "")
        row.session_expires_at = expires_at
        row.session_refresh_status = status
        row.last_session_refresh_at = now
        row.last_error = ""
        if plan_type:
            row.plan_type = plan_type
        metadata.update({
            "auth_provider": session_data.get("authProvider") or metadata.get("auth_provider") or "",
            "expires": session_data.get("expires") or "",
            "user_id": chatgpt_user_id,
            "user": user,
            "account": account,
            "backend_me": me_data,
            "session_refreshed_at": now.isoformat(),
        })
        row.metadata_json = json_dumps(metadata)
        row.updated_at = now
        s.add(row)
        s.commit()
        s.refresh(row)
        return _account_snapshot(row)


def _validate_access_token(client: Any, access_token: str, ctx: Any, *, label: str) -> tuple[bool, dict[str, Any] | str]:
    ok, me_or_error = client.fetch_backend_me(access_token, max_attempts=2, retry_delay=1.0)
    if ok:
        plan = _plan_from_me(me_or_error if isinstance(me_or_error, dict) else {})
        ctx.log("/backend-api/me access token valid", payload={"source": label, "plan_type": plan})
        return True, me_or_error if isinstance(me_or_error, dict) else {}
    return False, str(me_or_error or "/backend-api/me validation failed")


def _record_session_valid(account_id: int, me_data: dict[str, Any], status: str) -> dict[str, Any]:
    now = utcnow()
    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            raise RuntimeError(f"account {account_id} not found")
        metadata = json_loads(row.metadata_json, fallback={}) or {}
        plan_type = _plan_from_me(me_data)
        row.session_refresh_status = status
        row.last_session_refresh_at = now
        row.last_error = ""
        if plan_type:
            row.plan_type = plan_type
        metadata.update({
            "backend_me": me_data if isinstance(me_data, dict) else {},
            "session_validated_at": now.isoformat(),
        })
        row.metadata_json = json_dumps(metadata)
        row.updated_at = now
        row.last_error = ""
        s.add(row)
        s.commit()
        s.refresh(row)
        return _account_snapshot(row)


def _build_otp_provider(email: str):
    from backend.integrations.mail.email_service import MicrosoftEmailService

    return MicrosoftEmailService(extra_config={"fixed_email": str(email or "").strip()})


def _plan_from_session_or_me(account: dict[str, Any], me_data: dict[str, Any]) -> str:
    return _normalize_plan_type(str(account.get("planType") or account.get("plan_type") or ""), _plan_from_me(me_data))


def _plan_from_me(me_data: dict[str, Any]) -> str:
    if not isinstance(me_data, dict):
        return ""
    workspace_plan_type = ""
    orgs = ((me_data.get("orgs") or {}).get("data") if isinstance(me_data.get("orgs"), dict) else []) or []
    if isinstance(orgs, list):
        for org in orgs:
            if not isinstance(org, dict):
                continue
            org_settings = org.get("settings") if isinstance(org.get("settings"), dict) else {}
            workspace_plan_type = str(org_settings.get("workspace_plan_type") or "").strip()
            if workspace_plan_type:
                break
    return _normalize_plan_type(str(me_data.get("plan_type") or ""), workspace_plan_type)


def _normalize_plan_type(*values: str) -> str:
    raw = " ".join(str(value or "") for value in values).strip().lower()
    if not raw:
        return ""
    for plan in ("enterprise", "team", "plus", "pro", "free"):
        if plan in raw:
            return plan
    return raw.split()[0]


def _token_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_deactivated_error(error: Any) -> bool:
    return is_account_deactivated_message("", str(error or ""))


def _record_session_failure(account_id: int, error: str) -> None:
    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            return
        row.session_refresh_status = "failed"
        row.last_session_refresh_at = utcnow()
        row.last_error = str(error or "")
        if _is_deactivated_error(error):
            row.status = ACCOUNT_STATUS_BANNED
        row.updated_at = utcnow()
        s.add(row)


def _expires_soon(value: datetime | None, refresh_before_seconds: int) -> bool:
    if value is None:
        return False
    compare = value
    if compare.tzinfo is None:
        compare = compare.replace(tzinfo=timezone.utc)
    return compare <= utcnow() + timedelta(seconds=max(0, int(refresh_before_seconds or 0)))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _read_int_setting(key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(settings.get(key, str(default)))
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
