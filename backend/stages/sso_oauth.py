"""SSO OAuth refresh-token stage.

Mirrors `openai_oauth` but replaces the email+password login flow with
an SSO-based login through external IdP redirects (WorkOS / OIDC).
"""
from __future__ import annotations

import json as _json
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.core.errors import JobCancelled
from backend.core.job_context import JobContext
from backend.core.proxy import build_requests_proxy_config
from backend.core.settings import settings
from backend.core.stages import stage
from backend.integrations.chatgpt.fingerprint import impersonate_from_user_agent
from backend.integrations.chatgpt.oauth import AUTH_BASE, AUTHORIZE_URL, OAuthSession, exchange_code, create_oauth_session as _create_oauth_session
from backend.integrations.chatgpt.sentinel_token import build_sentinel_token
from backend.integrations.chatgpt.utils import (
    FlowState,
    build_browser_headers,
    extract_flow_state,
    generate_datadog_trace,
    infer_page_type_from_url,
    normalize_flow_url,
    normalize_page_type,
    seed_oai_device_cookie,
)
from backend.schemas.stage_io import OpenAIOAuthInput, OpenAIOAuthOutput

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = '"Chromium";v="143", "Google Chrome";v="143", "Not A(Brand";v="24"'

# ---- stage registration ----------------------------------------------------


