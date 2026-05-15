"""smstome_tool stub.

The legacy ChatGPT module imports `smstome_tool` for the optional
add-phone OTP path.  The queue project doesn't use phone OTP, so we provide
a no-op stub that preserves the import surface but raises if anyone actually
tries to use it.

Installed via `sys.modules['smstome_tool'] = backend.compat.smstome_tool` from
`backend.main`.
"""
from __future__ import annotations

from typing import Any


class PhoneEntry:  # noqa: D401 - mimic legacy class shape
    """Placeholder; never instantiated in this project."""


def _disabled(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("smstome_tool is not available in chatgpt-queue-reg")


def get_unused_phone(*args: Any, **kwargs: Any) -> Any:
    return None


def mark_phone_blacklisted(*args: Any, **kwargs: Any) -> None:
    return None


def parse_country_slugs(value: Any) -> list[str]:
    return []


def update_global_phone_list(*args: Any, **kwargs: Any) -> int:
    return 0


def wait_for_otp(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("phone OTP not supported in chatgpt-queue-reg")
