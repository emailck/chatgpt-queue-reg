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
    payment_proxy_region: str
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


class OAuthCodexInput(StageInput):
    account_id: int
    sms_project: str = "openai_oauth"
    extra_config: dict[str, Any] = Field(default_factory=dict)


class OAuthCodexOutput(StageOutput):
    account_id: int
    codex_token_id: Optional[int] = None
    codex_rt: str = ""
    codex_at: str = ""
    expires_in: int = 0
    sub2api_external_id: str = ""
    sub2api_status: str = ""


class RtKeepaliveInput(StageInput):
    account_id: Optional[int] = None
    codex_token_id: Optional[int] = None


class RtKeepaliveOutput(StageOutput):
    account_id: int
    codex_token_id: Optional[int] = None
    sub2api_external_id: str = ""
    sub2api_status: str = ""


STAGE_INPUT_SCHEMAS = {
    "register": RegisterInput,
    "payment_link": PaymentLinkInput,
    "payment": PaymentInput,
    "oauth_codex": OAuthCodexInput,
    "rt_keepalive": RtKeepaliveInput,
}

STAGE_OUTPUT_SCHEMAS = {
    "register": RegisterOutput,
    "payment_link": PaymentLinkOutput,
    "payment": PaymentOutput,
    "oauth_codex": OAuthCodexOutput,
    "rt_keepalive": RtKeepaliveOutput,
}
