"""Codex activation stage.

Protocol-mode activation based on ../gptinvite/codex_activation_helper.py.
It is intended to run after `sso_oauth` and uses the invited account's OAuth
access/id/refresh tokens from the previous stage.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import timedelta
from typing import Any, Optional

import requests
from sqlmodel import Session

from backend.core.db import engine, session_scope
from backend.core.job_context import JobContext
from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.models.openai_refresh_token import OpenAIRefreshToken
from backend.schemas.stage_io import ActiveInput, ActiveOutput

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
CHATGPT_BASE = "https://chatgpt.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


@stage(
    name="active",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=ActiveInput,
    output_schema=ActiveOutput,
    description="Activate invited Codex account by simulating Codex Desktop protocol calls after SSO OAuth.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.active."), **extra_config}

    token_bundle = _resolve_token_bundle(payload)
    email = str(payload.get("sso_email") or payload.get("email") or _extract_email(token_bundle.get("access_token", ""), token_bundle.get("id_token", "")) or "").strip()

    proxy_url = str(payload.get("proxy_url") or ctx.effective_proxy_url() or config.get("proxy_url") or "").strip()
    if not proxy_url and _as_bool(payload.get("acquire_proxy", config.get("acquire_proxy", False))):
        try:
            proxy_resource = ctx.acquire("proxy_pool", hint={"stage": "active"})
            proxy_payload = proxy_resource.payload or {}
            proxy_url = str(proxy_payload.get("url") or proxy_resource.id or "").strip()
            ctx.attach_proxy(proxy_id=int(proxy_payload.get("proxy_id") or 0) or None, proxy_url=proxy_url)
        except Exception as exc:
            ctx.log(f"proxy_pool acquire failed, continue without proxy: {exc}", level="warning")

    verify_tls = not _as_bool(payload.get("insecure", config.get("insecure", False)))
    dry_run = _as_bool(payload.get("dry_run", config.get("dry_run", False)))
    refresh_before_activation = _as_bool(payload.get("refresh_before_activation", config.get("refresh_before_activation", True)), default=True)

    ctx.log("starting active stage", payload={
        "email": email,
        "account_id": token_bundle.get("account_id") or "(auto)",
        "refresh_token_id": token_bundle.get("refresh_token_id"),
        "proxy_provided": bool(proxy_url),
        "dry_run": dry_run,
    })

    if refresh_before_activation and token_bundle.get("refresh_token"):
        ctx.check_cancelled()
        refreshed = _refresh_access_token(str(token_bundle.get("refresh_token") or ""), proxy_url, verify_tls=verify_tls)
        if refreshed and refreshed.get("access_token"):
            token_bundle["access_token"] = refreshed.get("access_token") or token_bundle.get("access_token", "")
            token_bundle["id_token"] = refreshed.get("id_token") or token_bundle.get("id_token", "")
            if refreshed.get("refresh_token"):
                token_bundle["refresh_token"] = refreshed.get("refresh_token")
            _persist_refreshed_token(token_bundle, refreshed)
            ctx.log("active refreshed OAuth access_token")
        else:
            ctx.log("active access_token refresh failed; continue with existing access_token", level="warning")

    account_id = str(token_bundle.get("account_id") or "").strip() or _extract_account_id(
        str(token_bundle.get("access_token") or ""),
        str(token_bundle.get("id_token") or ""),
    )
    access_token = str(token_bundle.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("active stage missing access_token")
    if not account_id:
        raise RuntimeError("active stage missing chatgpt account_id")

    if dry_run:
        ctx.update_result({
            "email": email,
            "chatgpt_account_id": account_id,
            "activated": False,
            "dry_run": True,
            "success_count": 0,
            "total_count": 0,
            "endpoint_results": [],
        })
        ctx.log("active dry-run completed", payload={"email": email, "chatgpt_account_id": account_id})
        return

    success_count, endpoint_results = _run_protocol_activation(ctx, access_token, account_id, proxy_url, verify_tls=verify_tls)
    total_count = len(endpoint_results)
    activated = success_count == total_count

    ctx.update_result({
        "account_id": payload.get("account_id") or ctx.account_id,
        "email": email,
        "sso_email": email,
        "chatgpt_account_id": account_id,
        "activated": activated,
        "success_count": success_count,
        "total_count": total_count,
        "endpoint_results": endpoint_results,
        "access_token": access_token,
        "refresh_token": token_bundle.get("refresh_token", ""),
        "id_token": token_bundle.get("id_token", ""),
        "refresh_token_id": token_bundle.get("refresh_token_id"),
    })
    if not activated:
        raise RuntimeError(f"Codex active partial failure: {success_count}/{total_count} endpoints succeeded")
    ctx.log("active succeeded", payload={"email": email, "success_count": success_count, "total_count": total_count})


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
    if rt_id and (not bundle["access_token"] or not bundle["refresh_token"]):
        with Session(engine) as s:
            row = s.get(OpenAIRefreshToken, rt_id)
            if row:
                bundle["access_token"] = bundle["access_token"] or row.oauth_access_token or ""
                bundle["id_token"] = bundle["id_token"] or row.oauth_id_token or ""
                bundle["refresh_token"] = bundle["refresh_token"] or row.refresh_token or ""
                bundle["refresh_token_id"] = int(row.id or 0)
                bundle["local_account_id"] = bundle["local_account_id"] or row.account_id
    return bundle


def _run_protocol_activation(ctx: JobContext, access_token: str, account_id: str, proxy: str = "", *, verify_tls: bool = True) -> tuple[int, list[dict[str, Any]]]:
    sess = requests.Session()
    sess.trust_env = False
    if proxy:
        proxies = build_requests_proxy_config(proxy)
        if proxies:
            sess.proxies.update(proxies)

    headers = {
        "authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "content-type": "application/json",
        "oai-language": "en",
        "originator": "Codex Desktop",
        "user-agent": UA,
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br",
    }
    app_session_id = str(uuid.uuid4())
    stable_id = str(uuid.uuid4())
    endpoints = [
        ("POST", "/backend-api/wham/statsig/bootstrap", {"json": {"app_session_id": app_session_id, "app_version": "26.609.41114", "build_flavor": "prod", "locale": "zh-CN", "stable_id": stable_id, "system_name": "Windows", "system_version": "10.0.22631", "window_type": "electron"}}, "Statsig bootstrap"),
        ("GET", "/backend-api/wham/accounts/check", {}, "workspace check"),
        ("GET", "/backend-api/wham/tasks/list", {"params": {"limit": 20, "task_filter": "current"}}, "tasks list"),
        ("GET", "/backend-api/wham/usage", {}, "usage activation"),
        ("GET", "/backend-api/wham/sites/access", {}, "sites access"),
        ("POST", "/backend-api/wham/apps", {"json": {"id": 1, "jsonrpc": "2.0", "method": "tools/call", "params": {"arguments": {"limit": 20}, "name": "sites_list_projects"}}}, "projects list"),
        ("GET", "/backend-api/accounts/check/v4-2023-04-27", {}, "account check v4"),
        ("GET", f"/backend-api/accounts/{account_id}/settings", {}, "settings"),
        ("GET", f"/backend-api/accounts/{account_id}/codex_invite_promo_status", {}, "invite promo status"),
        ("GET", "/backend-api/me", {}, "me"),
        ("GET", f"/backend-api/accounts/{account_id}/remaining_balance", {}, "remaining balance"),
    ]
    results: list[dict[str, Any]] = []
    success_count = 0
    for idx, (method, path, kwargs, label) in enumerate(endpoints, 1):
        ctx.check_cancelled()
        url = CHATGPT_BASE + path
        try:
            resp = sess.request(method, url, headers=headers, timeout=20, verify=verify_tls, **kwargs)
            optional = label in {"remaining balance"}
            ok = resp.status_code == 200 or (optional and resp.status_code in {401, 403, 404})
            if ok:
                success_count += 1
            item = {"index": idx, "label": label, "method": method, "path": path, "status_code": resp.status_code, "ok": ok, "optional": optional}
            results.append(item)
            level = "info" if resp.status_code == 200 else ("warning" if optional else "warning")
            ctx.log(f"active [{idx}/{len(endpoints)}] {label} -> HTTP {resp.status_code}{' (optional)' if optional and resp.status_code != 200 else ''}", level=level, payload=item)
        except Exception as exc:
            item = {"index": idx, "label": label, "method": method, "path": path, "status_code": 0, "ok": False, "error": str(exc)}
            results.append(item)
            ctx.log(f"active [{idx}/{len(endpoints)}] {label} failed: {exc}", level="warning", payload=item)
    return success_count, results


def _refresh_access_token(refresh_token: str, proxy: str = "", *, verify_tls: bool = True) -> Optional[dict[str, Any]]:
    sess = requests.Session()
    sess.trust_env = False
    if proxy:
        proxies = build_requests_proxy_config(proxy)
        if proxies:
            sess.proxies.update(proxies)
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


def _extract_account_id(access_token: str, id_token: str = "") -> str:
    for tok in (access_token, id_token):
        jwt = _jwt_decode(tok)
        auth_info = jwt.get("https://api.openai.com/auth", {}) if isinstance(jwt, dict) else {}
        account_id = auth_info.get("chatgpt_account_id") or auth_info.get("account_id") or jwt.get("account_id")
        if account_id:
            return str(account_id)
    return ""


def _extract_email(access_token: str, id_token: str = "") -> str:
    for tok in (access_token, id_token):
        jwt = _jwt_decode(tok)
        profile = jwt.get("https://api.openai.com/profile", {}) if isinstance(jwt, dict) else {}
        email = profile.get("email") or jwt.get("email")
        if email:
            return str(email)
    return ""


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


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
