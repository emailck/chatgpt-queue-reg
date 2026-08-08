"""Workspace join request/accept stage.

Backend implementation of the browser-side workflow:
  GET /api/auth/session -> accessToken
  POST /backend-api/accounts/{workspace_id}/invites/{request|accept}

This stage is intended to run after `sso_oauth` so the invited/sub account's
OAuth access token is available, but it can also be used standalone with an
explicit access_token, refresh_token_id, or account_id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import time
import uuid
from datetime import timedelta
from typing import Any, Optional

import requests
from sqlalchemy import select as sa_select
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.access_token import AccessTokenAccount
from backend.models.account import ChatGPTAccount
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.schemas.stage_io import WorkspaceJoinInput, WorkspaceJoinOutput

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
CHATGPT_BASE = "https://chatgpt.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
_ALLOWED_ROUTES = {"request", "accept"}


@stage(
    name="workspace_join",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=WorkspaceJoinInput,
    output_schema=WorkspaceJoinOutput,
    description="Use a sub-account access token to request joining or accept invites for one or more workspaces.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.workspace_join."), **extra_config}

    route = str(
        payload.get("route")
        or payload.get("workspace_route")
        or payload.get("join_route")
        or config.get("route")
        or "request"
    ).strip().lower()
    if route not in _ALLOWED_ROUTES:
        raise RuntimeError(f"workspace_join route must be one of {sorted(_ALLOWED_ROUTES)}, got {route!r}")

    workspace_ids_all = _resolve_workspace_ids(payload, config)
    workspace_ids = _select_workspace_ids(workspace_ids_all, payload, config, ctx)
    if not workspace_ids:
        raise RuntimeError("workspace_join requires workspace_ids/workspace_id or resolvable workspace_account_ids")

    token_bundle = _resolve_token_bundle(payload)
    access_token = str(token_bundle.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("workspace_join missing access_token; run after sso_oauth or provide access_token/refresh_token_id/account_id")

    proxy_url = str(payload.get("proxy_url") or ctx.effective_proxy_url() or config.get("proxy_url") or "").strip()
    if not proxy_url and _as_bool(payload.get("acquire_proxy", config.get("acquire_proxy", False))):
        try:
            proxy_resource = ctx.acquire("proxy_pool", hint={"stage": "workspace_join"})
            proxy_payload = proxy_resource.payload or {}
            proxy_url = str(proxy_payload.get("url") or proxy_resource.id or "").strip()
            ctx.attach_proxy(proxy_id=int(proxy_payload.get("proxy_id") or 0) or None, proxy_url=proxy_url)
        except Exception as exc:
            ctx.log(f"proxy_pool acquire failed, continue without proxy: {exc}", level="warning")

    verify_tls = not _as_bool(payload.get("insecure", config.get("insecure", False)))
    dry_run = _as_bool(payload.get("dry_run", config.get("dry_run", False)))
    refresh_before = _as_bool(payload.get("refresh_before_request", config.get("refresh_before_request", True)), default=True)
    interval_ms = _read_int(payload.get("interval_ms", config.get("interval_ms")), default=1500, minimum=0, maximum=300000)
    max_retries = _read_int(payload.get("max_retries", config.get("max_retries")), default=3, minimum=0, maximum=10)
    retry_backoff_ms = _read_int(payload.get("retry_backoff_ms", config.get("retry_backoff_ms")), default=5000, minimum=0, maximum=300000)
    allow_partial = _as_bool(payload.get("allow_partial", config.get("allow_partial", False)))
    switch_after_join = _as_bool(payload.get("switch_after_join", config.get("switch_after_join", True)), default=True)
    switch_all_workspaces = _as_bool(payload.get("switch_all_workspaces", config.get("switch_all_workspaces", False)))
    device_id = str(payload.get("device_id") or config.get("device_id") or uuid.uuid4()).strip()
    language = str(payload.get("language") or config.get("language") or "en-US").strip() or "en-US"

    token_info = _decode_token_info(access_token, str(token_bundle.get("id_token") or ""))
    ctx.log("starting workspace_join", payload={
        "route": route,
        "workspace_count": len(workspace_ids),
        "workspace_ids": [_short_ws(x) for x in workspace_ids],
        "workspace_total_candidates": len(workspace_ids_all),
        "pick_mode": _workspace_pick_mode(payload, config),
        "email": token_info.get("email") or payload.get("email") or payload.get("sso_email") or "",
        "token_account_id": token_info.get("account_id") or token_bundle.get("account_id") or "",
        "proxy_provided": bool(proxy_url),
        "dry_run": dry_run,
        "switch_after_join": switch_after_join,
        "switch_all_workspaces": switch_all_workspaces,
    })

    if refresh_before and token_bundle.get("refresh_token"):
        ctx.check_cancelled()
        refreshed = _refresh_access_token(str(token_bundle.get("refresh_token") or ""), proxy_url, verify_tls=verify_tls)
        if refreshed and refreshed.get("access_token"):
            access_token = str(refreshed.get("access_token") or access_token)
            token_bundle["access_token"] = access_token
            token_bundle["id_token"] = str(refreshed.get("id_token") or token_bundle.get("id_token") or "")
            if refreshed.get("refresh_token"):
                token_bundle["refresh_token"] = str(refreshed.get("refresh_token") or "")
            _persist_refreshed_token(token_bundle, refreshed)
            ctx.log("workspace_join refreshed OAuth access_token")
        else:
            ctx.log("workspace_join access_token refresh failed; continue with existing access_token", level="warning")

    if dry_run:
        ctx.update_result({
            "route": route,
            "workspace_ids": workspace_ids,
            "requested_count": len(workspace_ids),
            "success_count": 0,
            "failed_count": 0,
            "dry_run": True,
            "results": [],
            "access_token": access_token,
            "refresh_token": token_bundle.get("refresh_token", ""),
            "id_token": token_bundle.get("id_token", ""),
            "refresh_token_id": token_bundle.get("refresh_token_id"),
        })
        ctx.log("workspace_join dry-run completed")
        return

    sess = _build_session(proxy_url)
    results: list[dict[str, Any]] = []
    ok_count = 0
    for idx, ws_id in enumerate(workspace_ids, 1):
        ctx.check_cancelled()
        if idx > 1 and interval_ms:
            time.sleep(interval_ms / 1000)
        ok, item, access_token = _send_one(
            ctx,
            sess,
            ws_id,
            route,
            access_token,
            token_bundle,
            device_id=device_id,
            language=language,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
            proxy_url=proxy_url,
            verify_tls=verify_tls,
        )
        if ok:
            ok_count += 1
        results.append(item)

    failed_count = len(workspace_ids) - ok_count
    switched_session: dict[str, Any] = {}
    workspace_sessions: list[dict[str, Any]] = []
    successful_workspace_ids = [str(item.get("workspace_id") or "") for item in results if item.get("ok") and item.get("workspace_id")]
    switched_workspace_id = successful_workspace_ids[0] if successful_workspace_ids else ""
    local_account_id = _to_int(payload.get("account_id") or ctx.account_id or token_bundle.get("local_account_id"))
    if switch_after_join and successful_workspace_ids and local_account_id:
        switch_ids = successful_workspace_ids if switch_all_workspaces else [switched_workspace_id]
        for ws_id in switch_ids:
            current_switched = _switch_chatgpt_workspace_session(
                ctx,
                account_id=local_account_id,
                workspace_id=ws_id,
                proxy_url=proxy_url,
                device_id=device_id,
                language=language,
                verify_tls=verify_tls,
            )
            if current_switched.get("ok"):
                info = {
                    "workspace_id": ws_id,
                    "access_token": current_switched.get("access_token", ""),
                    "plan_type": current_switched.get("plan_type", ""),
                    "ok": True,
                }
                workspace_sessions.append(info)
                if not switched_session:
                    switched_session = current_switched
                    switched_workspace_id = ws_id
            else:
                workspace_sessions.append({"workspace_id": ws_id, "ok": False, "error": current_switched.get("error", "")})

    output = {
        "account_id": payload.get("account_id") or ctx.account_id,
        "email": payload.get("email") or payload.get("sso_email") or token_info.get("email") or "",
        "sso_email": payload.get("sso_email") or payload.get("email") or token_info.get("email") or "",
        "route": route,
        "workspace_ids": workspace_ids,
        "requested_count": len(workspace_ids),
        "success_count": ok_count,
        "failed_count": failed_count,
        "results": results,
        "access_token": access_token,
        "refresh_token": token_bundle.get("refresh_token", ""),
        "id_token": token_bundle.get("id_token", ""),
        "refresh_token_id": token_bundle.get("refresh_token_id"),
        "workspace_joined": ok_count == len(workspace_ids),
        "switched_workspace_id": switched_workspace_id,
        "workspace_session_switched": bool(switched_session.get("ok")),
        "workspace_session_access_token": switched_session.get("access_token", ""),
        "workspace_session_plan_type": switched_session.get("plan_type", ""),
        "workspace_sessions": workspace_sessions,
    }
    ctx.update_result(output)
    ctx.log("workspace_join completed", payload={"route": route, "success_count": ok_count, "total": len(workspace_ids)})
    if failed_count and not allow_partial:
        raise RuntimeError(f"workspace_join partial failure: {ok_count}/{len(workspace_ids)} succeeded")



def _switch_chatgpt_workspace_session(
    ctx: JobContext,
    *,
    account_id: int,
    workspace_id: str,
    proxy_url: str,
    device_id: str,
    language: str,
    verify_tls: bool,
) -> dict[str, Any]:
    """Switch cached chatgpt.com web session to the joined workspace.

    This mirrors the browser request:
      GET /api/auth/session?exchange_workspace_token=true&workspace_id=...&reason=setCurrentAccount

    It intentionally persists only cookies/current workspace metadata.  The
    following chatgpt_session stage still fetches and validates the AT via the
    normal /api/auth/session path, avoiding a forced email relogin.
    """
    ws_id = str(workspace_id or "").strip()
    if not account_id or not ws_id:
        return {"ok": False, "error": "missing account_id/workspace_id"}
    with Session(engine) as s:
        account = s.get(ChatGPTAccount, int(account_id))
        if account is None:
            return {"ok": False, "error": f"account {account_id} not found"}
        cookies = _load_cookie_rows(account.cookies_json)
        metadata = _loads_dict(account.metadata_json)
        user_agent = str(account.user_agent or metadata.get("user_agent") or UA)
        client_version = str(metadata.get("oai_client_version") or metadata.get("client_version") or "")
        stored_device_id = str(metadata.get("device_id") or device_id or uuid.uuid4())
    if not cookies:
        ctx.log("workspace_join switch skipped: account has no cached chatgpt cookies", level="warning", payload={"account_id": account_id, "workspace_id": ws_id})
        return {"ok": False, "error": "missing cached cookies"}

    sess = _build_session(proxy_url)
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name or not value:
            continue
        sess.cookies.set(name, value, domain=str(cookie.get("domain") or ".chatgpt.com"), path=str(cookie.get("path") or "/"))

    url = f"{CHATGPT_BASE}/api/auth/session"
    headers = {
        "accept": "*/*",
        "accept-language": language or "en-US",
        "oai-device-id": stored_device_id,
        "priority": "u=1, i",
        "referer": CHATGPT_BASE + "/",
        "user-agent": user_agent,
        "x-openai-target-path": "/api/auth/session",
        "x-openai-target-route": "/api/auth/session",
    }
    if client_version:
        headers["oai-client-version"] = client_version
    ctx.log("workspace_join switching ChatGPT session workspace", payload={"account_id": account_id, "workspace_id": ws_id})
    try:
        resp = sess.get(
            url,
            params={"exchange_workspace_token": "true", "workspace_id": ws_id, "reason": "setCurrentAccount"},
            headers=headers,
            timeout=30,
            verify=verify_tls,
        )
        text = resp.text or ""
        if resp.status_code != 200:
            ctx.log(f"workspace_join switch workspace -> HTTP {resp.status_code}: {text[:180]}", level="warning")
            return {"ok": False, "status_code": resp.status_code, "error": text[:500]}
        try:
            data = resp.json()
        except Exception as exc:
            ctx.log(f"workspace_join switch workspace returned non-json: {exc}", level="warning")
            return {"ok": False, "status_code": resp.status_code, "error": f"non-json: {exc}"}
        access_token = str(data.get("accessToken") or "").strip()
        info = _decode_token_info(access_token, "") if access_token else {}
        _persist_switched_workspace_state(account_id, sess, ws_id, data, stored_device_id)
        ctx.log("workspace_join switched ChatGPT session workspace", payload={
            "account_id": account_id,
            "workspace_id": ws_id,
            "has_access_token": bool(access_token),
            "plan_type": info.get("plan_type") or "",
        })
        return {"ok": True, "status_code": resp.status_code, "access_token": access_token, "plan_type": info.get("plan_type") or "", "session": data}
    except Exception as exc:
        ctx.log(f"workspace_join switch workspace network error: {exc}", level="warning")
        return {"ok": False, "error": str(exc)}


def _persist_switched_workspace_state(account_id: int, sess: requests.Session, workspace_id: str, session_data: dict[str, Any], device_id: str) -> None:
    cookies = []
    for cookie in sess.cookies:
        cookies.append({
            "name": getattr(cookie, "name", ""),
            "value": getattr(cookie, "value", ""),
            "domain": getattr(cookie, "domain", "") or ".chatgpt.com",
            "path": getattr(cookie, "path", "") or "/",
            "expires": getattr(cookie, "expires", None),
            "secure": bool(getattr(cookie, "secure", False)),
        })
    now = utcnow()
    with session_scope() as s:
        row = s.get(ChatGPTAccount, int(account_id))
        if row is None:
            return
        metadata = _loads_dict(row.metadata_json)
        if cookies:
            row.cookies_json = json.dumps(cookies, ensure_ascii=False)
        metadata.update({
            "current_workspace_id": workspace_id,
            "workspace_session_switched_at": now.isoformat(),
            "workspace_session_expires": session_data.get("expires") or "",
            "device_id": device_id or metadata.get("device_id") or "",
        })
        row.metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)
        row.updated_at = now
        s.add(row)


def _load_cookie_rows(raw: str) -> list[dict[str, Any]]:
    value = _json_load(raw, [])
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _loads_dict(raw: str) -> dict[str, Any]:
    value = _json_load(raw, {})
    return value if isinstance(value, dict) else {}


def _json_load(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback

def _send_one(
    ctx: JobContext,
    sess: requests.Session,
    ws_id: str,
    route: str,
    access_token: str,
    token_bundle: dict[str, Any],
    *,
    device_id: str,
    language: str,
    max_retries: int,
    retry_backoff_ms: int,
    proxy_url: str,
    verify_tls: bool,
) -> tuple[bool, dict[str, Any], str]:
    url = f"{CHATGPT_BASE}/backend-api/accounts/{ws_id}/invites/{route}"
    last_item: dict[str, Any] = {}
    for attempt in range(max_retries + 1):
        ctx.check_cancelled()
        headers = {
            "accept": "*/*",
            "authorization": f"Bearer {access_token}",
            "content-type": "application/json",
            "oai-device-id": device_id,
            "oai-language": language,
            "origin": CHATGPT_BASE,
            "referer": CHATGPT_BASE + "/",
            "user-agent": UA,
        }
        ctx.log(f"workspace_join -> POST /accounts/{_short_ws(ws_id)}/invites/{route} (attempt {attempt + 1})")
        try:
            resp = sess.post(url, headers=headers, data="", timeout=30, verify=verify_tls)
            text = resp.text or ""
            ok = 200 <= resp.status_code < 300
            last_item = {
                "workspace_id": ws_id,
                "route": route,
                "attempt": attempt + 1,
                "status_code": resp.status_code,
                "ok": ok,
                "response": text[:1000],
            }
            if ok:
                ctx.log(f"workspace_join {_short_ws(ws_id)} -> HTTP {resp.status_code}", payload=last_item)
                return True, last_item, access_token
            ctx.log(f"workspace_join {_short_ws(ws_id)} -> HTTP {resp.status_code}: {text[:180]}", level="warning", payload=last_item)

            if resp.status_code in {401, 403} and token_bundle.get("refresh_token"):
                refreshed = _refresh_access_token(str(token_bundle.get("refresh_token") or ""), proxy_url, verify_tls=verify_tls)
                if refreshed and refreshed.get("access_token"):
                    access_token = str(refreshed.get("access_token") or access_token)
                    token_bundle["access_token"] = access_token
                    token_bundle["id_token"] = str(refreshed.get("id_token") or token_bundle.get("id_token") or "")
                    if refreshed.get("refresh_token"):
                        token_bundle["refresh_token"] = str(refreshed.get("refresh_token") or "")
                    _persist_refreshed_token(token_bundle, refreshed)
                    ctx.log("workspace_join refreshed AT after auth failure")
        except Exception as exc:
            last_item = {"workspace_id": ws_id, "route": route, "attempt": attempt + 1, "status_code": 0, "ok": False, "error": str(exc)}
            ctx.log(f"workspace_join {_short_ws(ws_id)} network error: {exc}", level="warning", payload=last_item)

        if attempt < max_retries and retry_backoff_ms:
            time.sleep((retry_backoff_ms * (attempt + 1)) / 1000)

    return False, last_item, access_token


def _resolve_token_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    bundle = {
        "access_token": str(payload.get("access_token") or payload.get("oauth_access_token") or "").strip(),
        "id_token": str(payload.get("id_token") or payload.get("oauth_id_token") or "").strip(),
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "account_id": str(payload.get("chatgpt_account_id") or payload.get("codex_account_id") or "").strip(),
        "refresh_token_id": payload.get("refresh_token_id"),
        "local_account_id": payload.get("account_id"),
    }
    rt_id = _to_int(payload.get("refresh_token_id"))
    local_account_id = _to_int(payload.get("account_id"))

    with Session(engine) as s:
        if rt_id:
            row = s.get(OpenAIRefreshToken, rt_id)
            if row:
                _merge_rt_row(bundle, row)
        if local_account_id and (not bundle["access_token"] or not bundle["refresh_token"]):
            row = s.exec(sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == local_account_id)).scalars().first()
            if row:
                _merge_rt_row(bundle, row)
            acct = s.get(ChatGPTAccount, local_account_id)
            if acct:
                bundle["access_token"] = bundle["access_token"] or acct.access_token or ""
                bundle["id_token"] = bundle["id_token"] or acct.id_token or ""
                bundle["refresh_token"] = bundle["refresh_token"] or acct.refresh_token or ""
                bundle["account_id"] = bundle["account_id"] or acct.account_id or ""
        access_token_account_id = _to_int(payload.get("access_token_account_id"))
        if access_token_account_id and not bundle["access_token"]:
            row = s.get(AccessTokenAccount, access_token_account_id)
            if row:
                bundle["access_token"] = row.access_token or ""
                bundle["id_token"] = row.id_token or ""
                bundle["refresh_token"] = row.refresh_token or ""
                bundle["account_id"] = bundle["account_id"] or row.account_id or ""
    return bundle


def _merge_rt_row(bundle: dict[str, Any], row: OpenAIRefreshToken) -> None:
    bundle["access_token"] = bundle["access_token"] or row.oauth_access_token or ""
    bundle["id_token"] = bundle["id_token"] or row.oauth_id_token or ""
    bundle["refresh_token"] = bundle["refresh_token"] or row.refresh_token or ""
    bundle["refresh_token_id"] = int(row.id or 0)
    bundle["local_account_id"] = bundle.get("local_account_id") or row.account_id


def _resolve_workspace_ids(payload: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Resolve target mother workspace ids.

    Important: prior stages such as `register` also emit `workspace_id`, but
    that value is the newly-created child account id/workspace-like id, not the
    mother workspace to join. Therefore WorkPool/global config must win over
    carry-over `workspace_id`. Explicit per-job `workspace_ids` still wins.
    """
    explicit_values: list[Any] = []
    for key in ("workspace_ids", "workspaceIds"):
        value = payload.get(key)
        if value not in (None, "", []):
            explicit_values.append(value)
    if explicit_values:
        out = _split_many(explicit_values)
    else:
        config_values: list[Any] = []
        for key in ("workspace_ids", "workspace_id"):
            value = config.get(key)
            if value not in (None, "", []):
                config_values.append(value)
        if config_values:
            out = _split_many(config_values)
        else:
            # Only use singular payload workspace_id as a last-resort explicit
            # standalone input. In register -> workspace_join chains this is
            # usually carry-over from register and should not override config.
            out = _split_many([payload.get("workspace_id"), payload.get("workspaceId")])

    account_ids = _split_many([
        payload.get("workspace_account_ids"),
        payload.get("workspace_account_id"),
        config.get("workspace_account_ids"),
        config.get("workspace_account_id"),
    ])
    if account_ids:
        out.extend(_lookup_workspace_ids(account_ids))

    return _dedupe([x for x in out if x])


