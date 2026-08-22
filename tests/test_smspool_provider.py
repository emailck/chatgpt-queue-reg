from __future__ import annotations

import unittest

from backend.integrations.chatgpt.phone_service import PhoneLease
from backend.integrations.chatgpt.smspool_provider import _reuse_lease_log_message


class SmsPoolProviderTests(unittest.TestCase):
    def test_reuse_lease_log_message_reads_metadata(self) -> None:
        lease = PhoneLease(
            provider="smspool",
            activation_id="E6MCJ84V",
            phone_number="+15822772499",
            metadata={"success_count": 1},
        )
        self.assertEqual(
            _reuse_lease_log_message(lease),
            "复用号码: +15822772499 orderid=E6MCJ84V success_count=1",
        )

    def test_reuse_lease_log_message_handles_missing_metadata(self) -> None:
        lease = PhoneLease(
            provider="smspool",
            activation_id="E6MCJ84V",
            phone_number="+15822772499",
        )
        self.assertEqual(
            _reuse_lease_log_message(lease),
            "复用号码: +15822772499 orderid=E6MCJ84V success_count=",
        )


if __name__ == "__main__":
    unittest.main()
