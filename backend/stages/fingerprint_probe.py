"""Batch preauth fingerprint probe.

This stage does not register an account.  It repeatedly creates a fresh
proxy+browser fingerprint and runs only the ChatGPT entry/signin/authorize
preauth path.  Fingerprints that reach a real auth page without landing on the
Cloudflare /api/accounts/authorize interstitial are persisted into
`chatgpt_fingerprint_good_pool` by ChatGPTClient.record_fingerprint_outcome().
"""
from __future__ import annotations

import random
import string
import time
from typing import Any
from urllib.parse import urlparse

from backend.core.job_context import JobContext
from backend.core.proxy import resolve_workpool_proxy_template
from backend.core.settings import settings
from backend.core.stages import stage


@stage(
    name="fingerprint_probe",
    optional_resources=["proxy_pool"],
    default_concurrency=1,
    description="Batch probe ChatGPT preauth with generated UA/fingerprint and fresh proxy per attempt.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    all_settings = {**settings.get_all(), **dict(payload.get("extra_config") or {})}
    attempts = _int(payload.get("attempts") or all_settings.get("workpool.fingerprint_probe.attempts"), 20, 1, 500)
    delay_ms = _int(payload.get("delay_ms") or all_settings.get("workpool.fingerprint_probe.delay_ms"), 800, 0, 60000)
    country_code = str(payload.get("country_code") or all_settings.get("workpool.fingerprint_probe.country_code") or "US").strip() or "US"
    stop_on_success = _bool(payload.get("stop_on_success", all_settings.get("workpool.fingerprint_probe.stop_on_success", "0")))
    browsers = _parse_browsers(payload.get("browsers") or all_settings.get("workpool.fingerprint_probe.browsers") or "chrome,firefox")
    email_hint = str(payload.get("email") or payload.get("email_hint") or "").strip()

    ctx.log(
        "starting fingerprint_probe",
        payload={
            "attempts": attempts,
            "browsers": browsers,
            "country_code": country_code,
            "delay_ms": delay_ms,
            "stop_on_success": stop_on_success,
        },
    )

    from backend.integrations.chatgpt.chatgpt_client import ChatGPTClient
    from backend.integrations.chatgpt.chatgpt_entry_bootstrap import bootstrap_chatgpt_entry
    from backend.integrations.chatgpt.fingerprint import random_browserforge_fingerprint

    results: list[dict[str, Any]] = []
    ok = 0
    cf = 0
    errors = 0

    for i in range(1, attempts + 1):
        ctx.check_cancelled()
        proxy_url = _render_proxy(payload, all_settings, attempt=i)
        if not proxy_url:
            ctx.log(f"fingerprint_probe[{i}/{attempts}] 无可用代理", level="error")
            errors += 1
            continue

        browser_mode = random.choice(browsers)
        fp = random_browserforge_fingerprint(browser_mode)
        probe_email = email_hint or _random_email()
        client = ChatGPTClient(proxy=proxy_url, browser_mode="protocol")
        client.reset_fingerprint(fp)
        client.verbose = True
        client.log_callback = lambda msg, level="info", _ctx=ctx: _ctx.log(str(msg), level=level)
        # Probe the first protocol preauth decision only.  Do not use Camoufox
        # or captcha solving here; this job is meant to discover UA/fingerprints
        # that do not trigger CF in the first place.
        client.authorize_via_browser = lambda url, **kwargs: ""  # type: ignore[method-assign]

        ctx.log(
            f"fingerprint_probe[{i}/{attempts}] start browser={browser_mode} "
            f"impersonate={fp.impersonate} ua={fp.user_agent} proxy={_mask_proxy(proxy_url)}"
        )
        started = time.time()
        final_url = ""
        status = "error"
        error = ""
        try:
            final_url = bootstrap_chatgpt_entry(
                client,
                probe_email,
                device_id=client.device_id,
                user_agent=client.ua,
                sec_ch_ua=client.sec_ch_ua,
                impersonate=client.impersonate,
                country_code=country_code,
            )
            status = _classify(final_url)
            if status == "ok":
                ok += 1
                client.record_fingerprint_outcome(True, reason="fingerprint_probe", final_url=final_url)
            elif status == "cf":
                cf += 1
                client.record_fingerprint_outcome(False, reason="fingerprint_probe_cf", final_url=final_url)
            else:
                errors += 1
                client.record_fingerprint_outcome(False, reason=f"fingerprint_probe_{status}", final_url=final_url)
        except Exception as exc:
            errors += 1
            error = f"{type(exc).__name__}: {exc}"
            try:
                client.record_fingerprint_outcome(False, reason="fingerprint_probe_exception", final_url=final_url)
            except Exception:
                pass
        elapsed = round(time.time() - started, 2)
        item = {
            "attempt": i,
            "status": status,
            "error": error,
            "final_url": final_url[:260],
            "proxy_url": proxy_url,
            "fingerprint": client._fingerprint_payload(),
            "elapsed_seconds": elapsed,
        }
        results.append(item)
        ctx.log(
            f"fingerprint_probe[{i}/{attempts}] result={status} elapsed={elapsed}s "
            f"final={final_url[:140]}" + (f" error={error[:180]}" if error else ""),
            payload={"attempt": i, "status": status, "fingerprint": item["fingerprint"]},
        )
        ctx.update_result({"ok": ok, "cf": cf, "errors": errors, "attempts_done": i, "last": item})
        if status == "ok" and stop_on_success:
            break
        if delay_ms > 0 and i < attempts:
            time.sleep(delay_ms / 1000.0)

    ctx.update_result({"ok": ok, "cf": cf, "errors": errors, "attempts": len(results), "results": results[-50:]})
    ctx.log(f"fingerprint_probe done: ok={ok} cf={cf} errors={errors}/{len(results)}")


def _render_proxy(payload: dict[str, Any], all_settings: dict[str, str], *, attempt: int) -> str:
    probe_payload = {**payload, "attempt": attempt}
    rendered = resolve_workpool_proxy_template("fingerprint_probe", payload=probe_payload, extra=all_settings)
    if rendered is None:
        rendered = resolve_workpool_proxy_template("register", payload=probe_payload, extra=all_settings)
    if rendered is not None and rendered.url:
        return rendered.url
    return str(payload.get("proxy_url") or all_settings.get("workpool.fingerprint_probe.proxy_url") or all_settings.get("workpool.register.proxy_url") or all_settings.get("proxy_url") or "").strip()


def _classify(final_url: str) -> str:
    path = urlparse(final_url or "").path
    if not final_url:
        return "empty"
    if "api/accounts/authorize" in path or path == "/error":
        return "cf"
    return "ok"


def _parse_browsers(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(x).strip().lower() for x in value]
    else:
        items = [x.strip().lower() for x in str(value or "").replace(";", ",").split(",")]
    out = [x for x in items if x in {"chrome", "firefox", "auto"}]
    return out or ["chrome", "firefox"]


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(minimum, min(maximum, n))


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _random_email() -> str:
    prefix = "probe-" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"{prefix}@example.com"


def _mask_proxy(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1) if "://" in url else ("", url)
    _, host = rest.rsplit("@", 1)
    return (scheme + "://" if scheme else "") + "***@" + host
