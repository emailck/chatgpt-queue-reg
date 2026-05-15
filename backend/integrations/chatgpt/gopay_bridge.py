"""GoPay bridge stub.

The legacy ChatGPT registration engine imports `gopay_bridge` to optionally
run the GoPay (IDR Plus) automatic-payment side chain after signup.  In the
queue project we don't ship that side chain, so this stub keeps `should_run_gopay`
returning False and provides no-op signatures the engine references on import.

The engine guards each call with `should_run_gopay(extra_config)`, so as
long as nothing flips that flag true, none of these functions execute.
"""
from __future__ import annotations

from typing import Any


def should_run_gopay(_extra_config: dict[str, Any] | None = None) -> bool:
    return False


def is_gopay_sms_otp_timeout(_value: Any) -> bool:
    return False


def run_gopay_with_chatgpt_session(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("gopay_bridge.run_gopay_with_chatgpt_session is not available in chatgpt-queue-reg")


def run_gopay_with_oauth_session(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("gopay_bridge.run_gopay_with_oauth_session is not available in chatgpt-queue-reg")


def release_gopay_phone_reservation(_source_id: str) -> None:
    return None