def _workspace_pick_mode(payload: dict[str, Any], config: dict[str, Any]) -> str:
    raw = str(
        payload.get("workspace_pick_mode")
        or payload.get("pick_mode")
        or config.get("pick_mode")
        or config.get("workspace_pick_mode")
        or "all"
    ).strip().lower().replace("-", "_")
    if raw in {"random", "random_one", "one_random"}:
        return "random_one"
    if raw in {"roundrobin", "round_robin", "rr"}:
        return "round_robin"
    return "all"


def _select_workspace_ids(ids: list[str], payload: dict[str, Any], config: dict[str, Any], ctx: JobContext) -> list[str]:
    ids = _dedupe([x for x in ids if x])
    if len(ids) <= 1:
        return ids
    mode = _workspace_pick_mode(payload, config)
    if mode == "all":
        return ids
    if mode == "random_one":
        selected = random.choice(ids)
        ctx.log("workspace_join selected workspace random_one", payload={"workspace_id": selected, "workspace_index": ids.index(selected), "candidate_count": len(ids)})
        return [selected]
    if mode == "round_robin":
        selected, index = _next_round_robin_workspace(ids)
        ctx.log("workspace_join selected workspace round_robin", payload={"workspace_id": selected, "workspace_index": index, "candidate_count": len(ids)})
        return [selected]
    return ids


