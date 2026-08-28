from __future__ import annotations

import unittest
from importlib import util as importlib_util

from backend.integrations.chatgpt.utils import infer_page_type_from_url

if importlib_util.find_spec("curl_cffi") is not None:
    from backend.integrations.chatgpt.chatgpt_client import ChatGPTClient
else:
    ChatGPTClient = None  # type: ignore[assignment]


class PasswordSetupClientTests(unittest.TestCase):
    def test_infer_reset_password_page_type(self) -> None:
        self.assertEqual(
            infer_page_type_from_url("https://auth.openai.com/reset-password/new-password"),
            "reset_password_new_password",
        )

    @unittest.skipIf(ChatGPTClient is None, "curl_cffi not available in this test environment")
    def test_signin_password_setup_uses_password_reauth_params(self) -> None:
        client = ChatGPTClient(verbose=False)
        captured: dict[str, object] = {}

        def fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            captured["url"] = url
            captured["kwargs"] = kwargs

            class _Resp:
                status_code = 200

                @staticmethod
                def json():
                    return {"url": "https://auth.openai.com/api/accounts/authorize?code=1"}

            return _Resp()

        client._browser_pause = lambda *args, **kwargs: None  # type: ignore[method-assign]
        client._session_post = fake_post  # type: ignore[method-assign]

        authorize_url = client.signin_password_setup("user@example.com", "csrf-token")
        self.assertTrue(authorize_url)

        kwargs = captured["kwargs"]
        self.assertEqual(captured["url"], "https://chatgpt.com/api/auth/signin/openai")
        self.assertEqual(kwargs["params"]["connection"], "password")
        self.assertEqual(kwargs["params"]["reauth"], "password")
        self.assertEqual(kwargs["params"]["post_login_add_password"], "true")
        self.assertEqual(kwargs["params"]["max_age"], "0")
        self.assertEqual(kwargs["params"]["login_hint"], "user@example.com")
        self.assertEqual(kwargs["data"]["csrfToken"], "csrf-token")

    @unittest.skipIf(ChatGPTClient is None, "curl_cffi not available in this test environment")
    def test_add_password_posts_expected_payload(self) -> None:
        client = ChatGPTClient(verbose=False)
        captured: dict[str, object] = {}

        def fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            captured["url"] = url
            captured["kwargs"] = kwargs

            class _Resp:
                status_code = 200

                @staticmethod
                def json():
                    return {}

                text = ""

            return _Resp()

        client._browser_pause = lambda *args, **kwargs: None  # type: ignore[method-assign]
        client._session_post = fake_post  # type: ignore[method-assign]
        client._protocol_sentinel_token = lambda flow: f"sentinel:{flow}"  # type: ignore[method-assign]

        state = client._state_from_url("https://auth.openai.com/reset-password/new-password")
        ok, next_state = client.add_password("Secret123!", state, return_state=True)

        self.assertTrue(ok)
        self.assertEqual(captured["url"], "https://auth.openai.com/api/accounts/password/add")
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["json"], {"password": "Secret123!"})
        self.assertEqual(kwargs["headers"]["openai-sentinel-token"], "sentinel:password_reset")
        self.assertIn("referer", kwargs["headers"])
        self.assertTrue(getattr(next_state, "current_url", "").startswith("https://auth.openai.com"))


if __name__ == "__main__":
    unittest.main()
