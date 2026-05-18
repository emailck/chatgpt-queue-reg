"""Configurable sub2api client used as the Codex RT pool backend."""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests

from backend.core.settings import settings


class Sub2ApiNotConfigured(RuntimeError):
    pass


class Sub2ApiClient:
    def __init__(self) -> None:
        self.base_url = _setting("sub2api_base_url", "SUB2API_BASE_URL").rstrip("/")
        self.api_key = _setting("sub2api_api_key", "SUB2API_API_KEY")
        self.upload_path = _setting("sub2api_upload_path", "SUB2API_UPLOAD_PATH", "/api/codex-tokens")
        self.status_path = _setting("sub2api_status_path", "SUB2API_STATUS_PATH", "/api/codex-tokens/{external_id}")
        self.timeout = _setting_int("sub2api_timeout_seconds", "SUB2API_TIMEOUT_SECONDS", 30)

    def ensure_configured(self) -> None:
        if not self.base_url:
            raise Sub2ApiNotConfigured("sub2api_base_url is not configured")

    def upload_codex_token(
        self,
        *,
        account_id: int,
        refresh_token: str,
        access_token: str = "",
        id_token: str = "",
        expires_at: str = "",
        proxy_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_configured()
        payload = {
            "account_id": account_id,
            "refresh_token": refresh_token,
            "access_token": access_token,
            "id_token": id_token,
            "expires_at": expires_at,
            "proxy_url": proxy_url,
            "metadata": metadata or {},
        }
        return self._request("POST", self.upload_path, json=payload)

    def get_codex_token_status(self, *, external_id: str) -> dict[str, Any]:
        self.ensure_configured()
        path = self.status_path.format(external_id=external_id)
        return self._request("GET", path)

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = _join_url(self.base_url, path)
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        resp = requests.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"sub2api {method} {path} status={resp.status_code}: {resp.text[:300]}")
        if not resp.text.strip():
            return {}
        try:
            body = resp.json()
        except Exception as exc:
            raise RuntimeError(f"sub2api {method} {path} returned non-json response") from exc
        return body if isinstance(body, dict) else {"data": body}


def get_sub2api_client() -> Sub2ApiClient:
    return Sub2ApiClient()


def _setting(key: str, env_key: str, default: str = "") -> str:
    return str(settings.get(key, settings.get(env_key, default)) or "").strip()


def _setting_int(key: str, env_key: str, default: int) -> int:
    try:
        return int(_setting(key, env_key, str(default)))
    except Exception:
        return default


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    suffix = str(path or "").lstrip("/")
    return urljoin(base, suffix)