def _next_round_robin_workspace(ids: list[str]) -> tuple[str, int]:
    if not ids:
        return "", 0
    fingerprint = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]
    key = f"workpool.workspace_join.round_robin_index.{fingerprint}"
    # Atomic enough for SQLite-backed single app process: use one session and
    # commit the increment before sending the request. If concurrent callers
    # collide, the worst case is two accounts sharing one workspace once.
    from backend.core.settings import SettingItem
    with session_scope() as s:
        row = s.get(SettingItem, key)
        current = 0
        if row is not None:
            try:
                current = int(str(row.value or "0"))
            except Exception:
                current = 0
        index = current % len(ids)
        next_value = str((current + 1) % len(ids))
        if row is None:
            s.add(SettingItem(key=key, value=next_value))
        else:
            row.value = next_value
            s.add(row)
    return ids[index], index


def _lookup_workspace_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    with Session(engine) as s:
        for raw in ids:
            iid = _to_int(raw)
            if not iid:
                continue
            acct = s.get(ChatGPTAccount, iid)
            if acct and acct.workspace_id:
                out.append(acct.workspace_id)
                continue
            ata = s.get(AccessTokenAccount, iid)
            if ata and ata.workspace_id:
                out.append(ata.workspace_id)
    return out


def _split_many(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple, set)):
            out.extend(_split_many(list(value)))
            continue
        text = str(value or "")
        for sep in ["\r\n", "\r", "\n", ";", "，"]:
            text = text.replace(sep, ",")
        for part in text.split(","):
            item = _normalize_workspace_id(part.strip())
            if item:
                out.append(item)
    return out


