"""Enable TOTP MFA for an existing ChatGPT account."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core.db import session_scope
from backend.core.json_utils import json_dumps, json_loads
from backend.core.proxy import resolve_workpool_proxy_template
from backend.core.settings import settings
from backend.core.stages import stage
from backend.core.time_utils import utcnow
from backend.integrations.mail.email_service import MicrosoftEmailService
from backend.integrations.chatgpt.mfa_client import (
    ChatGPTMfaClient,
    ChatGPTTotpMfaAdapter,
    create_twofauth_adapter_from_uri,
    build_totp_adapter_from_metadata,
)
from backend.stages.chatgpt_session import refresh_or_relogin_account_session
from backend.models.account import ChatGPTAccount
from backend.schemas.stage_io import ChatGPTMfaSetupInput, ChatGPTMfaSetupOutput


@stage(
    name="chatgpt_mfa_setup",
    requires_resources=[],
    optional_resources=["proxy_pool"],
    default_concurrency=2,
    input_schema=ChatGPTMfaSetupInput,
    output_schema=ChatGPTMfaSetupOutput,
    description="Enable ChatGPT TOTP MFA for an existing account and persist the factor metadata.",
)
def run(ctx) -> None:
    payload = dict(ctx.input or {})
    extra_config = dict(payload.get("extra_config") or {})
    config = {**settings.get_all(), **_workpool_config("workpool.chatgpt_mfa_setup."), **extra_config}

    account_id = _to_int(payload.get("account_id") or ctx.account_id)
    if not account_id:
        raise RuntimeError("chatgpt_mfa_setup requires account_id")
    ctx.attach_account(account_id)

    factor_type = str(payload.get("factor_type") or config.get("factor_type") or "totp").strip().lower()
    if factor_type != "totp":
        raise RuntimeError(f"unsupported factor_type for chatgpt_mfa_setup: {factor_type!r}")
    force_reenroll = _as_bool(payload.get("force_reenroll", config.get("force_reenroll", False)), default=False)
    verify_login_challenge = _as_bool(payload.get("verify_login_challenge", config.get("verify_login_challenge", False)), default=False)
    api_base_url = str(payload.get("api_base_url") or config.get("api_base_url") or "https://chatgpt.com").strip()
    auth_base_url = str(payload.get("auth_base_url") or config.get("auth_base_url") or "https://auth.openai.com").strip()
    mfa_timeout_seconds = max(1, _to_int(config.get("twofauth_timeout_seconds") or 30) or 30)

    with session_scope() as s:
        account = s.get(ChatGPTAccount, account_id)
        if account is None:
            raise RuntimeError(f"account {account_id} not found")
        account_email = str(account.email or "").strip()
        account_password = str(account.password or "").strip()
        access_token = str(account.access_token or payload.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"account {account_id} has no access_token")
        metadata = json_loads(account.metadata_json, fallback={}) or {}
        mfa_provider = build_totp_adapter_from_metadata(
            metadata,
            config=config,
            log_fn=lambda msg: ctx.log(str(msg or "")),
            timeout_seconds=mfa_timeout_seconds,
        )
        cookies = json_loads(account.cookies_json, fallback=[]) or []
        user_agent = str(account.user_agent or metadata.get("user_agent") or "").strip()
        device_id = str(metadata.get("device_id") or account.account_id or account.id or "").strip()
        oai_session_id = str(metadata.get("oai_session_id") or "").strip()
        oai_client_version = str(metadata.get("oai_client_version") or metadata.get("client_version") or "").strip()
        oai_client_build_number = str(metadata.get("oai_client_build_number") or "").strip()
        account_proxy_url = str(account.proxy_url or "").strip()
        account_proxy_id = account.proxy_id
        raw_account = metadata.get("account") if isinstance(metadata.get("account"), dict) else {}
        chatgpt_account_id = str(account.account_id or raw_account.get("id") or "").strip()

    proxy_url = (
        ctx.effective_proxy_url()
        or str(payload.get("proxy_url") or "").strip()
        or str(config.get("proxy_url") or "").strip()
        or account_proxy_url
    )
    if not proxy_url:
        rendered = resolve_workpool_proxy_template("chatgpt_mfa_setup", payload=payload, extra=config)
        if rendered is not None and rendered.url:
            proxy_url = rendered.url
            ctx.attach_proxy(proxy_id=None, proxy_url=proxy_url)
            ctx.log("chatgpt_mfa_setup dynamic proxy rendered", payload={"provider": rendered.provider, "region": rendered.region, "ttl": rendered.ttl, "sid": rendered.sid})
    elif account_proxy_id:
        ctx.attach_proxy(proxy_id=account_proxy_id, proxy_url=proxy_url)

    ctx.log(
        "starting chatgpt_mfa_setup",
        payload={
            "account_id": account_id,
            "email": account_email,
            "factor_type": factor_type,
            "mfa_code_provider": str(config.get("mfa_code_provider") or "local").strip().lower(),
            "twofauth_timeout_seconds": max(1, _to_int(config.get("twofauth_timeout_seconds") or 30) or 30),
            "proxy_provided": bool(proxy_url),
            "force_reenroll": force_reenroll,
            "verify_login_challenge": verify_login_challenge,
        },
    )

    email_service = MicrosoftEmailService(extra_config={"fixed_email": account_email})
    refresh_or_relogin_account_session(
        account_id,
        ctx,
        password=account_password,
        otp_provider=email_service,
        mfa_provider=mfa_provider,
        proxy_url_override=proxy_url,
        max_attempts=5,
    )

    def _load_client() -> tuple[ChatGPTMfaClient, dict[str, Any]]:
        with session_scope() as s:
            account_row = s.get(ChatGPTAccount, account_id)
            if account_row is None:
                raise RuntimeError(f"account {account_id} not found after session refresh")
            access_token = str(account_row.access_token or "").strip()
            metadata = json_loads(account_row.metadata_json, fallback={}) or {}
            cookies = json_loads(account_row.cookies_json, fallback=[]) or []
            user_agent = str(account_row.user_agent or metadata.get("user_agent") or "").strip()
            device_id = str(metadata.get("device_id") or account_row.account_id or account_row.id or "").strip()
            oai_session_id = str(metadata.get("oai_session_id") or "").strip()
            oai_client_version = str(metadata.get("oai_client_version") or metadata.get("client_version") or "").strip()
            oai_client_build_number = str(metadata.get("oai_client_build_number") or "").strip()
            raw_account = metadata.get("account") if isinstance(metadata.get("account"), dict) else {}
            chatgpt_account_id = str(account_row.account_id or raw_account.get("id") or "").strip()
        return ChatGPTMfaClient(
            access_token=access_token,
            cookies=cookies if isinstance(cookies, list) else [],
            user_agent=user_agent,
            oai_session_id=oai_session_id,
            oai_client_version=oai_client_version,
            oai_client_build_number=oai_client_build_number,
            device_id=device_id,
            chatgpt_account_id=chatgpt_account_id,
            proxy_url=proxy_url,
            api_base_url=api_base_url,
            auth_base_url=auth_base_url,
            log_fn=lambda msg: ctx.log(str(msg or "")),
        ), metadata

    client, metadata = _load_client()
    before_info = client.get_mfa_info(factor_type=factor_type)
    before_summary = _summarize_mfa_info(before_info)
    if before_summary["mfa_enabled"] and not force_reenroll:
        ctx.log("chatgpt_mfa_setup detected existing TOTP enrollment; reuse current state")
        existing_mfa = metadata.get("mfa") if isinstance(metadata.get("mfa"), dict) else {}
        existing_provider = str(existing_mfa.get("provider") or "local").strip().lower() or "local"
        existing_twofauth = existing_mfa.get("twofauth") if isinstance(existing_mfa.get("twofauth"), dict) else {}
        _persist_mfa_metadata(
            account_id,
            account_email,
            before_info,
            None,
            provider=existing_provider,
            twofauth_account_id=str(existing_mfa.get("twofauth_account_id") or existing_twofauth.get("account_id") or "").strip(),
            twofauth_base_url=str(existing_twofauth.get("base_url") or "").strip(),
        )
        ctx.update_result(
            _result_payload(
                account_id=account_id,
                email=account_email,
                factor_type=factor_type,
                enrollment=None,
                final_info=before_info,
                login_verify_ok=False,
                login_verify_error="",
                mfa_provider=existing_provider,
                twofauth_account_id=str(existing_mfa.get("twofauth_account_id") or existing_twofauth.get("account_id") or "").strip(),
            )
        )
        return

    enrollment = None
    enroll_error = ""
    for attempt in range(2):
        try:
            enrollment = client.enroll_totp(factor_type=factor_type)
            break
        except Exception as exc:
            enroll_error = str(exc)
            if attempt == 0 and "recent_auth_required" in enroll_error:
                ctx.log("mfa/enroll requires recent auth; forcing relogin and retry", level="warning")
                refresh_or_relogin_account_session(
                    account_id,
                    ctx,
                    password=account_password,
                    otp_provider=email_service,
                    mfa_provider=mfa_provider,
                    proxy_url_override=proxy_url,
                    max_attempts=5,
                    force_relogin=True,
                )
                client, metadata = _load_client()
                continue
            raise
    if enrollment is None:
        raise RuntimeError(f"mfa/enroll failed: {enroll_error}")
    if not enrollment.session_id:
        raise RuntimeError("mfa/enroll did not return session_id")
    if not enrollment.secret:
        raise RuntimeError("mfa/enroll did not return secret")
    ctx.log(
        "chatgpt_mfa_setup enrollment created",
        payload={
            "account_id": account_id,
            "factor_id": enrollment.factor_id,
            "session_id": enrollment.session_id,
            "secret_len": len(enrollment.secret),
        },
    )

    mfa_code_provider = str(config.get("mfa_code_provider") or "local").strip().lower()
    twofauth_account_id = ""
    if mfa_code_provider in {"2fauth", "twofauth", "external"}:
        mfa_provider = create_twofauth_adapter_from_uri(
            enrollment.qr_code_secret_url,
            account_label=account_email,
            factor_id=enrollment.factor_id,
            config=config,
            log_fn=lambda msg: ctx.log(str(msg or "")),
            timeout_seconds=mfa_timeout_seconds,
        )
        twofauth_account_id = str(getattr(mfa_provider, "get_twofauth_account_id", lambda: "")() or "").strip()
        ctx.log(
            "chatgpt_mfa_setup external MFA provider attached",
            payload={"provider": "twofauth", "account_id": twofauth_account_id},
        )
    else:
        mfa_provider = ChatGPTTotpMfaAdapter(
            secret=enrollment.secret,
            factor_id=enrollment.factor_id,
            log_fn=lambda msg: ctx.log(str(msg or "")),
            timeout_seconds=mfa_timeout_seconds,
        )
    code = mfa_provider.get_code(account_email)
    ctx.log(
        "chatgpt_mfa_setup fetched current TOTP code from 2FAuth"
        if mfa_code_provider in {"2fauth", "twofauth", "external"}
        else "chatgpt_mfa_setup generated current TOTP code"
    )
    activate_resp = client.activate_totp_enrollment(
        session_id=enrollment.session_id,
        code=code,
        factor_type=factor_type,
    )
    if isinstance(activate_resp, dict) and "success" in activate_resp and not bool(activate_resp.get("success")):
        raise RuntimeError(f"mfa activation failed: {activate_resp}")

    final_info = client.get_mfa_info(factor_type=factor_type, action="enable")
    final_summary = _summarize_mfa_info(final_info)
    if not final_summary["mfa_enabled"]:
        raise RuntimeError(f"mfa activation did not enable MFA: {final_info}")
    if enrollment.factor_id and final_summary["native_default_factor_id"] and enrollment.factor_id != final_summary["native_default_factor_id"]:
        raise RuntimeError(
            "mfa activation factor_id mismatch: "
            f"enroll={enrollment.factor_id} final={final_summary['native_default_factor_id']}"
        )
    login_verify_ok = False
    login_verify_error = ""
    if verify_login_challenge:
        if enrollment.factor_id:
            try:
                verify_resp = client.verify_totp_login(factor_id=enrollment.factor_id, code=code, factor_type=factor_type)
                login_verify_ok = bool(verify_resp.get("success", True)) if isinstance(verify_resp, dict) else True
                ctx.log("chatgpt_mfa_setup login verify completed", payload={"account_id": account_id, "factor_id": enrollment.factor_id})
            except Exception as exc:
                login_verify_error = str(exc)
                ctx.log(f"chatgpt_mfa_setup login verify failed: {exc}", level="warning")
        else:
            login_verify_error = "missing factor_id for login verify"

    _persist_mfa_metadata(
        account_id,
        account_email,
        final_info,
        enrollment,
        provider=mfa_code_provider if mfa_code_provider in {"2fauth", "twofauth", "external"} else "local",
        twofauth_account_id=twofauth_account_id,
        twofauth_base_url=str(config.get("twofauth_base_url") or config.get("workpool.chatgpt_mfa_setup.twofauth_base_url") or "").strip(),
    )
    ctx.update_result(
        _result_payload(
            account_id=account_id,
            email=account_email,
            factor_type=factor_type,
            enrollment=enrollment,
            final_info=final_info,
            login_verify_ok=login_verify_ok,
            login_verify_error=login_verify_error,
            mfa_provider="twofauth" if mfa_code_provider in {"2fauth", "twofauth", "external"} else "local",
            twofauth_account_id=twofauth_account_id,
        )
    )
    ctx.log(
        "chatgpt_mfa_setup completed",
        payload={
            "account_id": account_id,
            "mfa_enabled": bool(final_summary["mfa_enabled"]),
            "factor_id": str(final_summary["native_default_factor_id"] or enrollment.factor_id or ""),
        },
    )


def _result_payload(
    *,
    account_id: int,
    email: str,
    factor_type: str,
    enrollment,
    final_info: dict[str, Any],
    login_verify_ok: bool,
    login_verify_error: str,
    mfa_provider: str = "local",
    twofauth_account_id: str = "",
) -> dict[str, Any]:
    factors = final_info.get("factors") if isinstance(final_info.get("factors"), dict) else {}
    totp_factors = factors.get("totp") if isinstance(factors.get("totp"), list) else []
    factor_id = str(final_info.get("native_default_factor_id") or (enrollment.factor_id if enrollment else "") or "").strip()
    return {
        "account_id": account_id,
        "email": email,
        "factor_type": factor_type,
        "mfa_enabled": bool(final_info.get("mfa_enabled")),
        "factor_id": factor_id,
        "enrollment_session_id": str(enrollment.session_id if enrollment else ""),
        "qr_code_secret_url": str(enrollment.qr_code_secret_url if enrollment else ""),
        "native_default_factor_id": str(final_info.get("native_default_factor_id") or ""),
        "totp_factor_count": len(totp_factors),
        "login_verify_ok": bool(login_verify_ok),
        "login_verify_error": str(login_verify_error or ""),
        "mfa_provider": str(mfa_provider or ""),
        "twofauth_account_id": str(twofauth_account_id or ""),
        "mfa_info": final_info,
    }


def _persist_mfa_metadata(
    account_id: int,
    email: str,
    final_info: dict[str, Any],
    enrollment,
    *,
    provider: str = "local",
    twofauth_account_id: str = "",
    twofauth_base_url: str = "",
) -> None:
    now = utcnow()
    factors = final_info.get("factors") if isinstance(final_info.get("factors"), dict) else {}
    totp_factors = factors.get("totp") if isinstance(factors.get("totp"), list) else []
    factor_id = str(final_info.get("native_default_factor_id") or (enrollment.factor_id if enrollment else "") or "").strip()
    with session_scope() as s:
        row = s.get(ChatGPTAccount, int(account_id))
        if row is None:
            return
        metadata = json_loads(row.metadata_json, fallback={}) or {}
        previous_mfa = metadata.get("mfa") if isinstance(metadata.get("mfa"), dict) else {}
        secret = ""
        qr_code_secret_url = ""
        if provider != "twofauth":
            secret = str(getattr(enrollment, "secret", "") or previous_mfa.get("secret") or "").strip()
            qr_code_secret_url = str(
                getattr(enrollment, "qr_code_secret_url", "")
                or previous_mfa.get("qr_code_secret_url")
                or ""
            ).strip()
        mfa_payload = {
            "provider": provider,
            "enabled": bool(final_info.get("mfa_enabled")),
            "enabled_v2": bool(final_info.get("mfa_enabled_v2")),
            "factor_type": "totp",
            "factor_id": factor_id,
            "native_default_factor_id": str(final_info.get("native_default_factor_id") or ""),
            "totp_factor_count": len(totp_factors),
            "last_setup_at": now.isoformat(),
            "email": email or str(previous_mfa.get("email") or ""),
            "enrollment_session_id": str(getattr(enrollment, "session_id", "") or previous_mfa.get("enrollment_session_id") or ""),
        }
        if provider == "twofauth":
            mfa_payload["twofauth"] = {
                "account_id": str(twofauth_account_id or ""),
                "base_url": str(twofauth_base_url or ""),
                "created_at": now.isoformat(),
            }
        else:
            mfa_payload["qr_code_secret_url"] = qr_code_secret_url
            mfa_payload["secret"] = secret
        metadata["mfa"] = mfa_payload
        row.metadata_json = json_dumps(metadata)
        row.updated_at = now
        s.add(row)


def _summarize_mfa_info(info: dict[str, Any]) -> dict[str, Any]:
    factors = info.get("factors") if isinstance(info.get("factors"), dict) else {}
    totp_factors = factors.get("totp") if isinstance(factors.get("totp"), list) else []
    return {
        "mfa_enabled": bool(info.get("mfa_enabled")),
        "mfa_enabled_v2": bool(info.get("mfa_enabled_v2")),
        "native_default_factor_id": str(info.get("native_default_factor_id") or ""),
        "totp_factor_count": len(totp_factors),
    }


def _workpool_config(prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in settings.get_all().items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = value
    return out


def _to_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}