@stage(
    name="sso_oauth",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=3,
    input_schema=OpenAIOAuthInput,
    output_schema=OpenAIOAuthOutput,
    description="Run SSO-based OAuth on a registered account to obtain RT via external IdP redirects.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    pool_config = _workpool_config("workpool.sso_oauth.")
    merged_extra = {**settings.get_all(), **pool_config, **extra_config}

    proxy_url = ctx.effective_proxy_url() or ""
    if not proxy_url:
        try:
            proxy_resource = ctx.acquire("proxy_pool", hint={"stage": "sso_oauth"})
            proxy_payload = proxy_resource.payload or {}
            proxy_url = str(proxy_payload.get("url") or proxy_resource.id or "").strip()
            ctx.attach_proxy(proxy_id=int(proxy_payload.get("proxy_id") or 0) or None, proxy_url=proxy_url)
        except Exception:
            pass  # proxy is optional, proceed without if pool is empty
    sso_connection_id = str(payload.get("sso_connection_id") or extra_config.get("sso_connection_id") or merged_extra.get("sso_connection_id") or "").strip()
    sso_provider = int(payload.get("sso_provider") or extra_config.get("sso_provider") or merged_extra.get("sso_provider") or 2)
    sso_email_domain = str(payload.get("sso_email_domain") or extra_config.get("sso_email_domain") or merged_extra.get("sso_email_domain") or "").strip()
    sso_invite_code = str(payload.get("sso_invite_code") or extra_config.get("sso_invite_code") or merged_extra.get("sso_invite_code") or "").strip()

    # Build SSO login email: random_username@sso_email_domain
    if sso_email_domain:
        import secrets as _secrets
        import string as _string
        _random_user = "".join(_secrets.choice(_string.ascii_lowercase + _string.digits) for _ in range(10))
        sso_email = f"{_random_user}@{sso_email_domain.lstrip('@')}"
    else:
        raise RuntimeError("sso_oauth stage requires sso_email_domain in workpool config")

    ctx.log(
        "starting sso_oauth stage",
        payload={
            "sso_email": sso_email,
            "proxy_provided": bool(proxy_url),
            "sso_connection_id": sso_connection_id or "(auto-detect)",
            "sso_invite_code": bool(sso_invite_code),
        },
    )

    def _emit_log(message: str, level: str = "info") -> None:
        ctx.log(str(message or ""), level=level)
        try:
            ctx.check_cancelled()
        except JobCancelled:
            raise

    oauth_max_retries = _read_int_config(merged_extra, "chatgpt_oauth_max_retries", default=2, minimum=1, maximum=5)

    # Build fresh curl_cffi session (no account identity needed)
    ua = DEFAULT_UA
    sec_ch_ua = DEFAULT_SEC_CH_UA
    imp = impersonate_from_user_agent(ua, "chrome142")
    device_id = str(uuid.uuid4())
    session = _build_session(proxy_url, device_id, [], imp, ua, merged_extra)

    last_error = ""
    token_data: dict[str, Any] | None = None
    for attempt in range(oauth_max_retries):
        if attempt:
            _emit_log(f"SSO OAuth RT 获取重试 {attempt + 1}/{oauth_max_retries} ...")
            time.sleep(1)
        try:
            _emit_log("SSO OAuth: 创建 PKCE authorize session")
            oauth = _create_oauth_session(merged_extra)
            token_data = _run_sso_oauth(
                oauth, session, device_id, sso_email, ua, sec_ch_ua, imp,
                sso_connection_id, sso_provider, sso_invite_code, merged_extra, _emit_log,
            )
            refresh_token = str((token_data or {}).get("refresh_token") or "").strip()
            if not refresh_token:
                raise RuntimeError("SSO OAuth token response missing refresh_token")
            _emit_log("SSO OAuth refresh_token 获取完成")
            break
        except Exception as exc:
            last_error = str(exc)
            token_data = None
            if attempt < oauth_max_retries - 1:
                _emit_log(f"SSO OAuth RT 获取失败，准备重试: {last_error}", level="warning")
                continue

    if not token_data:
        raise RuntimeError(f"SSO OAuth 获取 refresh_token 失败: {last_error}")

    expires_in = int(token_data.get("expires_in") or 3600)
    ctx.update_result({
        "has_refresh_token": True,
        "expires_in": expires_in,
        "refresh_token": token_data.get("refresh_token", ""),
        "access_token": token_data.get("access_token", ""),
        "sub2api_status": "pending_sync",
    })
    ctx.log("sso_oauth succeeded", payload={
        "refresh_token": str(token_data.get("refresh_token", ""))[:20] + "...",
        "expires_in": expires_in,
    })


# ---- core SSO OAuth engine -------------------------------------------------


def _run_sso_oauth(
    oauth: OAuthSession,
    session,  # curl_cffi.Session
    device_id: str,
    email: str,  # SSO login email (random_user@domain)
    ua: str,
    sec_ch_ua: str,
    impersonate: str,
    sso_connection_id: str,
    sso_provider: int,
    sso_invite_code: str,
    config: dict[str, Any],
    log_fn,
) -> dict[str, Any]:
    """Run the SSO OAuth flow end-to-end."""
    import curl_cffi
    _log = log_fn

    headers = build_browser_headers(
        url=AUTHORIZE_URL,
        user_agent=ua,
        sec_ch_ua=sec_ch_ua,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        referer="https://chatgpt.com/",
        navigation=True,
    )

    # Step 1: Bootstrap OAuth
    _log("SSO OAuth: bootstrap /oauth/authorize")
    resp = session.get(AUTHORIZE_URL, params=_query_params(oauth.auth_url), headers=headers, allow_redirects=True, timeout=60)
    _log(f"SSO OAuth bootstrap -> {resp.status_code} {str(resp.url)[:120]}")
    if resp.status_code >= 400:
        raise RuntimeError(f"SSO OAuth bootstrap failed: HTTP {resp.status_code}")

    # Step 2: Authorize continue — submit email
    def _get_sentinel(flow: str) -> str:
        tok = build_sentinel_token(session, device_id, flow=flow, user_agent=ua, sec_ch_ua=sec_ch_ua, impersonate=impersonate, logger=lambda m: _log(str(m)))
        if not tok:
            raise RuntimeError(f"SSO OAuth sentinel token failed for {flow}")
        return tok

    _log("SSO OAuth: authorize/continue (email)")
    state = _authorize_continue(session, device_id, ua, sec_ch_ua, email, _get_sentinel)
    _log(f"SSO OAuth state after email: page_type={state.page_type} next={str(state.continue_url)[:120]}")

    # Step 3: Check if response contains SSO connections (auto-detect)
    # Connections may NOT be in the API response; the SSO page loads them via JS.
    # Try fetching the SSO page to extract connections if needed.
    raw = state.raw
    connections = (raw.get("connections") or
                   raw.get("page", {}).get("payload", {}).get("connections") or [])
    if not sso_connection_id and not connections and state.page_type == "sso":
        _log("SSO OAuth: fetching SSO page to extract connections...")
        try:
            sso_page = session.get(
                state.continue_url or "https://auth.openai.com/sso",
                headers=build_browser_headers(
                    url=state.continue_url or "https://auth.openai.com/sso",
                    user_agent=ua, sec_ch_ua=sec_ch_ua,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=f"{AUTH_BASE}/log-in", navigation=True,
                ),
                allow_redirects=True, timeout=30,
            )
            html = sso_page.text or ""
            # Connection is embedded as HTML-escaped JSON: &quot;connection_name&quot;:&quot;conn_xxx&quot; ... &quot;connection_provider&quot;:2
            import re as _re
            conn_match = _re.findall(r'&quot;connection_name&quot;\s*:\s*&quot;\s*(conn_[^&]+)', html)
            provider_match = _re.findall(r'&quot;connection_provider&quot;\s*:\s*(\d+)', html)
            if conn_match:
                sso_connection_id = conn_match[0].strip()
                sso_provider = int(provider_match[0]) if provider_match else 2
                _log(f"SSO OAuth: extracted connection {sso_connection_id} (provider={sso_provider}) from SSO page")
        except Exception as ex:
            _log(f"SSO OAuth: failed to extract connections from SSO page: {ex}", level="warning")

    _log(f"SSO OAuth connections: auto_detected={bool(connections)} from_config={bool(sso_connection_id)}")
    if not sso_connection_id and connections:
        # Auto-pick the first SSO connection
        for conn in connections:
            cid = str(conn.get("id") or conn.get("connection") or "").strip()
            if cid:
                sso_connection_id = cid
                sso_provider = int(conn.get("connection_provider") or conn.get("provider") or 2)
                _log(f"SSO OAuth: auto-selected connection {cid} (provider={sso_provider})")
                break

    if sso_connection_id:
        # Step 3a: Select SSO connection via authorize/continue
        _log(f"SSO OAuth: selecting SSO connection {sso_connection_id} (provider={sso_provider})")
        state = _authorize_continue_sso(session, device_id, ua, sec_ch_ua, sso_connection_id, sso_provider, _get_sentinel)
        _log(f"SSO OAuth state after sso select: page_type={state.page_type} next={str(state.continue_url)[:120]}")

        # Step 3b: Follow external SSO redirect chain
        _log("SSO OAuth: following external SSO redirects...")
        code, state = _follow_sso_chain(session, ua, sec_ch_ua, state, impersonate, _log, sso_invite_code)
        if code:
            return exchange_code(oauth, f"{oauth.redirect_uri}?code={code}&state={oauth.state}", user_agent=ua, proxy="")

        # Step 3c: After SSO redirects, handle interstitial + workspace + consent
        code, state = _handle_post_sso_flow(session, ua, sec_ch_ua, device_id, state, impersonate, _log)
        if code:
            return exchange_code(oauth, f"{oauth.redirect_uri}?code={code}&state={oauth.state}", user_agent=ua, proxy="")

        raise RuntimeError(f"SSO OAuth post-sso flow did not produce code: page_type={state.page_type}")
    else:
        # No SSO connection — fall back to standard email+password flow
        _log("SSO OAuth: no SSO connections detected, falling back to standard OAuth")
        # This shouldn't happen if user explicitly chose SSO, but handle gracefully
        raise RuntimeError("SSO OAuth: account does not support SSO (no connections in authorize/continue response)")


def _authorize_continue(session, device_id: str, ua: str, sec_ch_ua: str, email: str, get_sentinel) -> FlowState:
    url = f"{AUTH_BASE}/api/accounts/authorize/continue"
    headers = build_browser_headers(
        url=url, user_agent=ua, sec_ch_ua=sec_ch_ua,
        accept="application/json", referer=f"{AUTH_BASE}/log-in",
        origin=AUTH_BASE, content_type="application/json",
        extra_headers={"oai-device-id": device_id, "openai-sentinel-token": get_sentinel("authorize_continue")},
    )
    headers.update(generate_datadog_trace())
    resp = session.post(url, json={"username": {"kind": "email", "value": email}, "screen_hint": "login"}, headers=headers, allow_redirects=False, timeout=45)
    if resp.status_code != 200:
        raise RuntimeError(f"authorize/continue (email) failed: HTTP {resp.status_code}")
    data = resp.json()
    # Preserve raw connections for SSO detection
    return _flow_state_from_response(data, str(resp.url))


def _authorize_continue_sso(session, device_id: str, ua: str, sec_ch_ua: str, connection_id: str, provider: int, get_sentinel) -> FlowState:
    url = f"{AUTH_BASE}/api/accounts/authorize/continue"
    headers = build_browser_headers(
        url=url, user_agent=ua, sec_ch_ua=sec_ch_ua,
        accept="application/json", referer=f"{AUTH_BASE}/log-in",
        origin=AUTH_BASE, content_type="application/json",
        extra_headers={"oai-device-id": device_id, "openai-sentinel-token": get_sentinel("authorize_continue")},
    )
    headers.update(generate_datadog_trace())
    resp = session.post(url, json={"connection": connection_id, "connection_provider": provider}, headers=headers, allow_redirects=False, timeout=45)
    if resp.status_code != 200:
        raise RuntimeError(f"authorize/continue (sso) failed: HTTP {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return _flow_state_from_response(data, str(resp.url))


def _follow_sso_chain(session, ua: str, sec_ch_ua: str, state: FlowState, impersonate: str, log_fn, sso_invite_code: str = "", max_hops: int = 24) -> tuple[str, FlowState]:
    """Generic redirect follower — follows redirects natively for cookie handling."""
    current_url = state.continue_url or state.current_url
    if not current_url:
        return "", state

    referer = state.current_url or f"{AUTH_BASE}/log-in"
    for hop in range(max_hops):
        code = _extract_code_from_url(current_url)
        if code:
            return code, _state_from_url(current_url)

        h = build_browser_headers(
            url=current_url, user_agent=ua, sec_ch_ua=sec_ch_ua,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            referer=referer, navigation=True,
        )
        resp = session.get(current_url, headers=h, allow_redirects=True, timeout=45)
        last_url = str(resp.url)
        parsed_last = urlparse(last_url)
        log_fn(f"SSO follow[{hop + 1}] {resp.status_code} {last_url[:150]}")

        code = _extract_code_from_url(last_url)
        if code:
            return code, _state_from_url(last_url)

        # Check for login page with auth token
        if resp.status_code == 200 and _extract_auth_request_token(last_url):
            log_fn("SSO: landed on login page, auto-POST")
            after = _try_auto_post_login(session, ua, sec_ch_ua, last_url, last_url, sso_invite_code, log_fn)
            if after and after != last_url:
                code = _extract_code_from_url(after)
                if code:
                    return code, _state_from_url(after)
                referer = last_url
                current_url = after
                continue

        # Back at auth.openai.com — SSO complete
        if parsed_last.netloc.endswith("auth.openai.com") and resp.status_code == 200:
            try:
                if "application/json" in (resp.headers.get("content-type", "").lower()):
                    return "", extract_flow_state(resp.json(), current_url=last_url)
            except Exception:
                pass
            return "", _state_from_url(last_url)

        referer = last_url
        return "", _state_from_url(last_url)

    return "", _state_from_url(current_url)


def _sync_response_cookies(session, resp, parsed_url) -> None:
    """Manually sync Set-Cookie from a response (needed because allow_redirects=False)."""
    try:
        # curl_cffi response headers are case-insensitive dict-like
        raw_cookies = resp.headers.get("Set-Cookie") or resp.headers.get("set-cookie") or ""
        if raw_cookies and isinstance(raw_cookies, str):
            for part in raw_cookies.split(","):
                part = part.strip()
                if "=" not in part:
                    continue
                kv = part.split(";")[0].strip()
                k, _, v = kv.partition("=")
                if k and v:
                    session.cookies.set(k.strip(), v.strip(), domain=parsed_url.hostname, path="/")
    except Exception:
        pass


def _resolve_url(raw: str, base_parsed) -> str:
    """Resolve a Location header value against the response URL's origin."""
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("/"):
        return f"{base_parsed.scheme}://{base_parsed.netloc}{raw}"
    # Relative path
    return f"{base_parsed.scheme}://{base_parsed.netloc}/{raw}"


def _try_auto_post_login(session, ua, sec_ch_ua, url, referer, sso_invite_code, log_fn) -> str:
    """If the URL looks like a login page with an auth_request_token, auto-POST login."""
    if not url:
        return url
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    auth_token = params.get("t", [None])[0]
    if not auth_token:
        return url
    login_url = f"{parsed.scheme}://{parsed.netloc}/login"

    log_fn(f"SSO: auto-POST {login_url}")
    post_data = {"auth_request_token": auth_token}
    if sso_invite_code:
        post_data["invite_code"] = sso_invite_code
    headers = build_browser_headers(
        url=login_url, user_agent=ua, sec_ch_ua=sec_ch_ua,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        referer=referer, content_type="application/x-www-form-urlencoded",
    )

    post_resp = session.post(
        login_url, data=post_data, headers=headers,
        allow_redirects=False, timeout=45,
    )
    log_fn(f"SSO login POST -> {post_resp.status_code}")
    if post_resp.status_code in (301, 302, 303, 307, 308):
        loc = post_resp.headers.get("Location", "")
        if loc:
            return _resolve_url(loc, urlparse(str(post_resp.url)))
    return url


def _handle_post_sso_flow(session, ua: str, sec_ch_ua: str, device_id: str, state: FlowState, impersonate: str, log_fn) -> tuple[str, FlowState]:
    """Handle workspace select + consent after SSO redirects return to auth.openai.com."""
    current_url = state.continue_url or state.current_url
    if not current_url:
        return "", state

    # Try GET to see where we land
    h = build_browser_headers(
        url=current_url, user_agent=ua, sec_ch_ua=sec_ch_ua,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        referer="", navigation=True,
    )
    resp = session.get(current_url, headers=h, allow_redirects=False, timeout=45)
    last_url = str(resp.url)
    log_fn(f"SSO post-sso GET -> {resp.status_code} {last_url[:120]}")

    code = _extract_code_from_url(last_url)
    if code:
        return code, _state_from_url(last_url)

    # Follow redirects
    for hop in range(12):
        if resp.status_code in (301, 302, 303, 307, 308):
            location = normalize_flow_url(resp.headers.get("Location", ""), auth_base=AUTH_BASE)
            if not location:
                break
            code = _extract_code_from_url(location)
            if code:
                return code, _state_from_url(location)
            resp = session.get(location, headers=build_browser_headers(
                url=location, user_agent=ua, sec_ch_ua=sec_ch_ua,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer=last_url, navigation=True,
            ), allow_redirects=False, timeout=45)
            last_url = str(resp.url)
            code = _extract_code_from_url(last_url)
            if code:
                return code, _state_from_url(last_url)
        else:
            break

    # We're on a page. Check for workspace select or consent.
    page_lower = last_url.lower()

    if "sign-in-with-chatgpt" in page_lower or "consent" in page_lower:
        # Load session data to get workspaces
        session_data = _dump_client_auth_session(session, ua, sec_ch_ua, device_id, log_fn)
        workspaces = session_data.get("workspaces") or []
        if workspaces:
            ws_id = workspaces[0].get("id") or workspaces[0].get("workspace_id") or ""
            if ws_id:
                log_fn(f"SSO: selecting workspace {ws_id}")
                ws_resp = session.post(
                    f"{AUTH_BASE}/api/accounts/workspace/select",
                    json={"workspace_id": ws_id},
                    headers=build_browser_headers(
                        url=f"{AUTH_BASE}/api/accounts/workspace/select",
                        user_agent=ua, sec_ch_ua=sec_ch_ua,
                        accept="application/json", referer=last_url, origin=AUTH_BASE,
                        content_type="application/json",
                        extra_headers={"oai-device-id": device_id},
                    ),
                    allow_redirects=False, timeout=45,
                )
                log_fn(f"SSO workspace/select -> {ws_resp.status_code}")
                if ws_resp.status_code in (301, 302, 303, 307, 308):
                    loc = normalize_flow_url(ws_resp.headers.get("Location", ""), auth_base=AUTH_BASE)
                    code = _extract_code_from_url(loc)
                    if code:
                        return code, _state_from_url(loc)
                    return "", _state_from_url(loc)
                # Continue following
                next_state = _flow_state_from_response(ws_resp.json(), str(ws_resp.url)) if ws_resp.status_code == 200 else _state_from_url(str(ws_resp.url))
                if next_state.continue_url:
                    code, ns = _follow_sso_chain(session, ua, sec_ch_ua, next_state, impersonate, log_fn, max_hops=8)
                    if code:
                        return code, ns

    # Try extracting code from final URL
    code = _extract_code_from_url(last_url)
    return code, _state_from_url(last_url)


# ---- helpers ---------------------------------------------------------------


def _build_session(proxy_url: str, device_id: str, cookies: list, impersonate: str, ua: str, config: dict[str, Any]):
    import curl_cffi
    s = curl_cffi.requests.Session(impersonate=impersonate)
    proxies = build_requests_proxy_config(proxy_url)
    if proxies:
        s.proxies = proxies
    seed_oai_device_cookie(s, device_id)
    for c in (cookies or []):
        if isinstance(c, dict) and c.get("name") and c.get("value"):
            s.cookies.set(str(c["name"]), str(c["value"]), domain=str(c.get("domain") or ".chatgpt.com"), path=str(c.get("path") or "/"))
    return s


def _flow_state_from_response(data: dict, current_url: str) -> FlowState:
    """Extract FlowState preserving raw data (for SSO connection detection)."""
    raw = data if isinstance(data, dict) else {}
    page = raw.get("page") or {}
    payload = page.get("payload") or {}
    continue_url = normalize_flow_url(raw.get("continue_url") or payload.get("url") or "", auth_base=AUTH_BASE)
    effective_url = continue_url if continue_url else current_url
    return FlowState(
        page_type=normalize_page_type(page.get("type")) or infer_page_type_from_url(continue_url or effective_url),
        continue_url=continue_url,
        method=str(raw.get("method") or payload.get("method") or "GET").upper(),
        current_url=normalize_flow_url(effective_url, auth_base=AUTH_BASE),
        source="api",
        payload=payload if isinstance(payload, dict) else {},
        raw=raw,
    )


def _dump_client_auth_session(session, ua: str, sec_ch_ua: str, device_id: str, log_fn) -> dict[str, Any]:
    try:
        url = f"{AUTH_BASE}/api/accounts/client_auth_session_dump"
        h = build_browser_headers(
            url=url, user_agent=ua, sec_ch_ua=sec_ch_ua,
            accept="application/json", referer=f"{AUTH_BASE}/log-in", origin=AUTH_BASE,
            extra_headers={"oai-device-id": device_id},
        )
        resp = session.get(url, headers=h, allow_redirects=False, timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log_fn(f"SSO client_auth_session_dump failed: {e}")
    return {}


def _extract_code_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    for k, v in parse_qs(parsed.query).items():
        if k == "code" and v:
            return str(v[0])
    return ""


def _extract_auth_request_token(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    for k, v in parse_qs(parsed.query).items():
        if k == "t" and v:
            return str(v[0])
    return ""


def _state_from_url(url: str) -> FlowState:
    current = normalize_flow_url(url, auth_base=AUTH_BASE)
    return FlowState(
        page_type=infer_page_type_from_url(current),
        continue_url=current,
        method="GET",
        current_url=current,
        source="url",
        payload={},
        raw={},
    )


def _query_params(auth_url: str) -> dict[str, str]:
    parsed = urlparse(auth_url)
    return {k: v[0] for k, v in parse_qs(parsed.query).items()}


def _workpool_config(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in settings.get_all().items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
    return out


def _read_int_config(values: dict[str, Any], primary_key: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(values.get(primary_key, default))
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))
