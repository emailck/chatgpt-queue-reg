"""Tiny serialization helpers for SQLModel rows -> dicts."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.core.json_utils import json_loads


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def job_to_dict(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "pipeline_id": job.pipeline_id,
        "type": job.type,
        "status": job.status,
        "priority": job.priority,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "input": json_loads(job.input_json, fallback={}),
        "result": json_loads(job.result_json, fallback={}),
        "error": job.error,
        "account_id": job.account_id,
        "payment_link_id": job.payment_link_id,
        "email_address": job.email_address,
        "proxy_id": job.proxy_id,
        "proxy_url": job.proxy_url,
        "cancel_requested": job.cancel_requested,
        "created_at": iso(job.created_at),
        "queued_at": iso(job.queued_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "updated_at": iso(job.updated_at),
    }


def pipeline_to_dict(pipeline) -> dict[str, Any]:
    return {
        "id": pipeline.id,
        "preset": pipeline.preset,
        "stages": json_loads(pipeline.stages_json, fallback=[]),
        "stop_after": pipeline.stop_after,
        "stage_inputs": json_loads(pipeline.stage_inputs_json, fallback={}),
        "resource_bindings": json_loads(pipeline.resource_bindings_json, fallback={}),
        "status": pipeline.status,
        "current_stage": pipeline.current_stage,
        "total_steps": pipeline.total_steps,
        "completed_steps": pipeline.completed_steps,
        "account_id": pipeline.account_id,
        "payment_link_id": pipeline.payment_link_id,
        "proxy_id": pipeline.proxy_id,
        "proxy_url": pipeline.proxy_url,
        "input": json_loads(pipeline.input_json, fallback={}),
        "result": json_loads(pipeline.result_json, fallback={}),
        "error": pipeline.error,
        "cancel_requested": pipeline.cancel_requested,
        "created_at": iso(pipeline.created_at),
        "started_at": iso(pipeline.started_at),
        "finished_at": iso(pipeline.finished_at),
        "updated_at": iso(pipeline.updated_at),
    }


def account_to_dict(account, *, last_payment_link_url: str = "") -> dict[str, Any]:
    return {
        "id": account.id,
        "email": account.email,
        "password": account.password,
        "status": account.status,
        "account_id": account.account_id,
        "workspace_id": account.workspace_id,
        "proxy_id": account.proxy_id,
        "proxy_url": account.proxy_url,
        "last_error": account.last_error,
        "last_payment_link_id": account.last_payment_link_id,
        "last_payment_link_url": last_payment_link_url,
        "user_agent": account.user_agent,
        "has_access_token": bool(account.access_token),
        "has_refresh_token": bool(account.refresh_token),
        "has_session_token": bool(account.session_token),
        "created_at": iso(account.created_at),
        "registered_at": iso(account.registered_at),
        "updated_at": iso(account.updated_at),
    }


def payment_link_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "pipeline_id": row.pipeline_id,
        "job_id": row.job_id,
        "plan": row.plan,
        "promo_code": row.promo_code,
        "checkout_url": row.checkout_url,
        "checkout_session_id": row.checkout_session_id,
        "status": row.status,
        "error": row.error,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def email_account_to_dict(row) -> dict[str, Any]:
    metadata = json_loads(row.metadata_json, fallback={})
    pool_status = ""
    if isinstance(metadata, dict):
        pool_status = str(metadata.get("pool_status") or "")
    if not pool_status:
        pool_status = "available" if row.enabled else "consumed"
    return {
        "id": row.id,
        "provider": row.provider,
        "email": row.email,
        "enabled": row.enabled,
        "pool_status": pool_status,
        "has_password": bool(row.password),
        "has_refresh_token": bool(row.refresh_token),
        "api_base": row.api_base,
        "metadata": metadata,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def email_message_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "job_id": row.job_id,
        "email": row.email,
        "provider": row.provider,
        "subject": row.subject,
        "sender": row.sender,
        "body_text": row.body_text,
        "code": row.code,
        "received_at": iso(row.received_at),
        "created_at": iso(row.created_at),
    }


def proxy_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "url": row.url,
        "label": row.label,
        "region": row.region,
        "enabled": row.enabled,
        "success_count": row.success_count,
        "fail_count": row.fail_count,
        "last_used_at": iso(row.last_used_at),
    }


def browser_session_to_dict(row, *, is_alive: bool) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "is_alive": is_alive,
        "target_url": row.target_url,
        "browser_type": row.browser_type,
        "proxy_url": row.proxy_url,
        "user_agent": row.user_agent,
        "har_path": row.har_path,
        "account_id": row.account_id,
        "payment_link_id": row.payment_link_id,
        "pipeline_id": row.pipeline_id,
        "job_id": row.job_id,
        "error": row.error,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
        "closed_at": iso(row.closed_at),
    }


def event_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "pipeline_id": row.pipeline_id,
        "level": row.level,
        "event_type": row.event_type,
        "message": row.message,
        "payload": json_loads(row.payload_json, fallback={}),
        "created_at": iso(row.created_at),
    }


def access_token_account_to_dict(row, *, include_secrets: bool = False) -> dict[str, Any]:
    """Default view masks long secret tokens; export view returns them in full."""
    def _mask(value: str) -> str:
        s = str(value or "")
        if not s:
            return ""
        return s if include_secrets else (s[:24] + "...")

    return {
        "id": row.id,
        "pipeline_id": row.pipeline_id,
        "chatgpt_account_id": row.chatgpt_account_id,
        "email": row.email,
        "password": row.password if include_secrets else ("***" if row.password else ""),
        "account_id": row.account_id,
        "workspace_id": row.workspace_id,
        "access_token": _mask(row.access_token),
        "refresh_token": _mask(row.refresh_token),
        "id_token": _mask(row.id_token),
        "session_token": _mask(row.session_token),
        "has_access_token": bool(row.access_token),
        "has_refresh_token": bool(row.refresh_token),
        "has_session_token": bool(row.session_token),
        "user_agent": row.user_agent,
        "proxy_id": row.proxy_id,
        "proxy_url": row.proxy_url,
        "note": row.note,
        "metadata": json_loads(row.metadata_json, fallback={}),
        "cookies": json_loads(row.cookies_json, fallback=[]) if include_secrets else None,
        "local_storage": json_loads(row.local_storage_json, fallback={}) if include_secrets else None,
        "browser_fingerprint": json_loads(row.browser_fingerprint_json, fallback={}) if include_secrets else None,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }
