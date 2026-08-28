from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import unquote

from backend.integrations.chatgpt.mfa_client import (
    build_totp_adapter_from_metadata,
    create_twofauth_adapter_from_uri,
    build_otpauth_url,
    extract_secret_from_otpauth,
    generate_totp_code,
)


class MfaClientTests(unittest.TestCase):
    def test_generate_totp_code_matches_rfc6238_vector(self) -> None:
        # RFC 6238 Appendix B, SHA1 / 30s / 6 digits.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(generate_totp_code(secret, for_time=59), "287082")

    def test_otpauth_roundtrip(self) -> None:
        url = build_otpauth_url("ABC123", email="user@example.com")
        self.assertIn("otpauth://totp/ChatGPT:user@example.com", unquote(url))
        self.assertEqual(extract_secret_from_otpauth(url), "ABC123")

    def test_build_totp_adapter_from_metadata(self) -> None:
        adapter = build_totp_adapter_from_metadata(
            {"mfa": {"secret": "ABC123", "factor_id": "factor-1"}},
            timeout_seconds=15,
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_secret(), "ABC123")
        self.assertEqual(adapter.get_factor_id(), "factor-1")

    def test_build_totp_adapter_from_metadata_twofauth(self) -> None:
        adapter = build_totp_adapter_from_metadata(
            {"mfa": {"provider": "twofauth", "twofauth_account_id": "42", "factor_id": "factor-9"}},
            config={
                "mfa_code_provider": "twofauth",
                "twofauth_base_url": "https://2fa.oai-gpt.com",
                "twofauth_pat": "pat-test",
            },
            timeout_seconds=15,
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_factor_id(), "factor-9")
        self.assertEqual(adapter.get_twofauth_account_id(), "42")

    def test_create_twofauth_adapter_from_uri_and_fetch_code(self) -> None:
        class FakeResponse:
            def __init__(self, status_code: int, body: dict[str, object]):
                self.status_code = status_code
                self._body = body
                self.text = "{}"

            def json(self):
                return self._body

        class FakeSession:
            def __init__(self):
                self.calls = []

            def request(self, method, url, headers=None, timeout=None, verify=None, **kwargs):
                self.calls.append((method, url, kwargs))
                if url.endswith("/twofaccounts/preview"):
                    return FakeResponse(200, {"service": "ChatGPT"})
                if url.endswith("/twofaccounts"):
                    return FakeResponse(201, {"id": "99", "service": "ChatGPT"})
                raise AssertionError(f"unexpected url {url}")

            def get(self, url, headers=None, timeout=None, verify=None):
                self.calls.append(("GET", url, None))
                return FakeResponse(200, {"password": "123456"})

        fake_session = FakeSession()

        with patch("backend.integrations.chatgpt.mfa_client.requests.Session", return_value=fake_session):
            adapter = create_twofauth_adapter_from_uri(
                "otpauth://totp/ChatGPT:user@example.com?secret=ABC123&issuer=ChatGPT",
                account_label="user@example.com",
                factor_id="factor-x",
                config={
                    "mfa_code_provider": "twofauth",
                    "twofauth_base_url": "https://2fa.oai-gpt.com",
                    "twofauth_pat": "pat-test",
                },
                timeout_seconds=15,
            )

        self.assertEqual(adapter.get_twofauth_account_id(), "99")
        self.assertEqual(adapter.get_code("user@example.com"), "123456")


if __name__ == "__main__":
    unittest.main()
