"""Per-stage input/output contracts.

These schemas document the payload boundary for each stage. Runtime callers may
still pass plain dicts through the queue; stage code can opt into validation as
its implementation matures.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class StageInput(BaseModel):
    account_id: Optional[int] = None
    payment_link_id: Optional[int] = None
    email_address: str = ""
    proxy_id: Optional[int] = None
    proxy_url: str = ""


class StageOutput(BaseModel):
    account_id: Optional[int] = None
    payment_link_id: Optional[int] = None
    email_address: str = ""
    proxy_id: Optional[int] = None
    proxy_url: str = ""


class RegisterInput(StageInput):
    registration_mode: str = "access_token_only"
    email: str = ""
    password: Optional[str] = None
    proxy_region: str = ""
    region: str = ""
    also_record_to_at_pool: bool = False
    extra_config: dict[str, Any] = Field(default_factory=dict)


class RegisterOutput(StageOutput):
    account_id: int
    email: str = ""
    registered_account_id: str = ""
    workspace_id: str = ""
    source: str = ""
    access_token_account_id: Optional[int] = None


class PaymentLinkInput(StageInput):
    account_id: int
    plan: str = "team"
    workspace_name: str = "MyWorkspace"
    price_interval: str = "month"
    seat_quantity: int = Field(default=2, ge=1)
    country: str = "US"
    currency: Optional[str] = None
    promo_code: str = ""
    extra_config: dict[str, Any] = Field(default_factory=dict)


class PaymentLinkOutput(StageOutput):
    account_id: int
    payment_link_id: int
    checkout_url: str = ""
    checkout_session_id: str = ""
    cs_id: str = ""
    plan: str = ""


class PaymentInput(StageInput):
    account_id: Optional[int] = None
    payment_link_id: int
    payment_proxy_region: str = ""
    proxy_region: str = ""
    region: str = ""
    card_project: str = "default"
    sms_project: str = "stripe_payment"
    extra_config: dict[str, Any] = Field(default_factory=dict)


class PaymentOutput(StageOutput):
    payment_link_id: int
    state: str = ""
    payment_proxy_id: Optional[int] = None
    payment_proxy_region: str = ""


class ChatGPTSessionInput(StageInput):
    account_id: int
    mode: str = "session"
    force_refresh: bool = False
    sync_sub2api_after_refresh: bool = False
    extra_config: dict[str, Any] = Field(default_factory=dict)


class ChatGPTSessionOutput(StageOutput):
    account_id: int
    chatgpt_account_id: str = ""
    chatgpt_user_id: str = ""
    access_token: str = ""
    id_token: str = ""
    session_token: str = ""
    session_expires_at: str = ""
    session_refresh_status: str = ""
    plan_type: str = ""


class Sub2ApiSyncInput(StageInput):
    account_id: Optional[int] = None
    refresh_token_id: Optional[int] = None
    sub2api_account_id: str = ""
    force_upload: bool = False
    reset_remote_status: bool = False
    mode: str = "auto"
    extra_config: dict[str, Any] = Field(default_factory=dict)


class Sub2ApiSyncOutput(StageOutput):
    account_id: int
    refresh_token_id: Optional[int] = None
    sub2api_account_id: str = ""
    sub2api_status: str = ""
    auth_mode: str = ""
    schedulable: bool = True
    relogin_required: bool = False


class OpenAIOAuthInput(StageInput):
    account_id: int
    sms_project: str = "openai_oauth"
    extra_config: dict[str, Any] = Field(default_factory=dict)


class OpenAIOAuthOutput(StageOutput):
    account_id: int
    refresh_token_id: Optional[int] = None
    has_refresh_token: bool = False
    expires_in: int = 0
    sub2api_status: str = ""


class CodexInvitationInput(StageInput):
    email_id: Optional[int] = None
    email: str = ""
    inviter_account_id: Optional[int] = None
    inviter_email: str = ""
    source_id: Optional[int] = None
    source_type: str = "auto"
    invite_count: int = Field(default=1, ge=1, le=200)
    prefix_len: int = Field(default=20, ge=3, le=64)
    domain: str = ""
    emails: list[str] = Field(default_factory=list)
    access_token: str = ""
    chatgpt_account_id: str = ""
    codex_account_id: str = ""
    dry_run: bool = False
    check_eligibility: bool = True
    insecure: bool = False
    extra_config: dict[str, Any] = Field(default_factory=dict)


class CodexInvitationOutput(StageOutput):
    source_type: str = ""
    source_id: int = 0
    source_email: str = ""
    domain: str = ""
    emails: list[str] = Field(default_factory=list)
    invited_email: str = ""
    sso_email: str = ""
    invite_count: int = 0
    remaining_invites: Optional[int] = None
    sent: bool = False
    status_code: int = 0


class CodexBatchInviteInput(StageInput):
    inviter_list: str = ""
    inviter_emails: str = ""
    inviter_account_ids: str = ""
    source_type: str = "auto"
    invite_count_per_inviter: int = Field(default=5, ge=1, le=5)
    invite_concurrency: int = Field(default=5, ge=1, le=50)
    prefix_len: int = Field(default=20, ge=3, le=64)
    dry_run: bool = False
    activate_after_invite: bool = True
    check_eligibility: bool = True
    extra_config: dict[str, Any] = Field(default_factory=dict)


class CodexBatchInviteOutput(StageOutput):
    inviter_count: int = 0
    invite_count_per_inviter: int = 0
    invited_count: int = 0
    invited_emails: list[str] = Field(default_factory=list)
    activation_pipeline_ids: list[int] = Field(default_factory=list)
    activation_started: bool = False
    failed_count: int = 0


class ActiveInput(StageInput):
    email: str = ""
    sso_email: str = ""
    refresh_token_id: Optional[int] = None
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    chatgpt_account_id: str = ""
    dry_run: bool = False
    refresh_before_activation: bool = True
    insecure: bool = False
    extra_config: dict[str, Any] = Field(default_factory=dict)


class ActiveOutput(StageOutput):
    email: str = ""
    sso_email: str = ""
    chatgpt_account_id: str = ""
    refresh_token_id: Optional[int] = None
    activated: bool = False
    success_count: int = 0
    total_count: int = 0


STAGE_INPUT_SCHEMAS = {
    "register": RegisterInput,
    "payment_link": PaymentLinkInput,
    "payment": PaymentInput,
    "chatgpt_session": ChatGPTSessionInput,
    "openai_oauth": OpenAIOAuthInput,
    "sso_oauth": OpenAIOAuthInput,
    "codex_invitation": CodexInvitationInput,
    "codex_batch_invite": CodexBatchInviteInput,
    "active": ActiveInput,
    "sub2api_sync": Sub2ApiSyncInput,
}

STAGE_OUTPUT_SCHEMAS = {
    "register": RegisterOutput,
    "payment_link": PaymentLinkOutput,
    "payment": PaymentOutput,
    "chatgpt_session": ChatGPTSessionOutput,
    "openai_oauth": OpenAIOAuthOutput,
    "sso_oauth": OpenAIOAuthOutput,
    "codex_invitation": CodexInvitationOutput,
    "codex_batch_invite": CodexBatchInviteOutput,
    "active": ActiveOutput,
    "sub2api_sync": Sub2ApiSyncOutput,
}
