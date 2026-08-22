from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_TRUTHY_CONFIG_VALUES = {"1", "true", "yes", "on", "enabled"}
_DEFAULT_SID_CHARS = string.ascii_letters + string.digits


@dataclass(frozen=True)
class RenderedProxy:
    url: str
    provider: str = ""
    region: str = ""
    ttl: int | None = None
    sid: str = ""
    source: str = ""


def _is_auth_socks_proxy(scheme: str, username: str, password: str) -> bool:
    normalized = (scheme or "").lower()
    return normalized in {"socks5", "socks5h"} and bool(username or password)


def is_truthy_config_value(value) -> bool:
    return str(value or "").strip().lower() in _TRUTHY_CONFIG_VALUES


def is_authenticated_socks5_proxy(proxy_url: Optional[str]) -> bool:
    if not proxy_url:
        return False

    value = str(proxy_url).strip()
    if not value:
        return False

    if value.startswith("{"):
        try:
            data = json.loads(value)
            if isinstance(data, dict):
                server = str(data.get("server") or "").strip()
                if not server:
                    return False
                scheme = (urlsplit(server).scheme or "").lower()
                username = str(data.get("username") or "").strip()
                password = str(data.get("password") or "").strip()
                return _is_auth_socks_proxy(scheme, username, password)
        except Exception:
            return False

    parts = urlsplit(value)
    return _is_auth_socks_proxy(
        parts.scheme or "",
        unquote(parts.username or ""),
        unquote(parts.password or ""),
    )


def normalize_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    """将 socks5:// 规范化为 socks5h://，避免本地 DNS 泄漏。"""
    if proxy_url is None:
        return None

    value = str(proxy_url).strip()
    if not value:
        return None

    parts = urlsplit(value)
    if (parts.scheme or "").lower() == "socks5":
        parts = parts._replace(scheme="socks5h")
        return urlunsplit(parts)
    return value


def get_default_proxy_url(extra: Optional[dict] = None) -> Optional[str]:
    """Read the global default proxy from settings; returns None when disabled."""
    source = extra
    if source is None:
        try:
            from backend.core.settings import settings

            source = settings.get_all()
        except Exception:
            source = {}

    if not is_truthy_config_value((source or {}).get("default_proxy_enabled")):
        return None
    rendered = render_default_proxy(extra=source)
    if rendered and rendered.url:
        return normalize_proxy_url(rendered.url)
    return normalize_proxy_url((source or {}).get("default_proxy_url"))


def resolve_effective_proxy(
    explicit_proxy: Optional[str] = None,
    *,
    extra: Optional[dict] = None,
    allow_default: bool = True,
) -> Optional[str]:
    explicit = normalize_proxy_url(explicit_proxy)
    if explicit:
        return explicit
    if allow_default:
        return get_default_proxy_url(extra)
    return None


def build_requests_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    if not proxy_url:
        return None
    normalized_proxy = proxy_url
    if proxy_url.startswith("socks5://"):
        normalized_proxy = "socks5h://" + proxy_url[len("socks5://"):]
    return {"http": normalized_proxy, "https": normalized_proxy}


