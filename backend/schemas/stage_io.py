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


STAGE_INPUT_SCHEMAS = {
    "register": RegisterInput,
    "payment_link": PaymentLinkInput,
    "payment": PaymentInput,
    "chatgpt_session": ChatGPTSessionInput,
    "openai_oauth": OpenAIOAuthInput,
    "sub2api_sync": Sub2ApiSyncInput,
}

STAGE_OUTPUT_SCHEMAS = {
    "register": RegisterOutput,
    "payment_link": PaymentLinkOutput,
    "payment": PaymentOutput,
    "chatgpt_session": ChatGPTSessionOutput,
    "openai_oauth": OpenAIOAuthOutput,
    "sub2api_sync": Sub2ApiSyncOutput,
}
