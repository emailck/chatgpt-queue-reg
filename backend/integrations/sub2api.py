"""Configurable sub2api client used as the OpenAI account pool backend."""
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
        self.account_import_path = _setting(
            "sub2api_openai_import_path",
            "SUB2API_OPENAI_IMPORT_PATH",
            "/api/v1/admin/accounts/data",
        )
        self.openai_import_path = self.account_import_path
        self.account_export_path = _setting(
            "sub2api_account_export_path",
            "SUB2API_ACCOUNT_EXPORT_PATH",
            "/api/v1/admin/accounts/data",
        )
        self.account_list_path = _setting(
            "sub2api_account_list_path",
            "SUB2API_ACCOUNT_LIST_PATH",
            "/api/v1/admin/accounts",
        )
        self.account_status_path = _setting(
            "sub2api_account_status_path",
            "SUB2API_ACCOUNT_STATUS_PATH",
            "/api/v1/admin/accounts/{account_id}",
        )
        self.account_update_path = _setting(
            "sub2api_account_update_path",
            "SUB2API_ACCOUNT_UPDATE_PATH",
            "/api/v1/admin/accounts/{account_id}",
        )
        self.account_bulk_update_path = _setting(
            "sub2api_account_bulk_update_path",
            "SUB2API_ACCOUNT_BULK_UPDATE_PATH",
            "/api/v1/admin/accounts/bulk-update",
        )
        self.sold_group_id = _setting_int("sub2api_sold_group_id", "SUB2API_SOLD_GROUP_ID", 0)
        self.timeout = _setting_int("sub2api_timeout_seconds", "SUB2API_TIMEOUT_SECONDS", 30)

    def ensure_configured(self) -> None:
        if not self.base_url:
            raise Sub2ApiNotConfigured("sub2api_base_url is not configured")

    def import_account_data(self, payload: dict[str, Any], *, skip_default_group_bind: bool = True) -> dict[str, Any]:
        self.ensure_configured()
        return self._request(
            "POST",
            self.account_import_path,
            json={
                "data": _account_data_payload(payload),
                "skip_default_group_bind": bool(skip_default_group_bind),
            },
        )

    def import_openai_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.import_account_data(payload)

    def upsert_openai_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.import_account_data(payload)

    def export_account_data(
        self,
        *,
        ids: list[int] | None = None,
        include_proxies: bool = False,
        platform: str = "",
        account_type: str = "",
        status: str = "",
        search: str = "",
    ) -> dict[str, Any]:
        self.ensure_configured()
        params: dict[str, Any] = {"include_proxies": "true" if include_proxies else "false"}
        if ids:
            params["ids"] = ",".join(str(int(item)) for item in ids if int(item) > 0)
        if platform:
            params["platform"] = platform
        if account_type:
            params["type"] = account_type
        if status:
            params["status"] = status
        if search:
            params["search"] = search
        return self._request("GET", self.account_export_path, params=params)

    def list_accounts(
        self,
        *,
        platform: str = "",
        account_type: str = "",
        status: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        self.ensure_configured()
        params: dict[str, Any] = {"page": max(1, int(page)), "page_size": max(1, min(int(page_size), 1000))}
        if platform:
            params["platform"] = platform
        if account_type:
            params["type"] = account_type
        if status:
            params["status"] = status
        if search:
            params["search"] = search
        return self._request("GET", self.account_list_path, params=params)

    def update_openai_account(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_configured()
        path = self.account_update_path.format(account_id=account_id)
        return self._request("PUT", path, json=payload)

    def clear_openai_account_error(self, account_id: str) -> dict[str, Any]:
        self.ensure_configured()
        path = f"{self.account_status_path.format(account_id=account_id).rstrip('/')}/clear-error"
        return self._request("POST", path)

    def set_openai_account_schedulable(self, account_id: str, schedulable: bool) -> dict[str, Any]:
        self.ensure_configured()
        path = f"{self.account_status_path.format(account_id=account_id).rstrip('/')}/schedulable"
        return self._request("POST", path, json={"schedulable": bool(schedulable)})

    def reset_openai_account_status(self, account_id: str) -> dict[str, Any]:
        self.clear_openai_account_error(account_id)
        return self.set_openai_account_schedulable(account_id, True)

    def move_openai_account_to_group(self, account_id: str, group_id: int) -> dict[str, Any]:
        if not int(group_id or 0):
            return {}
        return self.update_openai_account(
            account_id,
            {
                "group_ids": [int(group_id)],
                "confirm_mixed_channel_risk": True,
            },
        )

    def move_openai_accounts_to_group(self, account_ids: list[str], group_id: int) -> dict[str, Any]:
        ids = [int(item) for item in account_ids if str(item or "").strip().isdigit() and int(item) > 0]
        if not ids or not int(group_id or 0):
            return {}
        return self._request(
            "POST",
            self.account_bulk_update_path,
            json={
                "account_ids": ids,
                "group_ids": [int(group_id)],
                "confirm_mixed_channel_risk": True,
            },
        )

    def get_openai_account_status(self, account_id: str) -> dict[str, Any]:
        self.ensure_configured()
        path = self.account_status_path.format(account_id=account_id)
        return self._request("GET", path)

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = _join_url(self.base_url, path)
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.api_key:
            headers.setdefault("x-api-key", self.api_key)
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


def _account_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "exported_at": str(payload.get("exported_at") or ""),
        "proxies": list(payload.get("proxies") or []),
        "accounts": list(payload.get("accounts") or []),
    }


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
