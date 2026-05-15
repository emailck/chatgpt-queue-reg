"""Camoufox / Playwright 版 Sentinel SDK token 获取辅助。

优先级：Camoufox 混合模式 → Playwright → QuickJS → 纯 Python。

Camoufox 混合模式：
  - 只启动浏览器过 Turnstile + 执行 SentinelSDK.token(flow)
  - 注入协议层的 UA、viewport、cookie（oai-did + auth session cookies）
  - 拿到 token + so_token 后立即关闭浏览器，回协议层继续
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from backend.core.browser_runtime import (
    ensure_browser_display_available,
    resolve_browser_headless,
)
from backend.core.proxy import (
    build_playwright_proxy_config,
    build_requests_proxy_config,
    is_authenticated_socks5_proxy,
)
from .fingerprint import impersonate_from_user_agent, normalize_impersonate


SENTINEL_VERSION = "20260219f9f6"
SENTINEL_SDK_URL = f"https://sentinel.openai.com/sentinel/{SENTINEL_VERSION}/sdk.js"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"


class SentinelToken(str):
    """String token with optional real so-token metadata."""

    def __new__(cls, value: str, so_token: str | None = None):
        obj = str.__new__(cls, value)
        obj.so_token = so_token
        return obj


def _flow_page_url(flow: str) -> str:
    flow_name = str(flow or "").strip().lower()
    mapping = {
        "authorize_continue": "https://auth.openai.com/create-account",
        "username_password_create": "https://auth.openai.com/create-account/password",
        "password_verify": "https://auth.openai.com/log-in/password",
        "email_otp_validate": "https://auth.openai.com/email-verification",
        "oauth_create_account": "https://auth.openai.com/about-you",
    }
    return mapping.get(flow_name, "https://auth.openai.com/about-you")


def _quickjs_script_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "js"
        / "openai_sentinel_quickjs.js"
    )


def _quickjs_debug_enabled() -> bool:
    value = os.getenv("OPENAI_SENTINEL_QUICKJS_DEBUG", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _quickjs_debug_root() -> Path:
    custom = os.getenv("OPENAI_SENTINEL_QUICKJS_DEBUG_DIR", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path(__file__).resolve().parents[2] / "artifacts" / "sentinel_quickjs"


def _safe_debug_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value or ""))
    return cleaned.strip("._")[:80] or "unknown"


def _write_debug_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_node_binary() -> str:
    custom = os.getenv("OPENAI_SENTINEL_NODE_PATH", "").strip()
    return custom or "node"


def _ensure_sdk_file(
    session: Any,
    timeout_ms: int,
    logger: Optional[Callable[[str], None]] = None,
    *,
    user_agent: Optional[str] = None,
    impersonate: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> Path:
    cache_dir = Path(tempfile.gettempdir()) / "openai-sentinel-demo" / SENTINEL_VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    sdk_file = cache_dir / "sdk.js"
    if sdk_file.exists() and sdk_file.stat().st_size > 0:
        return sdk_file

    if logger:
        try:
            logger(
                "Sentinel sdk.js 请求: "
                f"ua={user_agent or getattr(getattr(session, 'headers', {}), 'get', lambda *_: '')('User-Agent') or '-'} "
                f"impersonate={impersonate or '-'}"
            )
        except Exception:
            pass

    resp = session.get(
        SENTINEL_SDK_URL,
        headers={
            "accept": "*/*",
            "accept-language": str(accept_language or "en-US,en;q=0.5"),
            "referer": "https://auth.openai.com/",
            "sec-fetch-dest": "script",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "cross-site",
        },
        timeout=max(10, int(timeout_ms / 1000)),
    )
    resp.raise_for_status()
    content = getattr(resp, "content", b"")
    if not content:
        raise RuntimeError("下载 Sentinel sdk.js 失败: 响应为空")
    sdk_file.write_bytes(content)
    return sdk_file


def _run_quickjs_action_with_node(
    *,
    action: str,
    sdk_file: Path,
    quickjs_script: Path,
    payload: dict[str, Any],
    timeout_ms: int,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    wrapper_js = """
