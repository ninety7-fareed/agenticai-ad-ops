import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from main import INSTRUCTIONS, TOOLS, credential_from_settings, ensure_config, runtime_settings
from server import ChatSession, dispatch


class ConfigTests(unittest.TestCase):
    def test_config_file_is_created_once_with_lab_names(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "adpilot.json")
            ensure_config(path)
            with open(path, encoding="utf-8") as handle:
                saved = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"directory": "demo"}\n')
            ensure_config(path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), '{"directory": "demo"}\n')
        self.assertIn('"service_account": "svc-adpilot"', saved)
        self.assertNotIn("password", saved)

    def test_environment_replaces_one_saved_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "adpilot.json")
            ensure_config(path)
            settings = runtime_settings(path, environ={"ADPILOT_DC": "other.example"})
        self.assertEqual(settings["ADPILOT_DIRECTORY"], "ad")
        self.assertEqual(settings["ADPILOT_DC"], "other.example")
        self.assertEqual(settings["ADPILOT_SERVICE_ACCOUNT"], "svc-adpilot")
        self.assertEqual(settings["ADPILOT_TEST_OU"], "AgenticAI-AD-OPs-test")


class EntraCredentialTests(unittest.TestCase):
    def test_app_credentials_are_used_when_all_three_are_set(self):
        seen = {}

        def secret_credential(tenant, client_id, secret):
            seen["values"] = (tenant, client_id, secret)
            return "app"

        chosen = credential_from_settings(
            {
                "ADPILOT_AZURE_TENANT_ID": "tenant",
                "ADPILOT_AZURE_CLIENT_ID": "client",
                "ADPILOT_AZURE_CLIENT_SECRET": "secret",
            },
            secret_credential,
            lambda: "interactive",
        )
        self.assertEqual(chosen, "app")
        self.assertEqual(seen["values"], ("tenant", "client", "secret"))

    def test_missing_app_credentials_do_not_use_a_partial_secret(self):
        chosen = credential_from_settings({}, lambda *args: "app", lambda: "default")
        self.assertEqual(chosen, "default")
        with self.assertRaises(ValueError):
            credential_from_settings(
                {"ADPILOT_AZURE_CLIENT_ID": "client"},
                lambda *args: "app",
                lambda: "default",
            )


class ChatTests(unittest.TestCase):
    def test_page_has_a_message_form(self):
        status, page, content_type = dispatch("GET", "/", None, None)
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("<form>", page)
        self.assertIn("/message", page)

    def test_message_returns_the_model_answer(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output=[], output_text="Found Juan.")
        directory = SimpleNamespace(instructions=INSTRUCTIONS, tools=TOOLS)
        session = ChatSession(client, directory)

        status, body, content_type = dispatch(
            "POST", "/message", json.dumps({"text": "find Juan"}).encode("utf-8"), session,
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(body)["answer"], "Found Juan.")
        self.assertEqual(
            client.responses.create.call_args.kwargs["input"][0]["content"], "find Juan",
        )

    def test_model_error_stays_in_the_chat(self):
        class Broken:
            def reply(self, text):
                raise RuntimeError("content filter")

        status, body, _content_type = dispatch(
            "POST", "/message", b'{"text":"hello"}', Broken(),
        )
        self.assertEqual(status, 200)
        self.assertIn("content filter", json.loads(body)["answer"])

    def test_blank_message_is_rejected(self):
        status, body, _content_type = dispatch(
            "POST", "/message", b'{"text":" "}', Mock(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "text is required")


if __name__ == "__main__":
    unittest.main()