def _normalize_workspace_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Some imported workspace lists carry an auxiliary suffix after "__".
    # The ChatGPT invite API path only accepts the UUID account/workspace id.
    if "__" in text:
        text = text.split("__", 1)[0].strip()
    m = __import__("re").search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)
    return m.group(0).lower() if m else text


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _build_session(proxy: str = "") -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    if proxy:
        proxies = build_requests_proxy_config(proxy)
        if proxies:
            sess.proxies.update(proxies)
    return sess


def _refresh_access_token(refresh_token: str, proxy: str = "", *, verify_tls: bool = True) -> Optional[dict[str, Any]]:
    sess = _build_session(proxy)
    try:
        resp = sess.post(
            TOKEN_ENDPOINT,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            data={"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh_token},
            timeout=30,
            verify=verify_tls,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None


def _persist_refreshed_token(bundle: dict[str, Any], refreshed: dict[str, Any]) -> None:
    rt_id = _to_int(bundle.get("refresh_token_id"))
    if not rt_id:
        return
    now = utcnow()
    expires_in = _to_int(refreshed.get("expires_in")) or 3600
    with session_scope() as s:
        row = s.get(OpenAIRefreshToken, rt_id)
        if not row:
            return
        if refreshed.get("access_token"):
            row.oauth_access_token = str(refreshed.get("access_token") or "")
        if refreshed.get("id_token"):
            row.oauth_id_token = str(refreshed.get("id_token") or "")
        if refreshed.get("refresh_token"):
            row.refresh_token = str(refreshed.get("refresh_token") or "")
        row.oauth_access_expires_at = now + timedelta(seconds=expires_in)
        row.updated_at = now
        s.add(row)


def _decode_token_info(access_token: str, id_token: str = "") -> dict[str, Any]:
    for tok in (access_token, id_token):
        jwt = _jwt_decode(tok)
        if not jwt:
            continue
        auth = jwt.get("https://api.openai.com/auth", {}) if isinstance(jwt, dict) else {}
        profile = jwt.get("https://api.openai.com/profile", {}) if isinstance(jwt, dict) else {}
        return {
            "account_id": auth.get("chatgpt_account_id") or auth.get("account_id") or jwt.get("account_id") or "",
            "email": profile.get("email") or jwt.get("email") or "",
            "plan_type": auth.get("chatgpt_plan_type") or "",
            "exp": jwt.get("exp") or 0,
        }
    return {}


def _jwt_decode(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore"))
    except Exception:
        return {}


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


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _short_ws(ws_id: str) -> str:
    value = str(ws_id or "")
    return value[:8] if len(value) > 8 else value
