"""Register a fresh TinkMail mailbox and add it to the email pool."""
from __future__ import annotations

from typing import Any

from backend.core.job_context import JobContext
from backend.core.settings import settings
from backend.core.proxy import resolve_workpool_proxy_template
from backend.core.stages import stage


@stage(
    name="tinkmail_email_register",
    optional_resources=["proxy_pool"],
    default_concurrency=1,
    description="Create a TinkMail mailbox through Camoufox/Turnstile and persist it into email_accounts.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    account = str(payload.get("account") or payload.get("name") or "").strip()
    password = str(payload.get("password") or "").strip()
    secure_email = str(
        payload.get("secure_email")
        or payload.get("secureEmail")
        or settings.get("tinkmail.secure_email", settings.get("tinkmail_secure_email", ""))
        or ""
    ).strip()
    enabled = _as_bool(payload.get("enabled", True))
    settings_all = settings.get_all()
    proxy_url = (
        str(payload.get("proxy_url") or "").strip()
        or str(settings_all.get("workpool.tinkmail_email_register.proxy_url", "") or "").strip()
        or str(settings_all.get("tinkmail.proxy_url", settings_all.get("tinkmail_proxy_url", "")) or "").strip()
        or str(settings_all.get("default_proxy_url", "") or "").strip()
        or str(settings_all.get("proxy_url", "") or "").strip()
    )
    proxy_id = ctx.proxy_id
    if not proxy_url:
        rendered = resolve_workpool_proxy_template("tinkmail_email_register", payload=payload, extra=settings_all)
        if rendered is not None and rendered.url:
            proxy_url = rendered.url
            ctx.log("TinkMail dynamic proxy rendered", payload={"provider": rendered.provider, "region": rendered.region, "ttl": rendered.ttl, "sid": rendered.sid})
    if not proxy_url and _as_bool(payload.get("acquire_proxy", settings.get_bool("workpool.tinkmail_email_register.acquire_proxy", False))):
        resource = ctx.acquire("proxy_pool", hint={"stage": "tinkmail_email_register", "region": payload.get("proxy_region") or settings_all.get("workpool.tinkmail_email_register.proxy_region", ""), "provider": payload.get("proxy_provider") or settings_all.get("workpool.tinkmail_email_register.proxy_provider", ""), "duration": payload.get("proxy_duration") or payload.get("proxy_ttl") or settings_all.get("workpool.tinkmail_email_register.proxy_duration", "") or settings_all.get("workpool.tinkmail_email_register.proxy_ttl", "")})
        rpayload = resource.payload or {}
        proxy_url = str(rpayload.get("url") or resource.id or "").strip()
        proxy_id = int(rpayload.get("proxy_id") or 0) or proxy_id
    if proxy_url:
        ctx.attach_proxy(proxy_id=proxy_id, proxy_url=proxy_url)

    ctx.log("TinkMail mailbox register started", payload={"account": account or "<auto>", "proxy": bool(proxy_url), "enabled": enabled})

    from backend.integrations.mail.tinkmail import register_and_store

    row = register_and_store(
        account=account,
        password=password,
        secure_email=secure_email,
        proxy_url=proxy_url,
        enabled=enabled,
        log=lambda msg: ctx.log(str(msg or "")),
    )
    ctx.update_result({
        "email_id": int(row.id or 0),
        "email": row.email,
        "email_address": row.email,
        "provider": row.provider,
        "enabled": bool(row.enabled),
    })
    ctx.log("TinkMail mailbox register succeeded", payload={"email_id": row.id, "email": row.email})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}
