from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests as curl_requests

from backend.core.proxy import build_requests_proxy_config

AUTH_BASE = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH_BASE}/oauth/authorize"
TOKEN_URL = f"{AUTH_BASE}/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid email profile offline_access"


@dataclass
class OAuthSession:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str
    client_id: str


def generate_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def create_oauth_session(config: dict[str, Any] | None = None) -> OAuthSession:
    values = dict(config or {})
    oauth_config = values.get("oauth") if isinstance(values.get("oauth"), dict) else {}
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(24)
    client_id = str(oauth_config.get("client_id") or values.get("oauth_client_id") or OAUTH_CLIENT_ID).strip()
    redirect_uri = str(oauth_config.get("redirect_uri") or values.get("oauth_redirect_uri") or OAUTH_REDIRECT_URI).strip()
    scope = str(oauth_config.get("scope") or values.get("oauth_scope") or OAUTH_SCOPE).strip()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return OAuthSession(
        auth_url=f"{AUTHORIZE_URL}?{urlencode(params)}",
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        client_id=client_id,
    )


def parse_callback(callback_url: str, expected_state: str) -> str:
    parsed = urlparse(str(callback_url or "").strip())
    query = parse_qs(parsed.query)
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [""])[0]
    if not code:
        raise ValueError("callback URL missing code")
    if state != expected_state:
        raise ValueError("OAuth state mismatch")
    return code


def normalize_token_response(data: dict[str, Any]) -> dict[str, Any]:
    values = dict(data or {})
    return {
        "access_token": str(values.get("access_token") or ""),
        "refresh_token": str(values.get("refresh_token") or ""),
        "id_token": str(values.get("id_token") or ""),
        "token_type": str(values.get("token_type") or ""),
        "scope": str(values.get("scope") or ""),
        "expires_in": values.get("expires_in"),
        "raw_token_response": values,
    }


def exchange_code(
    session: OAuthSession,
    callback_url: str,
    *,
    user_agent: str = "",
    proxy: str = "",
) -> dict[str, Any]:
    code = parse_callback(callback_url, session.state)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://chatgpt.com",
        "Referer": callback_url,
        "User-Agent": user_agent
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    }
    payload = {
        "grant_type": "authorization_code",
        "client_id": session.client_id,
        "code": code,
        "redirect_uri": session.redirect_uri,
        "code_verifier": session.code_verifier,
    }
    kwargs: dict[str, Any] = {"data": payload, "headers": headers, "timeout": 60, "impersonate": "chrome142"}
    proxies = build_requests_proxy_config(proxy)
    if proxies:
        kwargs["proxies"] = proxies
    response = curl_requests.post(TOKEN_URL, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"OAuth token exchange failed: HTTP {response.status_code} {response.text[:300]}")
    return normalize_token_response(response.json())
