"""Create ChatGPT Admin Codex access tokens for team workspaces.

Flow mirrored from the browser userscript:
  1. Reuse cached chatgpt.com cookies from a registered account.
  2. Switch session to a target team workspace via /api/auth/session with
     exchange_workspace_token=true.
  3. POST /backend-api/wham/auth-credentials with the switched workspace AT.
  4. Emit workspace_sessions whose access_token is the newly-created Codex
     credential token, so sub2api_sync uploads that token instead of the normal
     ChatGPT session AT.
"""
from __future__ import annotations

import random
import string
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from backend.core.db import engine
from backend.core.job_context import JobContext
from backend.core.settings import settings
from backend.core.proxy import resolve_workpool_proxy_template
from backend.core.stages import stage
from backend.models.account import ChatGPTAccount
from backend.schemas.stage_io import CodexTokenInput, CodexTokenOutput
from backend.stages.workspace_join import (
    CHATGPT_BASE,
    UA,
    _as_bool,
    _build_session,
    _decode_token_info,
    _dedupe,
    _load_cookie_rows,
    _loads_dict,
    _lookup_workspace_ids,
    _persist_switched_workspace_state,
    _read_int,
    _split_many,
    _to_int,
    _workpool_config,
)

AUTH_CREDENTIALS_URL = f"{CHATGPT_BASE}/backend-api/wham/auth-credentials"
SESSION_URL = f"{CHATGPT_BASE}/api/auth/session"
DEFAULT_CODEX_SCOPE = "chatgpt.workspace.feature.allow-codex-local-access.access"
DEFAULT_TOKEN_TTL_SECONDS = 7_776_000  # 90 days


@stage(
    name="codex_token",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=CodexTokenInput,
    output_schema=CodexTokenOutput,
    description="Switch to team workspace, create Codex Admin token, and pass it to sub2api_sync.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.codex_token."), **extra_config}

    account_id = _to_int(payload.get("account_id") or ctx.account_id)
    if not account_id:
        raise RuntimeError("codex_token requires account_id")
    ctx.attach_account(account_id)

    workspace_ids = _resolve_workspace_ids(payload, config)
    if not workspace_ids:
        raise RuntimeError("codex_token requires workspace_ids/workspace_id")

    proxy_url = str(payload.get("proxy_url") or ctx.effective_proxy_url() or config.get("proxy_url") or "").strip()
    if not proxy_url:
        rendered = resolve_workpool_proxy_template("codex_token", payload=payload, extra=config)
        if rendered is not None and rendered.url:
            proxy_url = rendered.url
            ctx.attach_proxy(proxy_id=None, proxy_url=proxy_url)
            ctx.log("codex_token dynamic proxy rendered", payload={"provider": rendered.provider, "region": rendered.region, "ttl": rendered.ttl, "sid": rendered.sid})
    verify_tls = not _as_bool(payload.get("insecure", config.get("insecure", False)))
    dry_run = _as_bool(payload.get("dry_run", config.get("dry_run", False)))
    interval_ms = _read_int(payload.get("interval_ms", config.get("interval_ms")), default=1500, minimum=0, maximum=300000)
    max_retries = _read_int(payload.get("max_retries", config.get("max_retries")), default=3, minimum=0, maximum=10)
    retry_backoff_ms = _read_int(payload.get("retry_backoff_ms", config.get("retry_backoff_ms")), default=5000, minimum=0, maximum=300000)
    ttl = _read_int(payload.get("ttl", config.get("ttl")), default=DEFAULT_TOKEN_TTL_SECONDS, minimum=60, maximum=31536000)
    token_name_prefix = str(payload.get("token_name") or config.get("token_name") or "codex").strip() or "codex"
    scopes = _resolve_scopes(payload, config)
    language = str(payload.get("language") or config.get("language") or "en-US").strip() or "en-US"
    device_id = str(payload.get("device_id") or config.get("device_id") or uuid.uuid4()).strip()
    allow_partial = _as_bool(payload.get("allow_partial", config.get("allow_partial", False)))
    upload_multiple = _as_bool(payload.get("upload_multiple", config.get("upload_multiple", True)), default=True)

    account = _load_account(account_id)
    cookies = _load_cookie_rows(str(account.get("cookies_json") or ""))
    if not cookies:
        raise RuntimeError(f"account {account_id} has no cached chatgpt cookies")

    ctx.log("starting codex_token", payload={
        "account_id": account_id,
        "email": account.get("email") or "",
        "workspace_count": len(workspace_ids),
        "workspace_ids": [ws[:8] for ws in workspace_ids],
        "ttl": ttl,
        "scopes": scopes,
        "proxy_provided": bool(proxy_url),
        "dry_run": dry_run,
    })

    if dry_run:
        ctx.update_result({
            "account_id": account_id,
            "email": account.get("email") or "",
            "workspace_ids": workspace_ids,
            "success_count": 0,
            "failed_count": 0,
            "workspace_sessions": [],
            "upload_multiple": upload_multiple,
            "dry_run": True,
        })
        return

    sess = _session_from_account(account, cookies, proxy_url)
    results: list[dict[str, Any]] = []
    workspace_sessions: list[dict[str, Any]] = []
    ok_count = 0
    for idx, workspace_id in enumerate(workspace_ids, 1):
        ctx.check_cancelled()
        if idx > 1 and interval_ms:
            time.sleep(interval_ms / 1000)
        ok, item = _switch_and_create_token(
            ctx,
            sess,
            account=account,
            account_id=account_id,
            workspace_id=workspace_id,
            device_id=device_id,
            language=language,
            ttl=ttl,
            scopes=scopes,
            token_name_prefix=token_name_prefix,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
            verify_tls=verify_tls,
        )
        results.append(item)
        if ok:
            ok_count += 1
            workspace_sessions.append(_workspace_session_from_item(item, account))

    failed_count = len(workspace_ids) - ok_count
    output = {
        "account_id": account_id,
        "email": account.get("email") or "",
        "workspace_ids": workspace_ids,
        "success_count": ok_count,
        "failed_count": failed_count,
        "results": results,
        "workspace_sessions": workspace_sessions,
        "upload_multiple": upload_multiple,
    }
    ctx.update_result(output)
    ctx.log("codex_token completed", payload={"success_count": ok_count, "total": len(workspace_ids)})
    if failed_count and not allow_partial:
        raise RuntimeError(f"codex_token partial failure: {ok_count}/{len(workspace_ids)} succeeded")


