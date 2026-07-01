"""Batch Codex invite orchestrator.

One job accepts multiple inviter accounts, sends up to N invites per inviter
(default/max 5), waits until all invite attempts are finished, then creates
child pipelines `sso_oauth -> active` for every successfully invited email.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from backend.core.job_context import JobContext
from backend.core.pipeline import create_pipeline
from backend.core.settings import settings
from backend.core.stages import stage
from backend.schemas.stage_io import CodexBatchInviteInput, CodexBatchInviteOutput
from backend.stages import codex_invitation as inv


@stage(
    name="codex_batch_invite",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=1,
    input_schema=CodexBatchInviteInput,
    output_schema=CodexBatchInviteOutput,
    description="Batch invite with multiple mother accounts; after all invitations finish, start sso_oauth->active child pipelines for invited emails.",
)
def run(ctx: JobContext) -> None:
    payload = dict(ctx.input or {})
    config = {**settings.get_all(), **_workpool_config("workpool.codex_batch_invite."), **dict(payload.get("extra_config") or {})}

    inviters = _parse_inviters(payload)
    if not inviters:
        raise RuntimeError("codex_batch_invite requires inviter_list/inviter_emails/inviter_account_ids")

    invite_count = _read_int(
        payload.get("invite_count_per_inviter", payload.get("invite_count", config.get("invite_count_per_inviter", config.get("invite_count")))),
        default=5,
        minimum=1,
        maximum=5,
    )
    prefix_len = _read_int(payload.get("prefix_len", config.get("prefix_len")), default=20, minimum=3, maximum=64)
    dry_run = _as_bool(payload.get("dry_run", config.get("dry_run", False)))
    activate_after_invite = _as_bool(payload.get("activate_after_invite", config.get("activate_after_invite", True)), default=True)
    invite_concurrency = _read_int(
        payload.get("invite_concurrency", payload.get("concurrency", config.get("invite_concurrency", config.get("concurrency")))),
        default=min(5, len(inviters)),
        minimum=1,
        maximum=max(1, min(50, len(inviters))),
    )

    ctx.log("starting codex_batch_invite", payload={
        "inviter_count": len(inviters),
        "invite_count_per_inviter": invite_count,
        "prefix_len": prefix_len,
        "dry_run": dry_run,
        "activate_after_invite": activate_after_invite,
        "invite_concurrency": invite_concurrency,
    })

    per_inviter: list[dict[str, Any]] = []
    invited_emails: list[str] = []
    failed: list[dict[str, Any]] = []

    # Invitation phase is the only concurrent part of this full flow.
    # All inviters are submitted first; activation child pipelines are created
    # only after every invite attempt has finished.
    with ThreadPoolExecutor(max_workers=invite_concurrency, thread_name_prefix="codex-batch-invite") as pool:
        futures = {
            pool.submit(_run_inviter_job, ctx, payload, config, inviter_payload, invite_count, prefix_len, dry_run, idx, len(inviters)): idx
            for idx, inviter_payload in enumerate(inviters, 1)
        }
        for future in as_completed(futures):
            ctx.check_cancelled()
            idx = futures[future]
            try:
                result = future.result()
                per_inviter.append(result)
                for email in result.get("emails") or []:
                    email_s = str(email or "").strip()
                    if email_s:
                        invited_emails.append(email_s)
                ctx.log(f"batch invite [{idx}/{len(inviters)}] completed", payload={
                    "inviter": result.get("source_email") or result.get("inviter"),
                    "invited": result.get("emails") or [],
                    "sent": result.get("sent"),
                    "dry_run": result.get("dry_run"),
                })
            except Exception as exc:
                # _run_inviter_job wraps expected failures, but keep this guard
                # so one broken worker never aborts other inviter results.
                item = {"inviter": "", "error": str(exc), "index": idx}
                failed.append(item)
                per_inviter.append({"inviter": "", "ok": False, "error": str(exc), "index": idx})
                ctx.log(f"batch invite [{idx}/{len(inviters)}] failed: {exc}", level="error", payload=item)

    for item in per_inviter:
        if item.get("ok") is False:
            failed.append({"inviter": str(item.get("inviter") or item.get("source_email") or ""), "error": str(item.get("error") or "")})
    per_inviter.sort(key=lambda item: int(item.get("index") or 0))

    # De-duplicate while preserving order.
    invited_emails = list(dict.fromkeys(invited_emails))

    activation_pipeline_ids: list[int] = []
    if activate_after_invite and invited_emails and not dry_run:
        ctx.log("all invitations finished; creating activation child pipelines", payload={"count": len(invited_emails)})
        for email in invited_emails:
            ctx.check_cancelled()
            pid = create_pipeline(
                stages=["sso_oauth", "active"],
                preset="codex_activation_child",
                stage_inputs={
                    "sso_oauth": {"sso_email": email, "email": email},
                    "active": {"email": email, "sso_email": email},
                },
                resource_bindings={},
                request_payload={"email": email, "sso_email": email},
            )
            activation_pipeline_ids.append(pid)
            ctx.log("activation child pipeline created", payload={"email": email, "pipeline_id": pid})
    elif activate_after_invite and dry_run:
        ctx.log("dry-run enabled; activation child pipelines not created")

    result = {
        "inviter_count": len(inviters),
        "invite_count_per_inviter": invite_count,
        "invited_count": len(invited_emails),
        "invited_emails": invited_emails,
        "activation_pipeline_ids": activation_pipeline_ids,
        "activation_started": bool(activation_pipeline_ids),
        "failed_count": len(failed),
        "failed": failed,
        "per_inviter": per_inviter,
        "dry_run": dry_run,
    }
    ctx.update_result(result)
    if failed and not invited_emails:
        raise RuntimeError(f"all batch inviters failed ({len(failed)})")
    ctx.log("codex_batch_invite completed", payload=result)


def _run_inviter_job(
    ctx: JobContext,
    payload: dict[str, Any],
    config: dict[str, Any],
    inviter_payload: dict[str, Any],
    invite_count: int,
    prefix_len: int,
    dry_run: bool,
    idx: int,
    total: int,
) -> dict[str, Any]:
    ctx.check_cancelled()
    one = {
        **payload,
        **inviter_payload,
        "invite_count": invite_count,
        "prefix_len": prefix_len,
        "dry_run": dry_run,
    }
    one.pop("inviter_list", None)
    one.pop("inviter_emails", None)
    one.pop("inviter_account_ids", None)
    label = one.get("inviter_email") or one.get("inviter_account_id") or one.get("email") or one.get("email_id") or one.get("source_id")
    ctx.log(f"batch invite [{idx}/{total}] inviter={label}")
    try:
        result = _run_single_invite(ctx, one, config)
        result["index"] = idx
        result["inviter"] = str(label or result.get("source_email") or "")
        return result
    except Exception as exc:
        item = {"index": idx, "inviter": str(label or ""), "ok": False, "error": str(exc)}
        ctx.log(f"batch invite [{idx}/{total}] failed: {exc}", level="error", payload=item)
        return item


def _run_single_invite(ctx: JobContext, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    source = inv._resolve_source(payload, config)  # noqa: SLF001 - intentional stage reuse
    domain = str(payload.get("domain") or "").strip().lstrip("@") or inv._domain_from_email(source.email)  # noqa: SLF001
    if not domain:
        raise RuntimeError(f"cannot infer email domain from source email={source.email!r}")

    invite_count = _read_int(payload.get("invite_count"), default=5, minimum=1, maximum=5)
    prefix_len = _read_int(payload.get("prefix_len"), default=20, minimum=3, maximum=64)
    dry_run = _as_bool(payload.get("dry_run", False))
    check_eligibility = _as_bool(payload.get("check_eligibility", config.get("check_eligibility", True)), default=True)
    verify_tls = not _as_bool(payload.get("insecure", config.get("insecure", False)))
    referral_key = str(payload.get("referral_key") or config.get("referral_key") or inv.REFERRAL_KEY).strip() or inv.REFERRAL_KEY

    recipients = inv._random_emails(domain, invite_count, prefix_len)  # noqa: SLF001
    proxy_url = str(payload.get("proxy_url") or ctx.effective_proxy_url() or config.get("proxy_url") or "").strip()
    session_type, session = inv._build_session(proxy_url)  # noqa: SLF001
    ctx.log("batch invite session initialized", payload={"source_email": source.email, "session_type": session_type, "proxy": bool(proxy_url)})

    remaining = None
    if check_eligibility:
        remaining = inv._check_eligibility(session, source.access_token, source.chatgpt_account_id, referral_key, verify_tls=verify_tls)  # noqa: SLF001
        if remaining is not None:
            if remaining <= 0:
                raise RuntimeError("source account has no remaining Codex referral invite quota")
            if len(recipients) > remaining:
                recipients = recipients[:remaining]

    invited_email = recipients[0] if recipients else ""
    base = {
        "ok": True,
        "source_type": source.source_type,
        "source_id": source.source_id,
        "source_email": source.email,
        "domain": domain,
        "emails": recipients,
        "invited_email": invited_email,
        "email": invited_email,
        "sso_email": invited_email,
        "invite_count": len(recipients),
        "remaining_invites": remaining,
        "dry_run": dry_run,
    }
    if dry_run:
        return {**base, "sent": False, "status_code": 0, "response": {}}

    resp = session.post(
        inv.INVITE_URL,
        headers=inv._headers(source.access_token, source.chatgpt_account_id, is_json=True),  # noqa: SLF001
        json={"referral_key": referral_key, "emails": recipients},
        timeout=_read_int(config.get("timeout_seconds"), default=30, minimum=5, maximum=180),
        verify=verify_tls,
    )
    try:
        response_payload = resp.json()
    except Exception:
        response_payload = {"text": (resp.text or "")[:5000]}
    if resp.status_code != 200:
        raise RuntimeError(f"Codex invitation failed: HTTP {resp.status_code} {(resp.text or '')[:300]}")
    invites = response_payload.get("invites", []) if isinstance(response_payload, dict) else []
    return {**base, "sent": True, "status_code": resp.status_code, "response": response_payload, "invites": invites}


def _parse_inviters(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items: list[Any] = []
    for key in ("inviter_list", "inviters", "inviter_emails"):
        raw_items.extend(_split_many(payload.get(key)))
    account_ids = _split_many(payload.get("inviter_account_ids"))

    out: list[dict[str, Any]] = []
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        if "@" in text:
            out.append({"inviter_email": text, "source_type": payload.get("source_type") or "auto"})
        else:
            out.append({"inviter_account_id": text, "source_type": payload.get("source_type") or "auto"})
    for item in account_ids:
        text = str(item or "").strip()
        if text:
            out.append({"inviter_account_id": text, "source_type": payload.get("source_type") or "auto"})

    # Backward compatible single inviter fields.
    if payload.get("inviter_email") or payload.get("email"):
        out.append({"inviter_email": payload.get("inviter_email") or payload.get("email"), "source_type": payload.get("source_type") or "auto"})
    if payload.get("inviter_account_id") or payload.get("email_id") or payload.get("source_id"):
        out.append({"inviter_account_id": payload.get("inviter_account_id") or payload.get("email_id") or payload.get("source_id"), "source_type": payload.get("source_type") or "auto"})

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in out:
        key = (str(item.get("inviter_email") or ""), str(item.get("inviter_account_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _split_many(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value or "")
    for sep in ["\r\n", "\r", "\n", ";", "，"]:
        text = text.replace(sep, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _workpool_config(prefix: str) -> dict[str, Any]:
    return {key[len(prefix):]: value for key, value in settings.get_all().items() if key.startswith(prefix)}


def _read_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(minimum, min(maximum, n))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
