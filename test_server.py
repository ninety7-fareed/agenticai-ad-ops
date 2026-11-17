import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from main import (
    INSTRUCTIONS, TOOLS, AuditedDirectory, DemoDirectory, credential_from_settings,
    ensure_config, runtime_settings,
)
from server import ChatSession, dispatch


def unlock_call():
    return SimpleNamespace(
        type="function_call",
        name="unlock_account",
        arguments='{"username":"juan.marketing"}',
        call_id="unlock_1",
    )


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
        self.assertIn("/confirm", page)
        self.assertIn("window.confirm", page)

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

    def test_unlock_requires_browser_confirmation_and_is_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            audit_path = os.path.join(temp, "audit.log")
            directory = AuditedDirectory(
                DemoDirectory(), audit_path, "banner", INSTRUCTIONS, TOOLS,
            )
            client = Mock()
            client.responses.create.return_value = SimpleNamespace(
                output=[unlock_call()], output_text="",
            )
            session = ChatSession(client, directory)

            status, body, _content_type = dispatch(
                "POST", "/message", b'{"text":"Unlock Juan Reyes"}', session,
            )
            pending = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(pending["confirmation_required"])
            self.assertEqual(pending["username"], "juan.marketing")
            self.assertTrue(directory.get_account_status("juan.marketing")["user"]["locked"])

            status, body, _content_type = dispatch(
                "POST",
                "/confirm",
                json.dumps({"token": pending["token"], "confirmed": True}).encode("utf-8"),
                session,
            )
            self.assertEqual(status, 200)
            self.assertIn("verified", json.loads(body)["answer"])
            self.assertFalse(directory.get_account_status("juan.marketing")["user"]["locked"])

            with open(audit_path, encoding="utf-8") as handle:
                entries = [json.loads(line) for line in handle]
            self.assertEqual(entries[0]["status"], "confirmation_required")
            self.assertTrue(any(
                entry.get("result", {}).get("status") == "unlocked" for entry in entries
            ))

    def test_cancelling_unlock_consumes_confirmation_without_changing_account(self):
        directory = DemoDirectory()
        directory.instructions = INSTRUCTIONS
        directory.tools = TOOLS
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(
            output=[unlock_call()], output_text="",
        )
        session = ChatSession(client, directory)
        pending = session.reply("Unlock Juan Reyes")

        answer = session.confirm_unlock(pending["token"], False)
        self.assertIn("cancelled", answer)
        self.assertTrue(directory.get_account_status("juan.marketing")["user"]["locked"])
        self.assertIn("expired or is invalid", session.confirm_unlock(pending["token"], True))

    def test_confirm_rejects_malformed_requests(self):
        status, body, _content_type = dispatch("POST", "/confirm", b'{"token":"x"}', Mock())
        self.assertEqual(status, 400)
        self.assertIn("required", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