def _resolve_workspace_ids(payload: dict[str, Any], config: dict[str, Any]) -> list[str]:
    values = [
        payload.get("workspace_ids"), payload.get("workspaceIds"), payload.get("workspace_id"), payload.get("workspaceId"),
        config.get("workspace_ids"), config.get("workspace_id"),
    ]
    out = _split_many(values)
    account_ids = _split_many([
        payload.get("workspace_account_ids"), payload.get("workspace_account_id"),
        config.get("workspace_account_ids"), config.get("workspace_account_id"),
    ])
    if account_ids:
        out.extend(_lookup_workspace_ids(account_ids))
    return _dedupe([x for x in out if x])


def _resolve_scopes(payload: dict[str, Any], config: dict[str, Any]) -> list[str]:
    raw = payload.get("scopes") or config.get("scopes") or ""
    scopes: list[str] = []
    if isinstance(raw, list):
        scopes = [str(x or "").strip() for x in raw if str(x or "").strip()]
    elif str(raw or "").strip():
        text = str(raw or "")
        for sep in ["\r\n", "\r", "\n", ";", "，"]:
            text = text.replace(sep, ",")
        scopes = [x.strip() for x in text.split(",") if x.strip()]
    scope = str(payload.get("scope") or config.get("scope") or "").strip()
    if scope:
        scopes.append(scope)
    if not scopes:
        scopes = [DEFAULT_CODEX_SCOPE]
    return list(dict.fromkeys(scopes))


def _load_account(account_id: int) -> dict[str, Any]:
    with Session(engine) as s:
        row = s.get(ChatGPTAccount, int(account_id))
        if row is None:
            raise RuntimeError(f"account {account_id} not found")
        metadata = _loads_dict(row.metadata_json)
        return {
            "id": int(row.id or 0),
            "email": str(row.email or ""),
            "cookies_json": str(row.cookies_json or ""),
            "metadata": metadata,
            "user_agent": str(row.user_agent or metadata.get("user_agent") or UA),
            "device_id": str(metadata.get("device_id") or ""),
            "client_version": str(metadata.get("oai_client_version") or metadata.get("client_version") or ""),
        }


def _session_from_account(account: dict[str, Any], cookies: list[dict[str, Any]], proxy_url: str):
    sess = _build_session(proxy_url)
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or not value:
            continue
        sess.cookies.set(name, value, domain=str(cookie.get("domain") or ".chatgpt.com"), path=str(cookie.get("path") or "/"))
    return sess


