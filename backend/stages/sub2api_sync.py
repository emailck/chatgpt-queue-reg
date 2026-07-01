"""sub2api_sync stage.

Synchronizes a registered ChatGPT/OpenAI account into sub2api. Refresh tokens are
optional: RT-present accounts include RT, and no-RT accounts are imported with
only their ChatGPT access token material.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.integrations.chatgpt.utils import decode_jwt_payload
from backend.integrations.sub2api import Sub2ApiNotConfigured, get_sub2api_client
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.models.payment import PaymentLink
from backend.models.sub2api_binding import Sub2ApiAccountBinding
from backend.schemas.stage_io import Sub2ApiSyncInput, Sub2ApiSyncOutput

STATUS_SYNC_INTERVAL_HOURS = 24
FAILURE_BACKOFF_HOURS = 1
INVALID_SUB2API_STATUSES = {"disabled", "error", "banned"}
RELOGIN_REQUIRED_PREFIX = "queue_relogin_required:"


@stage(
    name="sub2api_sync",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=5,
    input_schema=Sub2ApiSyncInput,
    output_schema=Sub2ApiSyncOutput,
    description="Sync ChatGPT/OpenAI account into sub2api with AT and optional RT.",
)
def run(ctx) -> None:
    account_id = int(ctx.account_id or ctx.input.get("account_id") or 0)
    refresh_token_id = int(ctx.input.get("refresh_token_id") or 0)
    if not account_id and not refresh_token_id:
        raise RuntimeError("sub2api_sync requires account_id or refresh_token_id")

    refresh_token_row = _load_refresh_token_row(refresh_token_id=refresh_token_id, account_id=account_id)
    if refresh_token_row is not None:
        refresh_token_id = int(refresh_token_row.id or 0)
        account_id = int(refresh_token_row.account_id or account_id or 0)
    if not account_id:
        raise RuntimeError("sub2api_sync could not resolve account_id")
    ctx.attach_account(account_id)

    with Session(engine) as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        if refresh_token_row is not None:
            refresh_token_row = s.get(OpenAIRefreshToken, refresh_token_id)
        account_snapshot = _snapshot_account(account)
        token_snapshot = _snapshot_refresh_token(refresh_token_row)

    mode = str(ctx.input.get("mode") or _setting("workpool.sub2api_sync.mode", "auto") or "auto").strip().lower()
    ctx.log(
        "syncing account with sub2api",
        payload={
            "account_id": account_id,
            "refresh_token_id": refresh_token_id or None,
            "mode": mode,
            "has_refresh_token": bool(token_snapshot.get("refresh_token")),
            "has_access_token": bool(account_snapshot.get("access_token")),
        },
    )

    try:
        if mode in {"", "auto", "openai", "session", "web_session"}:
            payload, auth_mode, credential_fingerprint = _build_openai_import_payload(account_snapshot, token_snapshot)
            sync_result = _sync_openai_account(
                payload,
                auth_mode=auth_mode,
                credential_fingerprint=credential_fingerprint,
                reset_remote_status=_truthy(ctx.input.get("reset_remote_status")),
            )
        else:
            raise RuntimeError(f"unsupported sub2api_sync mode: {mode}")
    except Sub2ApiNotConfigured as exc:
        _record_pending(account_id, refresh_token_id, str(exc), auth_mode=_auth_mode(account_snapshot, token_snapshot), status="pending_sync")
        ctx.log(f"sub2api not configured: {exc}", level="warning")
        ctx.update_result(_result_payload(account_id, refresh_token_id, status="pending_sync", auth_mode=_auth_mode(account_snapshot, token_snapshot)))
        return
    except Exception as exc:
        _record_failure(account_id, refresh_token_id, str(exc), auth_mode=_auth_mode(account_snapshot, token_snapshot), status="sync_failed")
        ctx.log(f"sub2api sync failed: {exc}", level="error")
        raise

    ctx.update_result(_result_payload(
        account_id,
        refresh_token_id,
        status=sync_result.get("status", ""),
        sub2api_account_id=sync_result.get("sub2api_account_id", ""),
        auth_mode=sync_result.get("auth_mode", ""),
        schedulable=sync_result.get("schedulable", True),
        relogin_required=sync_result.get("relogin_required", False),
    ))
    ctx.log(
        "sub2api sync ok",
        payload={
            "account_id": account_id,
            "sub2api_account_id": sync_result.get("sub2api_account_id", ""),
            "status": sync_result.get("status", ""),
            "auth_mode": sync_result.get("auth_mode", ""),
        },
    )


# ---- sync paths ------------------------------------------------------------


def _sync_openai_account(
    payload: dict[str, Any],
    *,
    auth_mode: str,
    credential_fingerprint: str,
    reset_remote_status: bool = False,
) -> dict[str, Any]:
    client = get_sub2api_client()
    account_doc = _first_payload_account(payload)
    account_id = int(payload.get("queue_account_id") or 0)
    refresh_token_id = int(payload.get("queue_refresh_token_id") or 0)
    existing = _find_existing_openai_account(client, account_doc, account_id=account_id)
    action = "updated" if existing else "imported"

    if existing:
        sub2api_account_id = _extract_sub2api_account_id(existing)
        if not sub2api_account_id:
            raise RuntimeError("matched sub2api account missing id")
        sync_resp = client.update_openai_account(sub2api_account_id, _account_update_payload(account_doc))
    else:
        sync_resp = client.import_account_data(_sub2api_data_payload(payload))
        existing = _find_existing_openai_account(client, account_doc, account_id=account_id)
        sub2api_account_id = _extract_sub2api_account_id(existing or sync_resp)
        if not sub2api_account_id:
            raise RuntimeError("sub2api import completed but account id could not be resolved")

    reset_resp: dict[str, Any] = {}
    if reset_remote_status:
        reset_resp = client.reset_openai_account_status(sub2api_account_id)

    status_resp = client.get_openai_account_status(sub2api_account_id)
    exported_resp: dict[str, Any] = {}
    try:
        exported_resp = client.export_account_data(ids=[int(sub2api_account_id)], include_proxies=False)
    except Exception:
        exported_resp = {}

    parsed_status = parse_sub2api_status_response(status_resp)
    status = str(parsed_status.get("status") or "synced")
    error = str(parsed_status.get("error") or "")
    relogin_required = bool(parsed_status.get("relogin_required"))
    schedulable = bool(parsed_status.get("schedulable"))
    stored_payload = {
        "action": action,
        "import_or_update": sync_resp,
        "status": status_resp,
        "reset_status": reset_resp,
        "export": exported_resp,
    }
    _record_success(
        account_id,
        refresh_token_id,
        sub2api_account_id=sub2api_account_id,
        status=status,
        payload=stored_payload,
        auth_mode=auth_mode,
        schedulable=schedulable,
        relogin_required=relogin_required,
        last_error=error,
        credential_fingerprint=credential_fingerprint,
    )
    return {
        "sub2api_account_id": sub2api_account_id,
        "status": status,
        "auth_mode": auth_mode,
        "schedulable": schedulable,
        "relogin_required": relogin_required,
    }


def _first_payload_account(payload: dict[str, Any]) -> dict[str, Any]:
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts or not isinstance(accounts[0], dict):
        raise RuntimeError("sub2api account payload missing accounts[0]")
    return accounts[0]


def _find_existing_openai_account(client: Any, account_doc: dict[str, Any], *, account_id: int) -> dict[str, Any] | None:
    bound_id = _bound_sub2api_account_id(account_id)
    if bound_id:
        try:
            detail = _account_detail_from_response(client.get_openai_account_status(bound_id))
            if detail and _is_openai_oauth_account(detail):
                return detail
        except Exception:
            pass

    credentials = account_doc.get("credentials") if isinstance(account_doc.get("credentials"), dict) else {}
    extra = account_doc.get("extra") if isinstance(account_doc.get("extra"), dict) else {}
    search_terms = [
        str(account_doc.get("name") or ""),
        str(credentials.get("email") or ""),
        str(extra.get("email") or ""),
        str(credentials.get("chatgpt_account_id") or ""),
    ]
    seen: set[str] = set()
    for term in search_terms:
        term = term.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        resp = client.list_accounts(platform="openai", account_type="oauth", search=term, page_size=50)
        match = _matching_account_from_response(resp, account_doc)
        if match:
            return match
    return None


def _bound_sub2api_account_id(account_id: int) -> str:
    if not account_id:
        return ""
    with Session(engine) as s:
        row = s.exec(
            sa_select(Sub2ApiAccountBinding)
            .where(Sub2ApiAccountBinding.chatgpt_account_id == int(account_id))
            .where(Sub2ApiAccountBinding.platform == "openai")
            .where(Sub2ApiAccountBinding.sub2api_base_url == _client_base_url())
            .order_by(Sub2ApiAccountBinding.id.desc())
        ).scalars().first()
        return str(row.sub2api_account_id or "") if row is not None else ""


def _matching_account_from_response(resp: dict[str, Any], account_doc: dict[str, Any]) -> dict[str, Any] | None:
    target_name = str(account_doc.get("name") or "").strip().lower()
    target_credentials = account_doc.get("credentials") if isinstance(account_doc.get("credentials"), dict) else {}
    target_email = str(target_credentials.get("email") or "").strip().lower()
    target_account_id = str(target_credentials.get("chatgpt_account_id") or "").strip()
    for item in _extract_account_items(resp):
        if not _is_openai_oauth_account(item):
            continue
        credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        item_name = str(item.get("name") or "").strip().lower()
        item_email = str(credentials.get("email") or extra.get("email") or "").strip().lower()
        item_account_id = str(credentials.get("chatgpt_account_id") or "").strip()
        if target_name and item_name == target_name:
            return item
        if target_email and item_email == target_email:
            return item
        if target_account_id and item_account_id == target_account_id:
            return item
    return None


def _account_detail_from_response(resp: dict[str, Any]) -> dict[str, Any] | None:
    data = _unwrap_response_data(resp)
    if isinstance(data, dict) and isinstance(data.get("account"), dict):
        data = data["account"]
    if isinstance(data, dict) and data.get("id") is not None:
        return data
    items = _extract_account_items(resp)
    return items[0] if items else None


def _extract_account_items(resp: Any) -> list[dict[str, Any]]:
    data = _unwrap_response_data(resp)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "accounts"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if data.get("id") is not None and data.get("name") is not None:
        return [data]
    return []


def _unwrap_response_data(resp: Any) -> Any:
    if isinstance(resp, dict) and "data" in resp and ("code" in resp or "message" in resp):
        return resp.get("data")
    return resp


def _is_openai_oauth_account(item: dict[str, Any]) -> bool:
    return str(item.get("platform") or "").lower() == "openai" and str(item.get("type") or "").lower() == "oauth"


def _account_update_payload(account_doc: dict[str, Any]) -> dict[str, Any]:
    credentials = dict(account_doc.get("credentials") or {})
    for key in ("access_token", "id_token", "refresh_token"):
        credentials.setdefault(key, "")
    payload: dict[str, Any] = {
        "name": str(account_doc.get("name") or ""),
        "type": str(account_doc.get("type") or "oauth"),
        "credentials": credentials,
        "extra": dict(account_doc.get("extra") or {}),
        "concurrency": int(account_doc.get("concurrency") or 1),
        "priority": int(account_doc.get("priority") or 0),
        "confirm_mixed_channel_risk": True,
    }
    if account_doc.get("notes") is not None:
        payload["notes"] = account_doc.get("notes")
    if account_doc.get("rate_multiplier") is not None:
        payload["rate_multiplier"] = account_doc.get("rate_multiplier")
    if account_doc.get("expires_at") is not None:
        payload["expires_at"] = account_doc.get("expires_at")
    if account_doc.get("auto_pause_on_expired") is not None:
        payload["auto_pause_on_expired"] = account_doc.get("auto_pause_on_expired")
    return payload


# ---- payload ---------------------------------------------------------------


def _build_openai_import_payload(account: dict[str, Any], token: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    auth_mode = _auth_mode(account, token)
    # Prefer ChatGPT Web Session AT when present; for SSO/Codex-only accounts
    # there is no ChatGPTAccount.access_token, so fall back to the OAuth AT
    # stored on OpenAIRefreshToken. sub2api expects this in credentials.access_token.
    access_token = str(account.get("access_token") or token.get("oauth_access_token") or "").strip()
    refresh_token = str(token.get("refresh_token") or "").strip()
    id_token = str(account.get("id_token") or token.get("oauth_id_token") or "").strip()
    if not access_token and not refresh_token:
        raise RuntimeError("account has no access_token or refresh_token for sub2api")

    access_payload = decode_jwt_payload(access_token) if access_token else {}
    id_payload = decode_jwt_payload(id_token) if id_token else {}
    auth_claims = access_payload.get("https://api.openai.com/auth") if isinstance(access_payload.get("https://api.openai.com/auth"), dict) else {}
    id_auth_claims = id_payload.get("https://api.openai.com/auth") if isinstance(id_payload.get("https://api.openai.com/auth"), dict) else {}
    profile_claims = access_payload.get("https://api.openai.com/profile") if isinstance(access_payload.get("https://api.openai.com/profile"), dict) else {}
    organization_id = _primary_organization_id(id_auth_claims)

    credentials: dict[str, Any] = {
        "access_token": access_token,
        "chatgpt_account_id": account.get("chatgpt_account_id") or auth_claims.get("chatgpt_account_id") or id_auth_claims.get("chatgpt_account_id") or "",
        "chatgpt_user_id": account.get("chatgpt_user_id") or auth_claims.get("chatgpt_user_id") or auth_claims.get("user_id") or id_auth_claims.get("chatgpt_user_id") or id_auth_claims.get("user_id") or "",
        "client_id": access_payload.get("client_id") or _first_audience(id_payload) or "",
        "email": account.get("email") or profile_claims.get("email") or id_payload.get("email") or "",
        "expires_at": _jwt_exp(access_payload),
        "expires_in": _jwt_expires_in(access_payload),
        "id_token": id_token,
        "organization_id": organization_id,
        "plan_type": account.get("plan_type") or auth_claims.get("chatgpt_plan_type") or id_auth_claims.get("chatgpt_plan_type") or "",
    }
    if refresh_token:
        credentials["refresh_token"] = refresh_token

    email = str(credentials.get("email") or account.get("email") or "").strip()
    account_doc = {
        "name": email or _sub2api_account_name(account),
        "platform": "openai",
        "type": "oauth",
        "credentials": _drop_empty(credentials),
        "extra": _drop_empty({
            "email": email,
            "privacy_mode": account.get("privacy_mode") or "",
        }),
        "concurrency": _read_int_setting("workpool.sub2api_sync.account_concurrency", 10, minimum=1, maximum=1000),
        "priority": _read_int_setting("workpool.sub2api_sync.account_priority", 100, minimum=0, maximum=1000),
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }
    payload = {
        "exported_at": _exported_at(),
        "proxies": [],
        "accounts": [account_doc],
        "queue_account_id": int(account.get("id") or 0),
        "queue_refresh_token_id": int(token.get("id") or 0),
    }
    return payload, auth_mode, _credential_fingerprint(account, token)


def _sub2api_account_name(account: dict[str, Any]) -> str:
    email = str(account.get("email") or "").strip()
    if email:
        return email
    account_id = int(account.get("id") or 0)
    if account_id:
        return f"chatgpt-queue-reg-{account_id}"
    key = _email_key(account.get("email") or "") or str(account.get("chatgpt_account_id") or "").strip()
    return f"chatgpt-queue-reg-{key}" if key else "chatgpt-queue-reg-account"


def _auth_mode(account: dict[str, Any], token: dict[str, Any]) -> str:
    if str(token.get("refresh_token") or "").strip():
        return "oauth_rt"
    return "access_token_only"


# ---- DB snapshots / writes -------------------------------------------------


def _load_refresh_token_row(*, refresh_token_id: int, account_id: int) -> OpenAIRefreshToken | None:
    with Session(engine) as s:
        if refresh_token_id:
            return s.get(OpenAIRefreshToken, refresh_token_id)
        if not account_id:
            return None
        return s.exec(sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == account_id)).scalars().first()


def _snapshot_account(row: ChatGPTAccount) -> dict[str, Any]:
    metadata = json_loads(row.metadata_json, fallback={}) or {}
    user = metadata.get("user") if isinstance(metadata.get("user"), dict) else {}
    raw_account = metadata.get("account") if isinstance(metadata.get("account"), dict) else {}
    auth_payload = (decode_jwt_payload(row.access_token).get("https://api.openai.com/auth") or {}) if row.access_token else {}
    plan_type = _effective_plan_type(row, raw_account)
    return {
        "id": int(row.id or 0),
        "email": str(row.email or ""),
        "chatgpt_account_id": str(row.account_id or raw_account.get("id") or auth_payload.get("chatgpt_account_id") or ""),
        "chatgpt_user_id": str(metadata.get("user_id") or user.get("id") or auth_payload.get("chatgpt_user_id") or auth_payload.get("user_id") or ""),
        "access_token": str(row.access_token or ""),
        "id_token": str(row.id_token or ""),
        "session_token": str(row.session_token or ""),
        "session_expires_at": row.session_expires_at,
        "plan_type": plan_type,
        "cookies": json_loads(row.cookies_json, fallback=[]) or [],
        "local_storage": json_loads(row.local_storage_json, fallback={}) or {},
        "browser_fingerprint": json_loads(row.browser_fingerprint_json, fallback={}) or {},
        "user_agent": str(row.user_agent or ""),
        "proxy_id": row.proxy_id,
        "proxy_url": str(row.proxy_url or ""),
        "auth_provider": str(metadata.get("auth_provider") or ""),
        "privacy_mode": str(metadata.get("privacy_mode") or raw_account.get("privacy_mode") or ""),
    }


def _effective_plan_type(row: ChatGPTAccount, raw_account: dict[str, Any]) -> str:
    current = str(row.plan_type or raw_account.get("planType") or raw_account.get("plan_type") or "").strip().lower()
    link_plan = _latest_paid_link_plan(int(row.id or 0), int(row.last_payment_link_id or 0))
    if link_plan and current in {"", "free"}:
        return link_plan
    return current


def _latest_paid_link_plan(account_id: int, last_payment_link_id: int) -> str:
    if not account_id:
        return ""
    with Session(engine) as s:
        link = s.get(PaymentLink, last_payment_link_id) if last_payment_link_id else None
        if link is None:
            link = s.exec(
                sa_select(PaymentLink)
                .where(PaymentLink.account_id == account_id)
                .where(PaymentLink.status.in_(["paid_unknown", "empty_payment_pending"]))
                .order_by(PaymentLink.id.desc())
            ).scalars().first()
    plan = str(link.plan or "").strip().lower() if link is not None else ""
    if plan in {"plus", "team"}:
        _persist_account_plan_type(account_id, plan)
        return plan
    return ""


def _persist_account_plan_type(account_id: int, plan_type: str) -> None:
    if not account_id or plan_type not in {"plus", "team"}:
        return
    with session_scope() as s:
        row = s.get(ChatGPTAccount, account_id)
        if row is None:
            return
        current = str(row.plan_type or "").strip().lower()
        if current in {"", "free"}:
            row.plan_type = plan_type
            row.updated_at = utcnow()
            s.add(row)


def _snapshot_refresh_token(row: OpenAIRefreshToken | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": int(row.id or 0),
        "account_id": int(row.account_id or 0),
        "refresh_token": str(row.refresh_token or ""),
        "oauth_access_token": str(row.oauth_access_token or ""),
        "oauth_id_token": str(row.oauth_id_token or ""),
        "oauth_access_expires_at": row.oauth_access_expires_at,
        "sub2api_account_id": str(row.sub2api_account_id or ""),
        "sub2api_status": str(row.sub2api_status or ""),
    }


def _record_success(
    account_id: int,
    refresh_token_id: int,
    *,
    sub2api_account_id: str,
    status: str,
    payload: dict[str, Any],
    auth_mode: str,
    schedulable: bool,
    relogin_required: bool,
    last_error: str,
    credential_fingerprint: str,
) -> None:
    now = utcnow()
    with session_scope() as s:
        if refresh_token_id:
            row = s.get(OpenAIRefreshToken, refresh_token_id)
            if row is not None:
                row.sub2api_account_id = sub2api_account_id
                row.sub2api_status = status
                row.sub2api_payload_json = json_dumps(_redact_payload(payload))
                row.status_checked_at = now
                if row.uploaded_at is None and sub2api_account_id:
                    row.uploaded_at = now
                row.next_sync_at = now + timedelta(hours=STATUS_SYNC_INTERVAL_HOURS)
                row.last_sync_at = now
                row.consecutive_failures = 0
                row.enabled = status.lower() not in INVALID_SUB2API_STATUSES and not relogin_required
                row.last_error = last_error if relogin_required else ""
                row.updated_at = now
                s.add(row)
        binding = _get_or_create_binding(s, account_id=account_id, base_url=_client_base_url())
        binding.sub2api_account_id = sub2api_account_id or binding.sub2api_account_id
        binding.auth_mode = auth_mode
        binding.status = status
        binding.schedulable = bool(schedulable)
        binding.last_sync_at = now
        binding.relogin_required = bool(relogin_required)
        binding.last_error = last_error if (last_error or relogin_required) else ""
        binding.credential_fingerprint = credential_fingerprint
        binding.payload_json = json_dumps(_redact_payload(payload))
        binding.updated_at = now
        s.add(binding)


def _record_pending(account_id: int, refresh_token_id: int, error: str, *, auth_mode: str, status: str) -> None:
    now = utcnow()
    with session_scope() as s:
        if refresh_token_id:
            row = s.get(OpenAIRefreshToken, refresh_token_id)
            if row is not None:
                row.sub2api_status = status
                row.last_error = error
                row.status_checked_at = now
                row.next_sync_at = now + timedelta(hours=STATUS_SYNC_INTERVAL_HOURS)
                row.updated_at = now
                s.add(row)
        binding = _get_or_create_binding(s, account_id=account_id, base_url=_client_base_url())
        binding.auth_mode = auth_mode
        binding.status = status
        binding.schedulable = False
        binding.last_sync_at = now
        binding.last_error = error
        binding.updated_at = now
        s.add(binding)


def _record_failure(account_id: int, refresh_token_id: int, error: str, *, auth_mode: str, status: str) -> None:
    now = utcnow()
    relogin_required = _is_relogin_required(status, error)
    with session_scope() as s:
        if refresh_token_id:
            row = s.get(OpenAIRefreshToken, refresh_token_id)
            if row is not None:
                row.sub2api_status = status
                row.consecutive_failures = int(row.consecutive_failures or 0) + 1
                row.last_error = error
                row.status_checked_at = now
                row.next_sync_at = now + timedelta(hours=FAILURE_BACKOFF_HOURS)
                row.updated_at = now
                s.add(row)
        binding = _get_or_create_binding(s, account_id=account_id, base_url=_client_base_url())
        binding.auth_mode = auth_mode
        binding.status = status
        binding.schedulable = False
        binding.last_sync_at = now
        binding.relogin_required = relogin_required
        binding.last_error = error
        binding.updated_at = now
        s.add(binding)


def _get_or_create_binding(s: Session, *, account_id: int, base_url: str) -> Sub2ApiAccountBinding:
    row = s.exec(
        sa_select(Sub2ApiAccountBinding)
        .where(Sub2ApiAccountBinding.chatgpt_account_id == int(account_id))
        .where(Sub2ApiAccountBinding.platform == "openai")
        .where(Sub2ApiAccountBinding.sub2api_base_url == base_url)
        .order_by(Sub2ApiAccountBinding.id.desc())
    ).scalars().first()
    if row is not None:
        return row
    now = utcnow()
    return Sub2ApiAccountBinding(
        chatgpt_account_id=int(account_id),
        platform="openai",
        sub2api_base_url=base_url,
        created_at=now,
        updated_at=now,
    )


# ---- response helpers ------------------------------------------------------


def parse_sub2api_status_response(status_resp: dict[str, Any]) -> dict[str, Any]:
    detail = _unwrap_response_data(status_resp)
    if isinstance(detail, dict) and isinstance(detail.get("account"), dict):
        detail = detail["account"]
    source = detail if isinstance(detail, dict) else status_resp
    status = _extract_status(source) or _extract_status(status_resp) or "synced"
    error = _normalize_sub2api_error(_extract_error(source) or _extract_error(status_resp))
    relogin_required = _is_relogin_required(status, error)
    schedulable = _extract_schedulable(
        source,
        default=not relogin_required and status.lower() not in INVALID_SUB2API_STATUSES,
    )
    return {
        "status": status,
        "error": error,
        "schedulable": schedulable,
        "relogin_required": relogin_required,
    }


def _extract_sub2api_account_id(resp: dict[str, Any]) -> str:
    for key in ("sub2api_account_id", "external_id", "account_id", "id", "token_id"):
        value = resp.get(key)
        if value not in (None, ""):
            return str(value)
    data = resp.get("data")
    if isinstance(data, dict):
        found = _extract_sub2api_account_id(data)
        if found:
            return found
    for key in ("account", "openai_account"):
        value = resp.get(key)
        if isinstance(value, dict):
            found = _extract_sub2api_account_id(value)
            if found:
                return found
    accounts = resp.get("accounts")
    if isinstance(accounts, list) and accounts:
        first = accounts[0]
        if isinstance(first, dict):
            return _extract_sub2api_account_id(first)
    return ""


def _extract_status(resp: dict[str, Any]) -> str:
    for key in ("status", "state"):
        value = resp.get(key)
        if value not in (None, ""):
            return str(value)
    data = resp.get("data")
    if isinstance(data, dict):
        status = _extract_status(data)
        if status:
            return status
    accounts = resp.get("accounts")
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict):
        return _extract_status(accounts[0])
    return ""


def _extract_error(resp: dict[str, Any]) -> str:
    for key in ("error_message", "error", "message", "detail"):
        value = resp.get(key)
        if value not in (None, ""):
            return str(value)
    data = resp.get("data")
    if isinstance(data, dict):
        error = _extract_error(data)
        if error:
            return error
    accounts = resp.get("accounts")
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict):
        return _extract_error(accounts[0])
    return ""


def _normalize_sub2api_error(error: str) -> str:
    value = str(error or "").strip()
    if value.lower() in {"success", "ok", "synced", "active", "alive"}:
        return ""
    return value


def _extract_schedulable(resp: dict[str, Any], *, default: bool) -> bool:
    value = resp.get("schedulable")
    if value is not None:
        return _truthy(value)
    data = resp.get("data")
    if isinstance(data, dict) and data.get("schedulable") is not None:
        return _truthy(data.get("schedulable"))
    accounts = resp.get("accounts")
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict) and accounts[0].get("schedulable") is not None:
        return _truthy(accounts[0].get("schedulable"))
    return bool(default)


def _is_relogin_required(status: str, error: str) -> bool:
    text = f"{status} {error}".strip().lower()
    return RELOGIN_REQUIRED_PREFIX in text


def _result_payload(
    account_id: int,
    refresh_token_id: int,
    *,
    status: str,
    auth_mode: str,
    sub2api_account_id: str = "",
    schedulable: bool = True,
    relogin_required: bool = False,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "refresh_token_id": refresh_token_id or None,
        "sub2api_account_id": sub2api_account_id,
        "sub2api_status": status,
        "auth_mode": auth_mode,
        "schedulable": schedulable,
        "relogin_required": relogin_required,
    }


# ---- misc ------------------------------------------------------------------


def _sub2api_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "exported_at": payload.get("exported_at") or _exported_at(),
        "proxies": list(payload.get("proxies") or []),
        "accounts": list(payload.get("accounts") or []),
    }


def _primary_organization_id(auth_claims: dict[str, Any]) -> str:
    organizations = auth_claims.get("organizations")
    if not isinstance(organizations, list):
        return ""
    first_id = ""
    for item in organizations:
        if not isinstance(item, dict):
            continue
        organization_id = str(item.get("id") or "").strip()
        if not organization_id:
            continue
        if item.get("is_default") is True:
            return organization_id
        if not first_id:
            first_id = organization_id
    return first_id


def _first_audience(payload: dict[str, Any]) -> str:
    audience = payload.get("aud")
    if isinstance(audience, list):
        return str(audience[0] or "") if audience else ""
    return str(audience or "")


def _jwt_exp(payload: dict[str, Any]) -> int | None:
    try:
        value = int(payload.get("exp") or 0)
    except Exception:
        return None
    return value or None


def _jwt_expires_in(payload: dict[str, Any]) -> int | None:
    exp = _jwt_exp(payload)
    if not exp:
        return None
    try:
        iat = int(payload.get("iat") or 0)
    except Exception:
        iat = 0
    base = iat or int(utcnow().timestamp())
    return max(0, exp - base)


def _exported_at() -> str:
    return utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _credential_fingerprint(account: dict[str, Any], token: dict[str, Any]) -> str:
    material = "\n".join([
        str(token.get("refresh_token") or ""),
        str(account.get("access_token") or ""),
        str(account.get("id_token") or token.get("oauth_id_token") or ""),
    ])
    return hashlib.sha256(material.encode("utf-8", errors="ignore")).hexdigest()


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if v not in (None, "", [], {})}


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"access_token", "refresh_token", "id_token", "session_token", "cookies", "local_storage", "browser_fingerprint", "web_session"}:
                out[key] = "***"
            else:
                out[key] = _redact_payload(item)
        return out
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _email_key(email: str) -> str:
    return str(email or "").strip().lower()


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _setting(key: str, default: str = "") -> str:
    from backend.core.settings import settings

    return settings.get(key, default)


def _read_int_setting(key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(_setting(key, str(default)))
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _client_base_url() -> str:
    try:
        return get_sub2api_client().base_url
    except Exception:
        return ""
