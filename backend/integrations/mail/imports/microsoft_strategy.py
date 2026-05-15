"""Microsoft (Outlook / Hotmail) bulk-import strategy.

Reads `email----password----client_id----refresh_token` (OAuth) or
`email----mailapi_url` (mailapi-url polling) lines, optionally fans them out
into Plus-aliases ("裂变"), probes OAuth availability via
`MicrosoftMailbox.probe_oauth_availability`, and writes survivors to the new
`email_accounts` table.
"""
from __future__ import annotations

import os
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from sqlmodel import Session, select

from backend.core.db import engine, session_scope
from backend.core.json_utils import json_dumps
from backend.integrations.mail.microsoft import MicrosoftMailbox
from backend.models.email import EmailAccount

from .microsoft_import_rules import (
    ACCOUNT_TYPE_MAILAPI_URL,
    ACCOUNT_TYPE_MICROSOFT_OAUTH,
    AutoDetectRowParser,
    DuplicateMicrosoftMailboxRule,
    MailApiUrlFormatRule,
    MicrosoftMailImportRecord,
    MicrosoftMailImportRuleEngine,
)
from .schemas import (
    MailImportBatchDeleteRequest,
    MailImportDeleteItem,
    MailImportDeleteRequest,
    MailImportExecuteRequest,
    MailImportProviderDescriptor,
    MailImportResponse,
    MailImportSnapshot,
    MailImportSnapshotItem,
    MailImportSnapshotRequest,
    MailImportSummary,
)


PROVIDER_NAME = "microsoft"
DEFAULT_ALIAS_COUNT = 5
MAX_ALIAS_COUNT = 5