def _switch_and_create_token(
    ctx: JobContext,
    sess,
    *,
    account: dict[str, Any],
    account_id: int,
    workspace_id: str,
    device_id: str,
    language: str,
    ttl: int,
    scopes: list[str],
    token_name_prefix: str,
    max_retries: int,
    retry_backoff_ms: int,
    verify_tls: bool,
) -> tuple[bool, dict[str, Any]]:
    last: dict[str, Any] = {}
    for attempt in range(max_retries + 1):
        try:
            ctx.check_cancelled()
            ctx.log(f"codex_token switch workspace {workspace_id[:8]} (attempt {attempt + 1})")
            session_data = _switch_workspace(sess, account, workspace_id, device_id=device_id, language=language, verify_tls=verify_tls)
            jwt = str(session_data.get("accessToken") or "").strip()
            session_account = session_data.get("account") if isinstance(session_data.get("account"), dict) else {}
            session_user = session_data.get("user") if isinstance(session_data.get("user"), dict) else {}
            chatgpt_account_id = str(session_account.get("id") or workspace_id).strip()
            email = str(session_user.get("email") or account.get("email") or "").strip()
            if not jwt:
                raise RuntimeError("switched session missing accessToken")
            if not chatgpt_account_id:
                raise RuntimeError("switched session missing account.id")
            _persist_switched_workspace_state(account_id, sess, workspace_id, session_data, device_id or str(account.get("device_id") or ""))

            token_name = _random_name(token_name_prefix)
            ctx.log("codex_token creating auth credential", payload={"workspace_id": workspace_id, "name": token_name})
            credential = _create_codex_credential(
                sess,
                jwt=jwt,
                account_id=chatgpt_account_id,
                name=token_name,
                scopes=scopes,
                ttl=ttl,
                verify_tls=verify_tls,
            )
            codex_token = str(credential.get("access_token") or "").strip()
            if not codex_token:
                raise RuntimeError("auth-credentials response missing access_token")
            info = _decode_token_info(jwt, "")
            last = {
                "ok": True,
                "workspace_id": workspace_id,
                "account_id": chatgpt_account_id,
                "email": email,
                "access_token": codex_token,
                "session_access_token": jwt,
                "plan_type": str(info.get("plan_type") or ""),
                "expires_at": _format_expires_at(credential.get("expires_at")),
                "expires_at_unix": int(credential.get("expires_at") or 0),
                "last_refresh": _format_now(),
                "credential_id": credential.get("credential_id"),
                "credential_name": credential.get("name") or token_name,
                "scopes": scopes,
                "ttl": ttl,
                "token_type": "codex",
                "websockets": True,
            }
            ctx.log("codex_token created", payload={
                "workspace_id": workspace_id,
                "credential_id": last.get("credential_id"),
                "credential_name": last.get("credential_name"),
                "expires_at": last.get("expires_at"),
            })
            return True, last
        except Exception as exc:
            last = {"ok": False, "workspace_id": workspace_id, "attempt": attempt + 1, "error": str(exc)}
            ctx.log(f"codex_token {workspace_id[:8]} failed: {exc}", level="warning", payload=last)
            if attempt < max_retries and retry_backoff_ms:
                time.sleep((retry_backoff_ms * (attempt + 1)) / 1000)
    return False, last


def _switch_workspace(sess, account: dict[str, Any], workspace_id: str, *, device_id: str, language: str, verify_tls: bool) -> dict[str, Any]:
    headers = {
        "accept": "*/*",
        "accept-language": language or "en-US",
        "oai-device-id": str(account.get("device_id") or device_id or uuid.uuid4()),
        "priority": "u=1, i",
        "referer": CHATGPT_BASE + "/",
        "user-agent": str(account.get("user_agent") or UA),
        "x-openai-target-path": "/api/auth/session",
        "x-openai-target-route": "/api/auth/session",
    }
    if account.get("client_version"):
        headers["oai-client-version"] = str(account.get("client_version"))
    resp = sess.get(
        SESSION_URL,
        params={"exchange_workspace_token": "true", "workspace_id": workspace_id, "reason": "setCurrentAccount"},
        headers=headers,
        timeout=30,
        verify=verify_tls,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"switch workspace HTTP {resp.status_code}: {(resp.text or '')[:300]}")
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"switch workspace returned non-json: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("switch workspace returned non-object JSON")
    return data


def _create_codex_credential(sess, *, jwt: str, account_id: str, name: str, scopes: list[str], ttl: int, verify_tls: bool) -> dict[str, Any]:
    resp = sess.post(
        AUTH_CREDENTIALS_URL,
        headers={
            "accept": "*/*",
            "content-type": "application/json",
            "authorization": f"Bearer {jwt}",
            "chatgpt-account-id": account_id,
            "origin": CHATGPT_BASE,
            "referer": CHATGPT_BASE + "/admin/access-tokens?modal=create",
            "user-agent": UA,
        },
        json={"name": name, "scopes": scopes, "ttl": int(ttl)},
        timeout=30,
        verify=verify_tls,
    )
    text = resp.text or ""
    if resp.status_code >= 400:
        raise RuntimeError(f"create credential HTTP {resp.status_code}: {text[:300]}")
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"create credential returned non-json: {text[:300]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("create credential returned non-object JSON")
    return data


def _workspace_session_from_item(item: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": str(item.get("workspace_id") or item.get("account_id") or ""),
        "access_token": str(item.get("access_token") or ""),
        "plan_type": str(item.get("plan_type") or ""),
        "email": str(item.get("email") or account.get("email") or ""),
        "account_id": str(item.get("account_id") or item.get("workspace_id") or ""),
        "token_type": "codex",
        "websockets": bool(item.get("websockets", True)),
        "credential_id": item.get("credential_id"),
        "credential_name": item.get("credential_name"),
        "expires_at": item.get("expires_at"),
        "expires_at_unix": item.get("expires_at_unix"),
        "last_refresh": item.get("last_refresh"),
    }


def _random_name(prefix: str, length: int = 8) -> str:
    base = "".join(ch for ch in str(prefix or "codex").strip().lower() if ch.isalnum() or ch in {"-", "_"}) or "codex"
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))
    return f"{base}-{suffix}"


def _format_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _format_expires_at(value: Any) -> str:
    try:
        ts = int(value or 0)
    except Exception:
        ts = 0
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