def build_playwright_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    if not proxy_url:
        return None

    value = str(proxy_url).strip()
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.hostname or parts.port is None:
        server = value
        if server.startswith("socks5h://"):
            server = "socks5://" + server[len("socks5h://") :]
        return {"server": server}

    scheme = (parts.scheme or "").lower()
    if _is_auth_socks_proxy(scheme, parts.username or "", parts.password or ""):
        return None
    if scheme == "socks5h":
        scheme = "socks5"

    config = {"server": f"{scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        config["username"] = unquote(parts.username)
    if parts.password:
        config["password"] = unquote(parts.password)
    return config


# ---- dynamic proxy provider templates --------------------------------------


def _cfg(source: dict[str, Any], key: str, default: Any = "") -> Any:
    value = (source or {}).get(key)
    if value is None or value == "":
        return default
    return value


def _read_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _sid(length: int, chars: str = "") -> str:
    alphabet = str(chars or _DEFAULT_SID_CHARS)
    if not alphabet:
        alphabet = _DEFAULT_SID_CHARS
    length = max(4, min(int(length or 8), 64))
    return "".join(random.choice(alphabet) for _ in range(length))


def _source(extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if extra is not None:
        return dict(extra or {})
    try:
        from backend.core.settings import settings

        return settings.get_all()
    except Exception:
        return {}


def _provider_enabled(source: dict[str, Any], provider: str) -> bool:
    raw = source.get(f"proxy_provider.{provider}.enabled")
    if raw is None or raw == "":
        return True
    return is_truthy_config_value(raw)


def available_proxy_providers(extra: Optional[dict[str, Any]] = None) -> list[str]:
    source = _source(extra)
    names: set[str] = set()
    for key in source:
        if key.startswith("proxy_provider."):
            parts = key.split(".")
            if len(parts) >= 3 and parts[1]:
                names.add(parts[1])
    default = str(source.get("proxy_provider.default") or source.get("default_proxy_provider") or "").strip()
    if default:
        names.add(default)
    return sorted(n for n in names if _provider_enabled(source, n))


def render_proxy_provider(
    provider: str,
    *,
    region: str = "",
    ttl: Any = None,
    sid: str = "",
    extra: Optional[dict[str, Any]] = None,
    source_label: str = "provider_template",
) -> RenderedProxy | None:
    source = _source(extra)
    provider = str(provider or "").strip()
    if not provider or not _provider_enabled(source, provider):
        return None
    prefix = f"proxy_provider.{provider}."
    template = str(_cfg(source, prefix + "url_template", "") or "").strip()
    scheme = str(_cfg(source, prefix + "scheme", "http") or "http").strip()
    host = str(_cfg(source, prefix + "host", "") or "").strip()
    port = str(_cfg(source, prefix + "port", "") or "").strip()
    username_template = str(_cfg(source, prefix + "username_template", "") or "").strip()
    password = str(_cfg(source, prefix + "password", "") or "").strip()
    default_region = str(_cfg(source, prefix + "default_region", "") or "").strip()
    default_ttl = _read_int(_cfg(source, prefix + "default_ttl", 5), 5, minimum=0, maximum=100000)
    sid_length = _read_int(_cfg(source, prefix + "sid_length", 8), 8, minimum=4, maximum=64)
    sid_chars = str(_cfg(source, prefix + "sid_charset", "") or "").strip()

    chosen_region = str(region or default_region or "").strip()
    chosen_ttl = _read_int(ttl if ttl not in (None, "") else default_ttl, default_ttl, minimum=0, maximum=100000)
    chosen_sid = str(sid or "").strip() or _sid(sid_length, sid_chars)
    values = {
        "provider": provider,
        "region": chosen_region,
        "country": chosen_region,
        "ttl": chosen_ttl,
        "duration": chosen_ttl,
        "sid": chosen_sid,
    }
    if template:
        try:
            url = template.format(**values)
        except Exception:
            return None
        normalized = normalize_proxy_url(url)
        return RenderedProxy(normalized or url, provider=provider, region=chosen_region, ttl=chosen_ttl, sid=chosen_sid, source=source_label)

    if not host or not port or not username_template:
        return None
    try:
        username = username_template.format(**values)
    except Exception:
        return None
    auth = quote(username, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    url = f"{scheme}://{auth}@{host}:{port}"
    normalized = normalize_proxy_url(url)
    return RenderedProxy(normalized or url, provider=provider, region=chosen_region, ttl=chosen_ttl, sid=chosen_sid, source=source_label)


def resolve_workpool_proxy_template(
    workpool: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
    sid: str = "",
) -> RenderedProxy | None:
    source = _source(extra)
    payload = payload or {}
    wp = f"workpool.{workpool}."
    provider = str(
        payload.get("proxy_provider")
        or payload.get("provider")
        or _cfg(source, wp + "proxy_provider", "")
        or _cfg(source, "proxy_provider.default", "")
        or _cfg(source, "default_proxy_provider", "")
        or ""
    ).strip()
    if not provider:
        return None
    region = str(
        payload.get("proxy_region")
        or payload.get("region")
        or _cfg(source, wp + "proxy_region", "")
        or _cfg(source, f"proxy_provider.{provider}.default_region", "")
        or ""
    ).strip()
    ttl = (
        payload.get("proxy_ttl")
        or payload.get("proxy_duration")
        or payload.get("ttl")
        or _cfg(source, wp + "proxy_ttl", "")
        or _cfg(source, wp + "proxy_duration", "")
        or _cfg(source, f"proxy_provider.{provider}.default_ttl", "")
        or ""
    )
    return render_proxy_provider(provider, region=region, ttl=ttl, sid=sid, extra=source, source_label=f"workpool.{workpool}")


def render_default_proxy(*, extra: Optional[dict[str, Any]] = None, sid: str = "") -> RenderedProxy | None:
    source = _source(extra)
    provider = str(source.get("default_proxy_provider") or source.get("proxy_provider.default") or "").strip()
    if not provider:
        return None
    region = str(source.get("default_proxy_region") or source.get(f"proxy_provider.{provider}.default_region") or "").strip()
    ttl = source.get("default_proxy_ttl") or source.get(f"proxy_provider.{provider}.default_ttl") or ""
    return render_proxy_provider(provider, region=region, ttl=ttl, sid=sid, extra=source, source_label="default_proxy")