const fs = require('fs');
const timeoutMs = Number(process.env.OPENAI_SENTINEL_VM_TIMEOUT_MS || '10000');
const sdkFile = process.env.OPENAI_SENTINEL_SDK_FILE;
const scriptFile = process.env.OPENAI_SENTINEL_QUICKJS_SCRIPT;
const patchedSdkFile = process.env.OPENAI_SENTINEL_PATCHED_SDK_FILE || '';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', async () => {
  try {
    const payload = JSON.parse(input || '{}');
    globalThis.__payload_json = JSON.stringify(payload);
    globalThis.__sdk_source = fs.readFileSync(sdkFile, 'utf8');
    globalThis.__vm_done = false;
    globalThis.__vm_output_json = '';
    globalThis.__vm_error = '';
    const script = fs.readFileSync(scriptFile, 'utf8');
    eval(script);
    if (patchedSdkFile && globalThis.__patched_sdk_source) {
      fs.writeFileSync(patchedSdkFile, String(globalThis.__patched_sdk_source), 'utf8');
    }

    const started = Date.now();
    while (!globalThis.__vm_done) {
      if ((Date.now() - started) > timeoutMs) {
        throw new Error('QuickJS script timeout');
      }
      await new Promise((resolve) => setTimeout(resolve, 1));
    }

    if (String(globalThis.__vm_error || '').trim()) {
      throw new Error(String(globalThis.__vm_error));
    }

    process.stdout.write(String(globalThis.__vm_output_json || ''));
  } catch (err) {
    const msg = err && err.stack ? String(err.stack) : String(err);
    process.stderr.write(msg);
    process.exit(1);
  }
});
""".strip()

    merged_payload = dict(payload)
    merged_payload["action"] = action
    vm_timeout_ms = max(
        1000,
        min(timeout_ms, int(os.getenv("OPENAI_SENTINEL_QUICKJS_VM_TIMEOUT_MS", "12000"))),
    )
    process_timeout = max(5, int((vm_timeout_ms + 3000) / 1000))
    process = subprocess.run(
        [_resolve_node_binary(), "-e", wrapper_js],
        input=json.dumps(merged_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=process_timeout,
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(sdk_file),
            "OPENAI_SENTINEL_QUICKJS_SCRIPT": str(quickjs_script),
            "OPENAI_SENTINEL_PATCHED_SDK_FILE": (
                str(debug_dir / "sdk.patched.js") if debug_dir else ""
            ),
            "OPENAI_SENTINEL_VM_TIMEOUT_MS": str(vm_timeout_ms),
        },
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "unknown error").strip()
        if debug_dir:
            try:
                (debug_dir / f"{action}_stderr.txt").write_text(
                    detail,
                    encoding="utf-8",
                )
            except Exception:
                pass
        raise RuntimeError(
            f"QuickJS 执行失败(action={action}, timeout={vm_timeout_ms}ms): {detail}"
        )
    output = (process.stdout or "").strip()
    if debug_dir:
        try:
            (debug_dir / f"{action}_stdout.json").write_text(output, encoding="utf-8")
        except Exception:
            pass
    if not output:
        raise RuntimeError("QuickJS 返回空输出")
    data = json.loads(output)
    if not isinstance(data, dict):
        raise RuntimeError("QuickJS 输出不是 JSON 对象")
    return data


def _quickjs_languages_from_accept_language(accept_language: str | None, user_agent: str | None = None) -> list[str]:
    raw = str(accept_language or "").strip()
    languages: list[str] = []
    if raw:
        for part in raw.split(","):
            value = part.split(";", 1)[0].strip()
            if value and value not in languages:
                languages.append(value)
    if languages:
        return languages
    return ["en-US", "en"]


def _quickjs_payload_base(
    *,
    device_id: str,
    flow: str,
    user_agent: str | None = None,
    accept_language: str | None = None,
    viewport_width: int = 1366,
    viewport_height: int = 768,
) -> dict[str, Any]:
    languages = _quickjs_languages_from_accept_language(accept_language, user_agent)
    ua = str(user_agent or "")
    is_firefox = "Firefox" in ua
    now_ms = int(time.time() * 1000)
    react_suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=random.randint(8, 12)))
    listening_suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=random.randint(8, 12)))
    return {
        "device_id": device_id,
        "user_agent": ua,
        "screen_width": int(viewport_width or 1366),
        "screen_height": int(viewport_height or 768),
        "language": languages[0],
        "languages": languages,
        "hardware_concurrency": random.choice([10, 12]),
        "navigator_vendor": "" if is_firefox else "Google Inc.",
        "navigator_platform": "MacIntel" if "Macintosh" in ua else "Win32",
        "time_origin": now_ms - random.randint(5000, 45000),
        "performance_now": random.randint(1200, 30000),
        "performance_step": random.randint(1, 12),
        "js_heap_size_limit": None if is_firefox else 4294967296,
        "date_string": time.strftime("%a %b %d %Y %H:%M:%S GMT+0800 (Taipei Standard Time)", time.gmtime(time.time() + 8 * 3600)),
        "script_urls": [
            "https://sentinel.openai.com/backend-api/sentinel/sdk.js",
            SENTINEL_SDK_URL,
        ],
        "navigator_proto_keys": [
            "serviceWorker",
            "languages",
            "geolocation",
            "globalPrivacyControl",
            "language",
            "taintEnabled",
            "sendBeacon",
        ] if is_firefox else ["languages", "language", "sendBeacon"],
        "document_keys": [
            "location",
            f"__reactContainer${react_suffix}",
            f"_reactListening{listening_suffix}",
        ],
        "window_keys": [
            "screenTop",
            "onmousedown",
            "onpagehide",
            "$RC",
            "resizeTo",
            "resizeBy",
            "onanimationcancel",
            "__reactRouterRouteModules",
        ],
        "referrer": "https://auth.openai.com/",
    }


def _fetch_sentinel_challenge(
    session: Any,
    *,
    device_id: str,
    flow: str,
    request_p: str,
    timeout_ms: int,
    logger: Optional[Callable[[str], None]] = None,
    user_agent: Optional[str] = None,
    impersonate: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> dict[str, Any]:
    body = {"p": request_p, "id": device_id, "flow": flow}
    if logger:
        try:
            logger(
                "Sentinel challenge 请求: "
                f"{_flow_page_url(flow)} "
                f"ua={user_agent or getattr(getattr(session, 'headers', {}), 'get', lambda *_: '')('User-Agent') or '-'} "
                f"impersonate={impersonate or '-'}"
            )
        except Exception:
            pass
    resp = session.post(
        SENTINEL_REQ_URL,
        data=json.dumps(body, separators=(",", ":")),
        headers={
            "origin": "https://sentinel.openai.com",
            "referer": f"https://sentinel.openai.com/backend-api/sentinel/frame.html?sv={SENTINEL_VERSION}",
            "content-type": "text/plain;charset=UTF-8",
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": str(accept_language or "en-US,en;q=0.5"),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        },
        timeout=max(10, int(timeout_ms / 1000)),
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Sentinel challenge 响应不是 JSON 对象")
    return payload


# ---------------------------------------------------------------------------
# Asyncio event-loop isolation — prevents "Playwright Sync API inside asyncio loop"
# ---------------------------------------------------------------------------


def _isolate_event_loop(logger: Callable | None = None):
    """Context manager that replaces the current asyncio event loop with a fresh one.

    Playwright/Camoufox sync API raises "It looks like you are using Playwright
    Sync API inside the asyncio loop" when the thread inherits an event loop from
    uvicorn/FastAPI.  Creating a fresh loop per browser invocation fixes this.
    """
    _log = logger or (lambda _msg: None)
    try:
        old_loop = asyncio.get_event_loop()
    except RuntimeError:
        old_loop = None

    # Always create a fresh loop so the sync API never sees a running loop.
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    _log("Sentinel: asyncio event loop isolated for browser")

    class _IsolateScope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            try:
                pending = asyncio.all_tasks(new_loop)
                for task in pending:
                    task.cancel()
                new_loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            except Exception:
                pass
            new_loop.close()
            if old_loop is not None:
                asyncio.set_event_loop(old_loop)
            return False

    return _IsolateScope()


# ---------------------------------------------------------------------------
# Camoufox 混合模式 — 最优先，真实浏览器指纹 + Turnstile
# ---------------------------------------------------------------------------


def _parse_camoufox_proxy(proxy: str | None) -> dict | None:
    """将代理 URL 转为 Camoufox proxy 格式。认证 SOCKS5 需 gost 中继。"""
    if not proxy:
        return None
    from urllib.parse import urlparse
    pp = urlparse(proxy)
    if pp.scheme in ("socks5", "socks5h") and pp.username:
        import socket as _sock
        relay_port = 18899
        try:
            with _sock.create_connection(("127.0.0.1", relay_port), timeout=2):
                pass
            return {"server": f"socks5://127.0.0.1:{relay_port}"}
        except Exception:
            return None
    return {
        "server": f"{pp.scheme}://{pp.hostname}:{pp.port}",
        "username": pp.username or "",
        "password": pp.password or "",
    }


def _extract_cookies_from_session(session: Any) -> list[dict]:
    """从 curl_cffi session 提取 OpenAI 相关 cookies，用于注入浏览器。"""
    result: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def _allowed_domain(domain: str) -> bool:
        d = str(domain or "").lstrip(".").lower()
        return d == "openai.com" or d.endswith(".openai.com") or d == "chatgpt.com" or d.endswith(".chatgpt.com")

    def _add_cookie(name: Any, value: Any, domain: Any, path: Any = "/", secure: Any = True) -> None:
        name_s = str(name or "").strip()
        value_s = "" if value is None else str(value)
        domain_s = str(domain or "").strip()
        if not name_s or not domain_s or not _allowed_domain(domain_s):
            return
        path_s = str(path or "/")
        key = (name_s, domain_s, path_s)
        if key in seen:
            return
        seen.add(key)
        result.append(
            {
                "name": name_s,
                "value": value_s,
                "domain": domain_s,
                "path": path_s,
                "secure": bool(secure),
                "sameSite": "Lax",
            }
        )

    jar = getattr(session, "cookies", None)
    if jar is None:
        return result

    try:
        for cookie in jar:
            _add_cookie(
                getattr(cookie, "name", ""),
                getattr(cookie, "value", ""),
                getattr(cookie, "domain", ""),
                getattr(cookie, "path", "/"),
                getattr(cookie, "secure", True),
            )
    except Exception:
        pass

    for attr in ("jar", "_cookies"):
        nested = getattr(jar, attr, None)
        if not nested:
            continue
        try:
            for domain, paths in nested.items():
                for path, names in (paths or {}).items():
                    for name, cookie in (names or {}).items():
                        _add_cookie(
                            getattr(cookie, "name", name),
                            getattr(cookie, "value", ""),
                            getattr(cookie, "domain", domain),
                            getattr(cookie, "path", path),
                            getattr(cookie, "secure", True),
                        )
        except Exception:
            pass

    if hasattr(jar, "get_dict"):
        for domain in ("auth.openai.com", ".openai.com", "openai.com", "chatgpt.com", ".chatgpt.com", "sentinel.openai.com"):
            try:
                for name, value in (jar.get_dict(domain=domain) or {}).items():
                    _add_cookie(name, value, domain)
            except Exception:
                pass

    return result


def _camoufox_os_from_ua(user_agent: str) -> str:
    """根据 UA 推断 Camoufox 应模拟的 OS。"""
    ua = (user_agent or "").lower()
    if "macintosh" in ua or "mac os" in ua:
        return "macos"
    return "windows"


def _camoufox_screen_from_viewport(width: int, height: int) -> dict:
    """构造 Camoufox screen 参数。"""
    try:
        from browserforge.fingerprints import Screen
        return {"screen": Screen(max_width=width, max_height=height)}
    except ImportError:
        return {}


def get_sentinel_token_via_camoufox(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    device_id: Optional[str] = None,
    impersonate: str = "firefox147",
    user_agent: Optional[str] = None,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    protocol_session: Optional[Any] = None,
    headless: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """Camoufox 混合模式：只开浏览器过 Turnstile + SentinelSDK，拿 token + so。

    关键：注入协议层的 UA、viewport、cookie 以保证指纹一致性。
    """
    _log = logger or (lambda _msg: None)

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        _log("Sentinel Camoufox: camoufox 未安装，跳过")
        return None

    try:
        from curl_cffi import requests as curl_requests
    except Exception as e:
        _log(f"Sentinel Camoufox: curl_cffi 不可用，跳过底层 challenge: {e}")
        return None

    did = str(device_id or uuid.uuid4())
    effective_ua = str(user_agent or "").strip()
    if not effective_ua:
        effective_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
            "Gecko/20100101 Firefox/147.0"
        )
    effective_impersonate = normalize_impersonate(
        impersonate_from_user_agent(effective_ua, default=impersonate or "firefox147"),
        "firefox147",
    )

    camoufox_os = _camoufox_os_from_ua(effective_ua)
    cf_proxy = _parse_camoufox_proxy(proxy)
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    target_url = _flow_page_url(flow)
    challenge_session = curl_requests.Session(impersonate=effective_impersonate)
    if proxy:
        challenge_session.proxies = build_requests_proxy_config(proxy)
    sdk_source = ""
    try:
        sdk_file = _ensure_sdk_file(
            challenge_session,
            timeout_ms,
            logger=_log,
            user_agent=effective_ua,
            impersonate=effective_impersonate,
        )
        sdk_source = sdk_file.read_text(encoding="utf-8")
    except Exception as ex:
        _log(f"Sentinel Camoufox: sdk.js 准备失败: {ex}")
        return None

    _log(
        f"Sentinel Camoufox: flow={flow} os={camoufox_os} "
        f"viewport={viewport_width}x{viewport_height} ua_tail={effective_ua[-30:]}"
    )

    try:
        camoufox_kwargs: dict[str, Any] = {
            "headless": headless or not has_display,
            "humanize": True,
            "os": camoufox_os,
            "locale": "en-US",
            "geoip": True,
        }
        camoufox_kwargs.update(_camoufox_screen_from_viewport(viewport_width, viewport_height))
        if cf_proxy:
            camoufox_kwargs["proxy"] = cf_proxy

        with _isolate_event_loop(_log), Camoufox(**camoufox_kwargs) as browser:
            if not hasattr(browser, "new_context"):
                _log("Sentinel Camoufox: 无法创建同步 UA 的 context，直接 QuickJS fallback")
                return None
            if hasattr(browser, "new_context"):
                context = browser.new_context(
                    viewport={"width": viewport_width, "height": viewport_height},
                    user_agent=effective_ua,
                    locale="en-US",
                    ignore_https_errors=True,
                )
                page = context.new_page()
                _log("Sentinel Camoufox: context UA 已同步协议层")
            else:
                context = browser
                try:
                    context.set_extra_http_headers({"User-Agent": effective_ua})
                except Exception as ex:
                    _log(f"Sentinel Camoufox: HTTP UA 同步异常: {ex}")
                try:
                    ua_json = json.dumps(effective_ua)
                    context.add_init_script(
                        "(() => {"
                        f"const ua = {ua_json};"
                        "Object.defineProperty(Navigator.prototype, 'userAgent', {get: () => ua, configurable: true});"
                        "Object.defineProperty(Navigator.prototype, 'appVersion', {get: () => ua.replace(/^Mozilla\\//, ''), configurable: true});"
                        "})();"
                    )
                except Exception as ex:
                    _log(f"Sentinel Camoufox: JS UA 同步异常: {ex}")
                page = context.new_page()
                _log("Sentinel Camoufox: fallback context 已同步协议层 UA")

            # 注入协议层 cookie：oai-did + auth session cookies
            cookies_to_inject = [
                {
                    "name": "oai-did",
                    "value": did,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "oai-did",
                    "value": did,
                    "domain": ".openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                },
                {
                    "name": "oai-did",
                    "value": did,
                    "domain": "sentinel.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                },
            ]
            # 从协议层 session 注入所有 auth 相关 cookies
            if protocol_session:
                session_cookies = _extract_cookies_from_session(protocol_session)
                cookies_to_inject.extend(session_cookies)
                _log(f"Sentinel Camoufox: 注入 {len(session_cookies)} 个协议层 cookies")

            try:
                context.add_cookies(cookies_to_inject)
            except Exception as ex:
                _log(f"Sentinel Camoufox: 注入 cookies 异常: {ex}")

            # 先导航到目标页面，等 SentinelSDK 加载
            _log(f"Sentinel Camoufox: 导航到 {target_url[:80]}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)

            result = None
            sentinel_frame = _wait_for_sentinel_frame(page, min(timeout_ms, 8000))
            sentinel_url = str(getattr(sentinel_frame, "url", "") or "") if sentinel_frame is not None else ""
            if sentinel_url:
                sentinel_page = None
                try:
                    sentinel_page = context.new_page()
                    _log(f"Sentinel Camoufox: 顶层打开 sentinel frame {sentinel_url[:100]}")
                    sentinel_page.goto(sentinel_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    _install_patched_sentinel_sdk(sentinel_page, sdk_source)
                    request_result = _get_requirements_token_from_patched_sdk(sentinel_page)
                    request_p = str((request_result or {}).get("request_p") or "").strip()
                    if not request_result or not request_result.get("success") or not request_p:
                        raise RuntimeError("requirements: " + str((request_result or {}).get("error") or "requirements failed"))
                    challenge = _fetch_sentinel_challenge(
                        challenge_session,
                        device_id=did,
                        flow=flow,
                        request_p=request_p,
                        timeout_ms=timeout_ms,
                        logger=_log,
                        user_agent=effective_ua,
                        impersonate=effective_impersonate,
                    )
                    result = _run_patched_sentinel_flow(
                        sentinel_page,
                        flow=flow,
                        request_p=request_p,
                        challenge=challenge,
                    )
                    if result and not result.get("success"):
                        raise RuntimeError("final: " + str(result.get("error") or "final failed"))
                    if result and result.get("success") and result.get("token"):
                        _log(f"Sentinel Camoufox: patched SDK 命中顶层 sentinel page {sentinel_url[:120]}")
                except Exception as ex:
                    _log(f"Sentinel Camoufox: 顶层 sentinel SDK 流程失败: {ex}")
                finally:
                    if sentinel_page is not None:
                        try:
                            sentinel_page.close()
                        except Exception:
                            pass

            if not result or not result.get("success"):
                result = _wait_and_evaluate_sentinel_token_in_frames(
                    page,
                    flow,
                    _log,
                    "Sentinel Camoufox",
                    min(timeout_ms, 7000),
                )
            if not result or not result.get("success"):
                frame_urls = (result or {}).get("frames") or _frame_urls(page)
                _log(f"Sentinel Camoufox: SentinelSDK 未加载，frames={frame_urls}，直接 QuickJS fallback")
                return None

            encoded = _encode_sentinel_result(result, did=did, flow=flow, logger=_log, prefix="Sentinel Camoufox")
            if not encoded:
                return None

            # 从浏览器提取 Turnstile/CF cookies 回写到协议层 session
            if protocol_session:
                _sync_browser_cookies_to_session(page, protocol_session, _log)

            return encoded

    except Exception as e:
        _log(f"Sentinel Camoufox 异常: {e}")
        return None


# ---------------------------------------------------------------------------
# Patchright (Chromium 反检测) — Chrome 指纹链路
# ---------------------------------------------------------------------------


def _parse_playwright_proxy(proxy: str | None) -> dict | None:
    """将代理 URL 转为 Playwright/Patchright proxy 格式。支持认证代理。"""
    if not proxy:
        return None
    return build_playwright_proxy_config(proxy)


def _sync_browser_cookies_to_session(page: Any, session: Any, logger: Callable) -> None:
    """从浏览器 page 提取关键 cookies 回写到协议层 curl_cffi session。"""
    important_cookies = {
        "cf_clearance", "__cf_bm", "__cflb", "_cfuvid",
        "oai-sc", "oai-did",
    }
    try:
        browser_cookies = page.context.cookies()
        synced = 0
        for bc in browser_cookies:
            name = str(bc.get("name", ""))
            if name in important_cookies:
                domain = str(bc.get("domain", ""))
                value = str(bc.get("value", ""))
                try:
                    session.cookies.set(name, value, domain=domain)
                    synced += 1
                except Exception:
                    pass
        if synced:
            logger(f"Sentinel: 回写 {synced} 个 cookies 到协议层")
    except Exception as ex:
        logger(f"Sentinel: cookie 回写异常: {ex}")


def _frame_urls(page: Any, limit: int = 12) -> list[str]:
    urls: list[str] = []
    for frame in list(getattr(page, "frames", []) or [])[:limit]:
        try:
            urls.append(str(getattr(frame, "url", "") or "")[:140])
        except Exception:
            pass
    return urls


def _sentinel_frames(page: Any) -> list[Any]:
    result: list[Any] = []
    for frame in list(getattr(page, "frames", []) or []):
        try:
            url = str(getattr(frame, "url", "") or "")
        except Exception:
            url = ""
        if "sentinel.openai.com" in url:
            result.append(frame)
    return result


def _wait_for_sentinel_frame(page: Any, timeout_ms: int) -> Any | None:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    best = None
    while time.monotonic() < deadline:
        frames = _sentinel_frames(page)
        for frame in frames:
            url = str(getattr(frame, "url", "") or "")
            if "/backend-api/sentinel/frame.html" in url:
                return frame
            best = best or frame
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    return best


def _evaluate_sentinel_token_in_frames(page: Any, flow: str, logger: Callable, prefix: str) -> dict:
    frames = list(getattr(page, "frames", []) or [])
    last_error = "SentinelSDK.token unavailable in frames"
    for index, frame in enumerate(frames):
        try:
            result = frame.evaluate(
                """
                async ({ flow }) => {
                    try {
                        if (!globalThis.SentinelSDK || typeof globalThis.SentinelSDK.token !== 'function') {
                            return { success: false, error: 'SentinelSDK.token unavailable' };
                        }
                        const value = await globalThis.SentinelSDK.token(flow);
                        const token = value && typeof value === 'object' && 'token' in value
                            ? value.token
                            : value;
                        const soToken = value && typeof value === 'object'
                            ? (value.so_token || value.soToken || value.so || null)
                            : null;
                        return { success: true, token, so_token: soToken };
                    } catch (e) {
                        return { success: false, error: (e && (e.message || String(e))) || 'unknown' };
                    }
                }
                """,
                {"flow": flow},
            )
            if result and result.get("success") and result.get("token"):
                logger(f"{prefix}: SentinelSDK 命中 frame[{index}] {str(getattr(frame, 'url', '') or '')[:120]}")
                return result
            if result and result.get("error"):
                last_error = str(result.get("error") or last_error)
        except Exception as ex:
            last_error = str(ex)
    return {"success": False, "error": last_error, "frames": _frame_urls(page)}


def _wait_and_evaluate_sentinel_token_in_frames(
    page: Any,
    flow: str,
    logger: Callable,
    prefix: str,
    timeout_ms: int,
) -> dict:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    last_result: dict = {"success": False, "error": "SentinelSDK.token unavailable in frames", "frames": []}
    while time.monotonic() < deadline:
        last_result = _evaluate_sentinel_token_in_frames(page, flow, logger, prefix)
        if last_result.get("success") and last_result.get("token"):
            return last_result
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    last_result["frames"] = _frame_urls(page)
    return last_result


def _ensure_sentinel_sdk_on_page(page: Any, timeout_ms: int, logger: Callable, prefix: str) -> bool:
    try:
        page.wait_for_function(
            "() => typeof window.SentinelSDK !== 'undefined' "
            "&& typeof window.SentinelSDK.token === 'function'",
            timeout=min(timeout_ms, 15000),
        )
        return True
    except Exception:
        logger(f"{prefix}: SentinelSDK 未随页面加载，尝试注入 sdk.js")

    try:
        page.add_script_tag(url=SENTINEL_SDK_URL)
        page.wait_for_function(
            "() => typeof window.SentinelSDK !== 'undefined' "
            "&& typeof window.SentinelSDK.token === 'function'",
            timeout=min(timeout_ms, 15000),
        )
        logger(f"{prefix}: SentinelSDK 注入成功")
        return True
    except Exception as ex:
        logger(f"{prefix}: SentinelSDK 注入失败: {ex}")
        return False


def _patched_sentinel_sdk_source(sdk_source: str) -> str:
    patches = [
        ("var SentinelSDK=", "globalThis.SentinelSDK="),
        ("var P=new _;", "var P=new _;globalThis.__debugP=P;"),
        (
            "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t}({});",
            "return o?(globalThis.__debug_so=o, r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o):null},t.token=ye,t.__debug_n=_n,t.__debug_bindProof=D,t}({});",
        ),
        (
            "const Xn=5e3,te=Hn(36),ne=new Map,ee=new Map;function re(t)",
            "const Xn=5e3,te=Hn(36),ne=new Map,ee=new Map;globalThis.__debug_ne=ne;function re(t)",
        ),
    ]
    sdk = str(sdk_source or "")
    for old, new in patches:
        sdk = sdk.replace(old, new)
    return sdk


def _install_patched_sentinel_sdk(target: Any, sdk_source: str) -> None:
    target.add_script_tag(content=_patched_sentinel_sdk_source(sdk_source))


def _install_patched_sentinel_sdk_on_page(page: Any, sdk_source: str) -> None:
    _install_patched_sentinel_sdk(page, sdk_source)


def _page_result_dom_id(name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(name or "result"))[:80]
    return f"sentinel_result_{safe or 'result'}"


def _read_page_json_result(target: Any, name: str, timeout_ms: int = 12000) -> dict:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    selector = f"#{_page_result_dom_id(name)}"
    value = ""
    last_error = ""
    while time.monotonic() < deadline:
        try:
            value = str(target.locator(selector).text_content(timeout=250) or "")
        except Exception as ex:
            last_error = str(ex)
        if value:
            break
        try:
            target.wait_for_timeout(100)
        except Exception:
            time.sleep(0.1)
    if not value:
        suffix = f": {last_error}" if last_error else ""
        return {"success": False, "error": f"{name} timeout{suffix}"}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {"success": False, "error": f"{name} non-object"}
    except Exception as ex:
        return {"success": False, "error": f"{name} invalid json: {ex}"}


def _run_page_script_json(target: Any, result_name: str, script: str, payload: dict[str, Any] | None = None) -> dict:
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False)
    result_id_json = json.dumps(_page_result_dom_id(result_name))
    source = (
        "(() => {"
        f"const __payload = {payload_json};"
        f"const __resultNodeId = {result_id_json};"
        "const __writeResult = (value) => {"
        "let node = document.getElementById(__resultNodeId);"
        "if (!node) {"
        "node = document.createElement('pre');"
        "node.id = __resultNodeId;"
        "node.setAttribute('data-sentinel-result', '1');"
        "node.style.cssText = 'display:none';"
        "(document.body || document.documentElement).appendChild(node);"
        "}"
        "node.textContent = value;"
        "};"
        "__writeResult('');"
        "Promise.resolve().then(async () => {"
        "try {"
        f"const __result = await (async (__payload) => {{ {script} }})(__payload);"
        "__writeResult(JSON.stringify(__result || {success:false,error:'empty result'}));"
        "} catch (e) {"
        "__writeResult(JSON.stringify({success:false,error:(e && (e.message || String(e))) || 'unknown'}));"
        "}"
        "});"
        "})();"
    )
    target.add_script_tag(content=source)
    return _read_page_json_result(target, result_name)


def _run_patched_sentinel_flow(
    target: Any,
    *,
    flow: str,
    request_p: str,
    challenge: dict[str, Any],
) -> dict:
    return _run_page_script_json(
        target,
        "__sentinel_final_result_json",
        """
        const { flow, requestP, challenge } = __payload;
        const finalP = await globalThis.__debugP.getEnforcementToken(challenge);
        globalThis.SentinelSDK.__debug_bindProof(challenge, requestP);
        const dx = challenge && challenge.turnstile ? challenge.turnstile.dx : null;
        const t = dx ? await globalThis.SentinelSDK.__debug_n(challenge, dx) : null;
        let so = globalThis.__debug_so || null;
        if (!so && globalThis.__debug_ne) {
            try {
                let ctx = globalThis.__debug_ne.get(flow);
                if (!ctx) {
                    ctx = {};
                    globalThis.__debug_ne.set(flow, ctx);
                }
                ctx.cachedSOChatReq = challenge;
                ctx.sessionObserverCollectorActive = false;
                if (challenge && challenge.so && challenge.so.required && typeof challenge.so.collector_dx === 'string') {
                    const soToken = await globalThis.SentinelSDK.sessionObserverToken(flow);
                    if (soToken) {
                        const obj = typeof soToken === 'string' ? JSON.parse(soToken) : soToken;
                        so = obj && obj.so ? obj.so : null;
                    }
                }
            } catch (_) {}
        }
        return { success: true, token: { p: finalP, t, c: challenge.token, id: challenge.id || null, flow }, so };
        """,
        {"flow": flow, "requestP": request_p, "challenge": challenge},
    )


def _get_requirements_token_from_patched_sdk(target: Any) -> dict:
    return _run_page_script_json(
        target,
        "__sentinel_requirements_result_json",
        """
        if (!globalThis.__debugP || typeof globalThis.__debugP.getRequirementsToken !== 'function') {
            return { success: false, error: 'debug requirements unavailable' };
        }
        const request_p = await globalThis.__debugP.getRequirementsToken();
        return { success: true, request_p };
        """,
    )


def _encode_sentinel_result(result: dict, *, did: str, flow: str, logger: Callable, prefix: str) -> dict | None:
    if not result or not result.get("success") or not result.get("token"):
        error_msg = str((result or {}).get("error") or "no result")
        logger(f"{prefix}: 获取失败: {error_msg}")
        return None

    raw_token_value = result.get("token")
    if isinstance(raw_token_value, dict):
        raw_token = json.dumps(
            {
                "p": raw_token_value.get("p"),
                "t": raw_token_value.get("t"),
                "c": raw_token_value.get("c"),
                "id": raw_token_value.get("id") or did,
                "flow": raw_token_value.get("flow") or flow,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    else:
        raw_token = str(raw_token_value or "").strip()
    if not raw_token:
        logger(f"{prefix}: 返回空 token")
        return None

    try:
        parsed = json.loads(raw_token)
        so_value = str(result.get("so") or "").strip()
        if not so_value and isinstance(parsed, dict):
            so_value = str(parsed.get("so") or "").strip()
        so_token = None
        if so_value:
            so_token = json.dumps(
                {
                    "so": so_value,
                    "c": parsed.get("c"),
                    "id": parsed.get("id") or did,
                    "flow": parsed.get("flow") or flow,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            )
        if not so_token:
            so_token = result.get("so_token") or None
        logger(
            f"{prefix}: 成功 "
            f"p={'OK' if parsed.get('p') else 'X'} "
            f"t={'OK' if parsed.get('t') else 'X'} "
            f"c={'OK' if parsed.get('c') else 'X'} "
            f"so={'OK(' + str(len(so_value)) + ')' if so_token else 'X'}"
        )
    except Exception:
        so_token = result.get("so_token") or None
        logger(f"{prefix}: 成功 len={len(raw_token)}")

    return {"token": raw_token, "so_token": so_token}


def get_sentinel_token_via_patchright(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    device_id: Optional[str] = None,
    impersonate: str = "chrome146",
    user_agent: Optional[str] = None,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    protocol_session: Optional[Any] = None,
    headless: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """Patchright Chromium 反检测浏览器获取 Sentinel token。

    Chrome 指纹链路：协议层 curl_cffi chrome impersonate + Patchright Chromium。
    TLS/UA/window.chrome 全部一致，无矛盾。
    """
    _log = logger or (lambda _msg: None)

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        _log("Sentinel Patchright: patchright 未安装，跳过")
        return None

    try:
        from curl_cffi import requests as curl_requests
    except Exception as e:
        _log(f"Sentinel Patchright: curl_cffi 不可用，跳过底层 challenge: {e}")
        return None

    did = str(device_id or uuid.uuid4())
    effective_impersonate = normalize_impersonate(impersonate, "chrome146")
    effective_ua = str(user_agent or "").strip()
    if not effective_ua:
        from .fingerprint import _random_chrome_fingerprint
        fp = _random_chrome_fingerprint()
        effective_ua = fp.user_agent
        effective_impersonate = fp.impersonate

    target_url = _flow_page_url(flow)
    effective_headless, reason = resolve_browser_headless(headless)
    ensure_browser_display_available(effective_headless)

    challenge_session = curl_requests.Session(impersonate=effective_impersonate)
    if proxy:
        challenge_session.proxies = build_requests_proxy_config(proxy)
    sdk_source = ""
    try:
        sdk_file = _ensure_sdk_file(
            challenge_session,
            timeout_ms,
            logger=_log,
            user_agent=effective_ua,
            impersonate=effective_impersonate,
        )
        sdk_source = sdk_file.read_text(encoding="utf-8")
    except Exception as ex:
        _log(f"Sentinel Patchright: sdk.js 准备失败: {ex}")
        return None

    proxy_config = _parse_playwright_proxy(proxy)
    launch_args: dict[str, Any] = {
        "headless": effective_headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    if proxy_config:
        launch_args["proxy"] = proxy_config

    _log(
        f"Sentinel Patchright: flow={flow} "
        f"viewport={viewport_width}x{viewport_height} "
        f"ua_tail={effective_ua[-30:]}"
    )

    with _isolate_event_loop(_log), sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                user_agent=effective_ua,
                ignore_https_errors=True,
            )

            # 注入 cookies
            cookies_to_inject = []
            if did:
                cookies_to_inject.append({
                    "name": "oai-did",
                    "value": did,
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                })
                cookies_to_inject.append({
                    "name": "oai-did",
                    "value": did,
                    "domain": ".openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                })
                cookies_to_inject.append({
                    "name": "oai-did",
                    "value": did,
                    "domain": "sentinel.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                })
            if protocol_session:
                session_cookies = _extract_cookies_from_session(protocol_session)
                cookies_to_inject.extend(session_cookies)
                _log(f"Sentinel Patchright: 注入 {len(session_cookies)} 个协议层 cookies")
            if cookies_to_inject:
                try:
                    context.add_cookies(cookies_to_inject)
                except Exception as ex:
                    _log(f"Sentinel Patchright: 注入 cookies 异常: {ex}")

            page = context.new_page()
            _log(f"Sentinel Patchright: 导航到 {target_url[:80]}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)

            try:
                _install_patched_sentinel_sdk_on_page(page, sdk_source)
                request_result = _get_requirements_token_from_patched_sdk(page)
                request_p = str((request_result or {}).get("request_p") or "").strip()
                if not request_result or not request_result.get("success") or not request_p:
                    raise RuntimeError("requirements: " + str((request_result or {}).get("error") or "requirements failed"))

                challenge = _fetch_sentinel_challenge(
                    challenge_session,
                    device_id=did,
                    flow=flow,
                    request_p=request_p,
                    timeout_ms=timeout_ms,
                    logger=_log,
                    user_agent=effective_ua,
                    impersonate=effective_impersonate,
                )
                result = _run_patched_sentinel_flow(
                    page,
                    flow=flow,
                    request_p=request_p,
                    challenge=challenge,
                )
                if result and not result.get("success"):
                    raise RuntimeError("final: " + str(result.get("error") or "final failed"))
            except Exception as ex:
                _log(f"Sentinel Patchright: 底层 SDK 流程失败，尝试高层 token(): {ex}")
                if not _ensure_sentinel_sdk_on_page(page, timeout_ms, _log, "Sentinel Patchright"):
                    return None
                result = page.evaluate(
                    """
                    async ({ flow }) => {
                        try {
                            if (!window.SentinelSDK || typeof window.SentinelSDK.token !== 'function') {
                                return { success: false, error: 'SentinelSDK.token unavailable' };
                            }
                            const value = await window.SentinelSDK.token(flow);
                            const token = value && typeof value === 'object' && 'token' in value
                                ? value.token
                                : value;
                            const soToken = value && typeof value === 'object'
                                ? (value.so_token || value.soToken || null)
                                : null;
                            return { success: true, token, so_token: soToken };
                        } catch (e) {
                            return {
                                success: false,
                                error: (e && (e.message || String(e))) || "unknown",
                            };
                        }
                    }
                    """,
                    {"flow": flow},
                )

            encoded = _encode_sentinel_result(result, did=did, flow=flow, logger=_log, prefix="Sentinel Patchright")
            if not encoded:
                return None

            # 回写 cookies
            if protocol_session:
                _sync_browser_cookies_to_session(page, protocol_session, _log)

            return encoded

        except Exception as e:
            _log(f"Sentinel Patchright 异常: {e}")
            return None
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# QuickJS 方案 — 降级为 fallback
# ---------------------------------------------------------------------------


def get_sentinel_token_via_quickjs(
    *,
    flow: str,
    proxy: Optional[str],
    timeout_ms: int = 45000,
    device_id: Optional[str] = None,
    impersonate: str = "chrome146",
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
    viewport_width: int = 1366,
    viewport_height: int = 768,
    logger: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """通过 QuickJS/Node 执行真实 Sentinel SDK（fallback 方案）。

    注意：QuickJS 产出的 so 字段基于假环境，可能被服务端识别。
    优先使用 Camoufox 混合模式。
    """
    _log = logger or (lambda _msg: None)
    try:
        from curl_cffi import requests as curl_requests
    except Exception as e:
        _log(f"Sentinel QuickJS 不可用: curl_cffi 导入失败: {e}")
        return None

    quickjs_script = _quickjs_script_path()
    if not quickjs_script.exists():
        _log(f"Sentinel QuickJS 脚本不存在: {quickjs_script}")
        return None

    did = str(device_id or uuid.uuid4())
    effective_impersonate = normalize_impersonate(impersonate, "chrome146")
    session = curl_requests.Session(impersonate=effective_impersonate)
    if proxy:
        session.proxies = build_requests_proxy_config(proxy)

    debug_dir = None
    try:
        _log(
            "Sentinel QuickJS 指纹 (fallback): "
            f"ua={user_agent or '-'} impersonate={effective_impersonate}"
        )
        sdk_file = _ensure_sdk_file(
            session,
            timeout_ms,
            logger=_log,
            user_agent=user_agent,
            impersonate=effective_impersonate,
            accept_language=accept_language,
        )
        if _quickjs_debug_enabled():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            debug_dir = (
                _quickjs_debug_root()
                / f"{timestamp}_{_safe_debug_name(flow)}_{_safe_debug_name(did[:8])}"
            )
            debug_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sdk_file, debug_dir / "sdk.raw.js")
            shutil.copy2(quickjs_script, debug_dir / "openai_sentinel_quickjs.runner.js")
            _write_debug_json(
                debug_dir / "meta.json",
                {
                    "flow": flow,
                    "device_id": did,
                    "impersonate": effective_impersonate,
                    "user_agent": user_agent or "",
                    "sentinel_version": SENTINEL_VERSION,
                    "sdk_url": SENTINEL_SDK_URL,
                    "sdk_cache_file": str(sdk_file),
                },
            )
            _log(f"Sentinel QuickJS debug dump: {debug_dir}")
        requirements_payload = _quickjs_payload_base(
            device_id=did,
            flow=flow,
            user_agent=user_agent,
            accept_language=accept_language,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
        if debug_dir:
            _write_debug_json(debug_dir / "requirements.payload.json", requirements_payload)
        requirements = _run_quickjs_action_with_node(
            action="requirements",
            sdk_file=sdk_file,
            quickjs_script=quickjs_script,
            payload=requirements_payload,
            timeout_ms=timeout_ms,
            debug_dir=debug_dir,
        )
        if debug_dir:
            _write_debug_json(debug_dir / "requirements.result.json", requirements)
        request_p = str(requirements.get("request_p") or "").strip()
        if not request_p:
            _log("Sentinel QuickJS 失败: requirements 未返回 request_p")
            return None

        challenge = _fetch_sentinel_challenge(
            session,
            device_id=did,
            flow=flow,
            request_p=request_p,
            timeout_ms=timeout_ms,
            logger=_log,
            user_agent=user_agent,
            impersonate=effective_impersonate,
            accept_language=accept_language,
        )
        if debug_dir:
            _write_debug_json(debug_dir / "challenge.response.json", challenge)
        c_value = str(challenge.get("token") or "").strip()
        if not c_value:
            _log("Sentinel QuickJS 失败: challenge token 为空")
            return None

        solve_payload = dict(requirements_payload)
        solve_payload.update(
            {
                "request_p": request_p,
                "challenge": challenge,
                "flow": flow,
            }
        )
        if debug_dir:
            _write_debug_json(debug_dir / "solve.payload.json", solve_payload)
        solved = _run_quickjs_action_with_node(
            action="solve",
            sdk_file=sdk_file,
            quickjs_script=quickjs_script,
            payload=solve_payload,
            timeout_ms=timeout_ms,
            debug_dir=debug_dir,
        )
        if debug_dir:
            _write_debug_json(debug_dir / "solve.result.json", solved)
        final_p = str(solved.get("final_p") or solved.get("p") or "").strip()
        if not final_p:
            _log("Sentinel QuickJS 失败: solve 未返回 final_p")
            return None

        t_raw = solved.get("t")
        t_value = "" if t_raw is None else str(t_raw).strip()
        if not t_value:
            _log("Sentinel QuickJS 失败: solve 未返回有效 t")
            return None

        token = json.dumps(
            {
                "p": final_p,
                "t": t_value,
                "c": c_value,
                "id": did,
                "flow": flow,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )

        so_value = solved.get("so")
        so_value = str(so_value).strip() if so_value else ""
        so_token = None
        if so_value:
            so_token = json.dumps(
                {"so": so_value, "c": c_value, "id": did, "flow": flow},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            _log(f"Sentinel QuickJS 成功 (fallback): p=OK t=OK c=OK so=OK({len(so_value)})")
        else:
            _log("Sentinel QuickJS 成功 (fallback): p=OK t=OK c=OK so=X")

        return {"token": token, "so_token": so_token}
    except Exception as e:
        if debug_dir:
            try:
                (debug_dir / "error.txt").write_text(str(e), encoding="utf-8")
            except Exception:
                pass
        _log(f"Sentinel QuickJS 异常 (fallback): {e}")
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Playwright 方案 — Camoufox 不可用时的第二选择
# ---------------------------------------------------------------------------


def get_sentinel_token_via_browser(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    impersonate: str = "chrome146",
    user_agent: Optional[str] = None,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    protocol_session: Optional[Any] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """通过浏览器直接调用 SentinelSDK.token(flow) 获取完整 token。

    SOCKS5 认证代理下自动降级到 QuickJS。
    """
    result = get_sentinel_token_bundle_via_browser(
        flow=flow,
        proxy=proxy,
        timeout_ms=timeout_ms,
        page_url=page_url,
        headless=headless,
        device_id=device_id,
        impersonate=impersonate,
        user_agent=user_agent,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        protocol_session=protocol_session,
        log_fn=log_fn,
    )
    if result and result.get("token"):
        return SentinelToken(str(result["token"]), result.get("so_token") or None)
    return None


def get_sentinel_token_bundle_via_browser(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    impersonate: str = "chrome146",
    user_agent: Optional[str] = None,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    protocol_session: Optional[Any] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """获取 Sentinel token bundle。

    双链路架构：
      Chrome 指纹 → Patchright (Chromium) — TLS/UA/window.chrome 全一致
      Firefox 指纹 → Camoufox (Firefox) — TLS/UA/SpiderMonkey 全一致

    降级：Patchright/Camoufox → QuickJS → 纯 Python PoW
    """
    logger = log_fn or (lambda _msg: None)
    did = str(device_id or uuid.uuid4())
    effective_ua = str(user_agent or "").strip()
    is_firefox_ua = "Firefox" in effective_ua
    is_auth_proxy = is_authenticated_socks5_proxy(proxy)

    # ── Firefox 链路：Camoufox ──
    if is_firefox_ua and not is_auth_proxy:
        camoufox_result = get_sentinel_token_via_camoufox(
            flow=flow,
            proxy=proxy,
            timeout_ms=timeout_ms,
            device_id=did,
            impersonate=impersonate,
            user_agent=effective_ua or None,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            protocol_session=protocol_session,
            headless=headless,
            logger=logger,
        )
        if camoufox_result and camoufox_result.get("token"):
            return camoufox_result
        logger("Sentinel Camoufox 失败，尝试 QuickJS fallback")
    elif not is_auth_proxy:
        logger("Sentinel Patchright 已禁用，直接尝试 QuickJS fallback")
        # patchright_result = get_sentinel_token_via_patchright(
        #     flow=flow,
        #     proxy=proxy,
        #     timeout_ms=timeout_ms,
        #     device_id=did,
        #     impersonate=impersonate,
        #     user_agent=effective_ua or None,
        #     viewport_width=viewport_width,
        #     viewport_height=viewport_height,
        #     protocol_session=protocol_session,
        #     headless=headless,
        #     logger=logger,
        # )
        # if patchright_result and patchright_result.get("token"):
        #     return patchright_result
        # logger("Sentinel Patchright 失败，尝试 QuickJS fallback")
    else:
        logger("Sentinel 检测到带认证 SOCKS5 代理: 跳过浏览器模式")

    qj_result = get_sentinel_token_via_quickjs(
        flow=flow,
        proxy=proxy,
        timeout_ms=timeout_ms,
        device_id=did,
        impersonate=impersonate,
        user_agent=effective_ua or None,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        logger=logger,
    )
    if qj_result and qj_result.get("token"):
        return qj_result

    return None


def _get_sentinel_token_via_playwright(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    protocol_session: Optional[Any] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """Playwright 版 Sentinel token 获取（Camoufox 不可用时的降级方案）。"""
    _log = logger or (lambda _msg: None)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        _log(f"Sentinel Playwright 不可用: {e}")
        return None

    target_url = str(page_url or _flow_page_url(flow)).strip() or _flow_page_url(flow)
    effective_headless, reason = resolve_browser_headless(headless)
    ensure_browser_display_available(effective_headless)
    _log(
        f"Sentinel Playwright 模式: {'headless' if effective_headless else 'headed'} ({reason})"
    )

    launch_args: dict[str, Any] = {
        "headless": effective_headless,
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    effective_ua = str(user_agent or "").strip() or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )

    _log(
        f"Sentinel Playwright: flow={flow} url={target_url[:80]} "
        f"ua_tail={effective_ua[-30:]}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                user_agent=effective_ua,
                ignore_https_errors=True,
            )

            # 注入 cookies
            cookies_to_inject = []
            if device_id:
                cookies_to_inject.append({
                    "name": "oai-did",
                    "value": str(device_id),
                    "domain": "auth.openai.com",
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax",
                })
            if protocol_session:
                session_cookies = _extract_cookies_from_session(protocol_session)
                cookies_to_inject.extend(session_cookies)
            if cookies_to_inject:
                try:
                    context.add_cookies(cookies_to_inject)
                except Exception as ex:
                    _log(f"Sentinel Playwright: 注入 cookies 异常: {ex}")

            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function(
                "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                timeout=min(timeout_ms, 15000),
            )

            result = page.evaluate(
                """
                async ({ flow }) => {
                    try {
                        const token = await window.SentinelSDK.token(flow);
                        return { success: true, token };
                    } catch (e) {
                        return {
                            success: false,
                            error: (e && (e.message || String(e))) || "unknown",
                        };
                    }
                }
                """,
                {"flow": flow},
            )

            if not result or not result.get("success") or not result.get("token"):
                _log(
                    "Sentinel Playwright 获取失败: "
                    + str((result or {}).get("error") or "no result")
                )
                return None

            token = str(result["token"] or "").strip()
            if not token:
                _log("Sentinel Playwright 返回空 token")
                return None

            try:
                parsed = json.loads(token)
                so_value = str(parsed.get("so") or "").strip() if isinstance(parsed, dict) else ""
                so_token = None
                if so_value:
                    so_token = json.dumps(
                        {
                            "so": so_value,
                            "c": parsed.get("c"),
                            "id": parsed.get("id") or device_id,
                            "flow": parsed.get("flow") or flow,
                        },
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                _log(
                    "Sentinel Playwright 成功: "
                    f"p={'OK' if parsed.get('p') else 'X'} "
                    f"t={'OK' if parsed.get('t') else 'X'} "
                    f"c={'OK' if parsed.get('c') else 'X'} "
                    f"so={'OK' if so_token else 'X'}"
                )
            except Exception:
                so_token = None
                _log(f"Sentinel Playwright 成功: len={len(token)}")

            # 回写 cookies
            if protocol_session:
                _sync_browser_cookies_to_session(page, protocol_session, _log)

            return {"token": token, "so_token": so_token}
        except Exception as e:
            _log(f"Sentinel Playwright 异常: {e}")
            return None
        finally:
            browser.close()