class MicrosoftMailImportStrategy:
    """Stateless service: instantiate once, call methods as needed."""

    descriptor = MailImportProviderDescriptor(
        type=PROVIDER_NAME,
        label="微软邮箱（Outlook / Hotmail）",
        description=(
            "导入微软邮箱本地账号池，运行时通过 Microsoft Graph 取邮件 / 验证码。"
            "支持邮箱裂变（同邮箱按 + 别名扩展）。"
        ),
        helper_text=(
            "支持两种格式并自动识别："
            "1) 邮箱----密码----client_id----refresh_token；"
            "2) 邮箱----mailapi_url。"
        ),
        content_placeholder=(
            "example@outlook.com----password----client_id----refresh_token\n"
            "example@hotmail.com----https://mailapi.icu/key?type=html&orderNo=xxx"
        ),
        preview_empty_text="当前还没有已导入的微软邮箱账号。",
    )

    # ---- alias fan-out ("裂变") -------------------------------------------------

    @staticmethod
    def _base_email_key(email: str) -> str:
        local, domain = str(email or "").strip().lower().split("@", 1)
        return f"{local.split('+', 1)[0]}@{domain}"

    @staticmethod
    def _generate_alias_email(email: str) -> str:
        local, domain = str(email or "").split("@", 1)
        base_local = local.split("+", 1)[0]
        suffix = "".join(random.choices(string.ascii_lowercase, k=6))
        return f"{base_local}+{suffix}@{domain}"

    @classmethod
    def _expand_records_with_aliases(
        cls,
        records: list[MicrosoftMailImportRecord],
        *,
        enabled: bool,
        alias_count: int,
        include_original: bool,
    ) -> list[MicrosoftMailImportRecord]:
        if not enabled:
            return records

        expanded_groups: list[list[MicrosoftMailImportRecord]] = []
        target_count = max(1, min(int(alias_count or 1), MAX_ALIAS_COUNT))

        for record in records:
            emails: list[str] = []
            seen: set[str] = set()
            if include_original:
                emails.append(record.email)
                seen.add(record.email)

            attempts = 0
            while len(emails) < target_count + (1 if include_original else 0) and attempts < target_count * 20:
                candidate = cls._generate_alias_email(record.email)
                attempts += 1
                if candidate in seen:
                    continue
                seen.add(candidate)
                emails.append(candidate)

            if not emails:
                emails.append(record.email)

            group: list[MicrosoftMailImportRecord] = []
            for email in emails:
                group.append(MicrosoftMailImportRecord(
                    line_number=record.line_number,
                    email=email,
                    password=record.password,
                    client_id=record.client_id,
                    refresh_token=record.refresh_token,
                    account_type=record.account_type,
                    mailapi_url=record.mailapi_url,
                ))
            expanded_groups.append(group)

        # Round-robin to interleave aliases of different originals.
        max_group_len = max((len(g) for g in expanded_groups), default=0)
        out: list[MicrosoftMailImportRecord] = []
        for index in range(max_group_len):
            slice_ = [g[index] for g in expanded_groups if index < len(g)]
            slice_.sort(key=lambda item: cls._base_email_key(item.email))
            out.extend(slice_)
        return out

    # ---- snapshot --------------------------------------------------------------

    def get_snapshot(self, request: MailImportSnapshotRequest) -> MailImportSnapshot:
        with Session(engine) as s:
            accounts = list(
                s.exec(
                    select(EmailAccount)
                    .where(EmailAccount.provider == PROVIDER_NAME)
                    .order_by(EmailAccount.id)
                ).all()
            )
        limit = max(int(request.preview_limit or 0), 0)
        preview = accounts[:limit] if limit else []
        items = [
            MailImportSnapshotItem(
                index=idx,
                email=account.email,
                enabled=bool(account.enabled),
                has_oauth=bool(account.refresh_token),
                account_type=_account_type(account),
            )
            for idx, account in enumerate(preview, start=1)
        ]
        return MailImportSnapshot(
            type=PROVIDER_NAME,
            label=self.descriptor.label,
            count=len(accounts),
            items=items,
            truncated=len(accounts) > limit if limit > 0 else len(accounts) > 0,
        )

    # ---- execute ---------------------------------------------------------------

    def execute(self, request: MailImportExecuteRequest) -> MailImportResponse:
        lines = (request.content or "").splitlines()
        actionable = [
            (idx, str(raw or "").strip())
            for idx, raw in enumerate(lines, start=1)
            if str(raw or "").strip() and not str(raw or "").strip().startswith("#")
        ]

        success = 0
        failed = 0
        errors: list[str] = []
        accounts_meta: list[dict[str, Any]] = []
        valid_records: list[MicrosoftMailImportRecord] = []

        with Session(engine) as s:
            existing_emails = {
                str(email or "").strip()
                for email in s.exec(
                    select(EmailAccount.email).where(EmailAccount.provider == PROVIDER_NAME)
                ).all()
            }

        parser = AutoDetectRowParser()
        rule_engine = MicrosoftMailImportRuleEngine(rules=[
            DuplicateMicrosoftMailboxRule(),
            MailApiUrlFormatRule(),
        ])
        seen_in_batch: set[str] = set()

        for line_number, line in actionable:
            try:
                record = parser.parse(line_number, line)
            except ValueError as exc:
                failed += 1
                errors.append(str(exc))
                continue
            if record.email in seen_in_batch:
                failed += 1
                errors.append(f"行 {line_number}: 导入内容存在重复邮箱: {record.email}")
                continue
            seen_in_batch.add(record.email)
            check = rule_engine.evaluate(record, {"existing_emails": existing_emails})
            if not check.get("ok"):
                failed += 1
                errors.append(str(check.get("message") or f"行 {line_number}: 导入失败"))
                continue
            valid_records.append(record)

        valid_records = self._expand_records_with_aliases(
            valid_records,
            enabled=bool(request.alias_split_enabled),
            alias_count=int(request.alias_split_count or DEFAULT_ALIAS_COUNT),
            include_original=bool(request.alias_include_original),
        )

        oauth_records = [r for r in valid_records if r.account_type == ACCOUNT_TYPE_MICROSOFT_OAUTH]
        oauth_results: dict[int, dict[str, Any]] = {}
        if oauth_records:
            mailbox = MicrosoftMailbox()
            workers = _resolve_oauth_check_workers(len(oauth_records))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="oauth-import") as pool:
                future_map = {
                    pool.submit(_evaluate_record_availability, record, mailbox): record
                    for record in oauth_records
                }
                for fut in as_completed(future_map):
                    record = future_map[fut]
                    try:
                        oauth_results[record.line_number] = fut.result()
                    except Exception as exc:
                        oauth_results[record.line_number] = {
                            "ok": False,
                            "message": f"行 {record.line_number}: OAuth 检测异常: {exc}",
                            "reason": "oauth_probe_exception",
                        }

        passed: list[MicrosoftMailImportRecord] = []
        for record in valid_records:
            if record.account_type != ACCOUNT_TYPE_MICROSOFT_OAUTH:
                passed.append(record)
                continue
            verdict = oauth_results.get(record.line_number) or {
                "ok": False,
                "message": f"行 {record.line_number}: OAuth 检测未返回结果",
                "reason": "oauth_probe_missing_result",
            }
            if verdict.get("ok"):
                passed.append(record)
            else:
                failed += 1
                errors.append(str(verdict.get("message")))

        with session_scope() as s:
            for record in passed:
                try:
                    account = EmailAccount(
                        provider=PROVIDER_NAME,
                        email=record.email,
                        password=record.password,
                        refresh_token=record.refresh_token,
                        api_base="",
                        enabled=bool(request.enabled),
                        metadata_json=json_dumps({
                            "client_id": record.client_id,
                            "account_type": record.account_type,
                            "mailapi_url": record.mailapi_url,
                        }),
                    )
                    s.add(account)
                    s.commit()
                    s.refresh(account)
                    existing_emails.add(record.email)
                    accounts_meta.append({
                        "id": account.id,
                        "email": account.email,
                        "account_type": record.account_type,
                        "has_oauth": bool(record.refresh_token),
                    })
                    success += 1
                except Exception as exc:
                    s.rollback()
                    failed += 1
                    errors.append(f"行 {record.line_number}: 创建失败: {exc}")

        snapshot = self.get_snapshot(MailImportSnapshotRequest(
            type=PROVIDER_NAME,
            preview_limit=request.preview_limit,
        ))
        return MailImportResponse(
            type=PROVIDER_NAME,
            summary=MailImportSummary(
                total=success + failed,
                success=success,
                failed=failed,
            ),
            snapshot=snapshot,
            errors=errors,
            meta={
                "accounts": accounts_meta,
                "alias_split_enabled": bool(request.alias_split_enabled),
                "alias_split_count": int(request.alias_split_count or DEFAULT_ALIAS_COUNT),
                "alias_include_original": bool(request.alias_include_original),
            },
        )

    # ---- delete ----------------------------------------------------------------

    def delete(self, request: MailImportDeleteRequest) -> MailImportResponse:
        email = str(request.email or "").strip()
        if not email:
            raise RuntimeError("缺少要删除的邮箱地址")
        deleted = self._delete_emails([email])
        if not deleted:
            raise RuntimeError(f"未找到要删除的微软邮箱: {email}")
        snapshot = self.get_snapshot(MailImportSnapshotRequest(
            type=PROVIDER_NAME, preview_limit=request.preview_limit,
        ))
        return MailImportResponse(
            type=PROVIDER_NAME,
            summary=MailImportSummary(total=1, success=1, failed=0),
            snapshot=snapshot,
            meta={"deleted_emails": deleted},
        )

    def batch_delete(self, request: MailImportBatchDeleteRequest) -> MailImportResponse:
        targets = [str(item.email or "").strip() for item in request.items if str(item.email or "").strip()]
        deleted = self._delete_emails(targets)
        errors = [f"未找到要删除的微软邮箱: {email}" for email in targets if email not in deleted]
        snapshot = self.get_snapshot(MailImportSnapshotRequest(
            type=PROVIDER_NAME, preview_limit=request.preview_limit,
        ))
        return MailImportResponse(
            type=PROVIDER_NAME,
            summary=MailImportSummary(
                total=len(targets),
                success=len(deleted),
                failed=len(errors),
            ),
            snapshot=snapshot,
            errors=errors,
            meta={"deleted_emails": deleted},
        )

    def _delete_emails(self, emails: Iterable[str]) -> list[str]:
        wanted = [str(e or "").strip() for e in emails if str(e or "").strip()]
        if not wanted:
            return []
        deleted: list[str] = []
        with session_scope() as s:
            for email in wanted:
                row = s.exec(
                    select(EmailAccount)
                    .where(EmailAccount.provider == PROVIDER_NAME)
                    .where(EmailAccount.email == email)
                ).first()
                if row is None:
                    continue
                s.delete(row)
                deleted.append(email)
        return deleted


# ---- module helpers ----------------------------------------------------------


def _account_type(account: EmailAccount):
    if account.refresh_token:
        return ACCOUNT_TYPE_MICROSOFT_OAUTH
    return ACCOUNT_TYPE_MAILAPI_URL


def _resolve_oauth_check_workers(total: int) -> int:
    raw = str(os.getenv("MAIL_IMPORT_OAUTH_WORKERS", "8")).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = 8
    configured = max(1, min(configured, 32))
    return max(1, min(configured, max(total, 1)))


def _evaluate_record_availability(record: MicrosoftMailImportRecord, mailbox: MicrosoftMailbox) -> dict[str, Any]:
    if record.account_type != ACCOUNT_TYPE_MICROSOFT_OAUTH:
        return {"ok": True}
    return mailbox.probe_oauth_availability(
        email=record.email,
        client_id=record.client_id,
        refresh_token=record.refresh_token,
    )


registry = {PROVIDER_NAME: MicrosoftMailImportStrategy()}
