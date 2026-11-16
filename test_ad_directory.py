import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ad_directory import (
    DEFAULT_OU,
    DEFAULT_SERVER,
    DEFAULT_SERVICE_ACCOUNT,
    SCRIPT_PATH,
    ActiveDirectoryDirectory,
    run_powershell,
)
from main import DEFAULT_AUDIT_LOG, TOOLS, AuditedDirectory, DemoDirectory, build_directory


OU = "OU=AgenticAI-AD-OPs-test,DC=ninety7,DC=local"


def account(username, name, department, locked, distinguished_name):
    return {
        "username": username,
        "name": name,
        "department": department,
        "enabled": True,
        "locked": locked,
        "distinguished_name": distinguished_name,
    }


JUAN_MARKETING = account(
    "juan.marketing", "Juan Reyes", "Marketing", True, "CN=Juan Reyes," + OU,
)
JUAN_FINANCE = account(
    "juan.finance", "Juan Alvarez", "Finance", False, "CN=Juan Alvarez," + OU,
)
SAM = account(
    "sam.ops", "Sam Patel", "Operations", True, "CN=Sam Patel,OU=Other,DC=ninety7,DC=local",
)


def completed(payload, returncode=0, stderr=""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr=stderr,
    )


class ActiveDirectoryDirectoryTests(unittest.TestCase):
    def test_search_filters_in_python_and_stays_inside_the_test_ou(self):
        calls = []

        def runner(powershell, script_path, ou_name, server, service_account, request_json):
            calls.append(request_json)
            self.assertEqual(ou_name, DEFAULT_OU)
            self.assertEqual(server, DEFAULT_SERVER)
            self.assertEqual(service_account, DEFAULT_SERVICE_ACCOUNT)
            return completed({
                "ok": True,
                "ou": OU,
                "users": [JUAN_FINANCE, SAM, JUAN_MARKETING],
            })

        directory = ActiveDirectoryDirectory(runner=runner)
        marketing = directory.find_users("Juan", "Marketing")
        both = directory.find_users("juan", "")

        self.assertEqual([json.loads(call) for call in calls], [{"op": "search"}, {"op": "search"}])
        self.assertEqual([user["username"] for user in marketing["users"]], ["juan.marketing"])
        self.assertTrue(marketing["users"][0]["locked"])
        self.assertEqual(
            [user["username"] for user in both["users"]],
            ["juan.finance", "juan.marketing"],
        )
        self.assertNotIn("sam.ops", json.dumps(both))

    def test_one_powershell_user_object_is_accepted(self):
        directory = ActiveDirectoryDirectory(
            runner=lambda *args: completed({"ok": True, "ou": OU, "users": JUAN_MARKETING})
        )
        found = directory.find_users("Juan", "")
        self.assertEqual([user["username"] for user in found["users"]], ["juan.marketing"])

    def test_status_hides_accounts_outside_the_test_ou(self):
        directory = ActiveDirectoryDirectory(
            runner=lambda *args: completed({"ok": True, "ou": OU, "users": [SAM]})
        )
        result = directory.get_account_status("sam.ops")
        self.assertEqual(result["reason"], "outside_test_ou")
        self.assertIsNone(result["user"])
        self.assertNotIn("Sam Patel", json.dumps(result))

    def test_status_returns_the_requested_account(self):
        directory = ActiveDirectoryDirectory(
            runner=lambda *args: completed({"ok": True, "ou": OU, "users": [JUAN_MARKETING]})
        )
        result = directory.get_account_status("Juan.Marketing")
        self.assertEqual(result["user"]["username"], "juan.marketing")
        self.assertTrue(result["user"]["locked"])

    def test_directory_errors_stay_in_the_result(self):
        cases = [
            ({"ok": False, "error": "ou_not_found"}, "ou_not_found"),
            ({"ok": False, "error": "ou_ambiguous"}, "ou_ambiguous"),
            ({"ok": False, "error": "user_not_found"}, "not_found"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                directory = ActiveDirectoryDirectory(runner=lambda *args: completed(payload))
                if expected == "not_found":
                    result = directory.get_account_status("missing")
                    self.assertEqual(result["status"], "not_found")
                    self.assertIsNone(result["user"])
                else:
                    result = directory.find_users("Juan", "")
                    self.assertEqual(result["reason"], expected)

    def test_invalid_powershell_output_is_a_failed_result(self):
        directory = ActiveDirectoryDirectory(
            runner=lambda *args: SimpleNamespace(returncode=1, stdout="nope", stderr="dc unavailable")
        )
        result = directory.find_users("Juan", "")
        self.assertEqual(result["reason"], "invalid_response")
        self.assertIn("dc unavailable", result["message"])

    def test_missing_powershell_is_a_failed_result(self):
        def runner(*args):
            raise FileNotFoundError("powershell.exe")

        directory = ActiveDirectoryDirectory(runner=runner)
        result = directory.find_users("Juan", "")
        self.assertEqual(result["reason"], "powershell_unavailable")

    def test_domain_qualified_service_account_uses_the_sam_name(self):
        directory = ActiveDirectoryDirectory(
            service_account="NINETY7\\svc-adpilot",
            runner=lambda *args: completed({
                "ok": False,
                "error": "wrong_identity",
                "message": "NINETY7\\labuser",
            }),
        )
        self.assertEqual(directory.service_account, "svc-adpilot")
        result = directory.find_users("Juan", "")
        self.assertEqual(result["reason"], "wrong_identity")
        self.assertIn("labuser", result["message"])

    def test_unlock_does_not_call_powershell_or_confirmation(self):
        def runner(*args):
            raise AssertionError("powershell should not run")

        confirm = Mock(side_effect=AssertionError("confirmation should not run"))
        directory = ActiveDirectoryDirectory(runner=runner)
        result = directory.unlock_account("juan.marketing", confirm=confirm)
        self.assertEqual(result["reason"], "unlock_not_enabled")
        confirm.assert_not_called()

    def test_script_is_read_only(self):
        with open(SCRIPT_PATH, encoding="utf-8") as handle:
            script = handle.read().casefold()
        self.assertIn("get-aduser", script)
        self.assertIn("-server $server", script)
        self.assertIn("windowsidentity", script)
        self.assertIn("wrong_identity", script)
        self.assertNotIn("svc-adpilot", script)
        self.assertNotIn("password", script)
        for forbidden in ("unlock-adaccount", "set-adaccountpassword", "remove-aduser",
                          "invoke-expression"):
            self.assertNotIn(forbidden, script)

    def test_runner_passes_config_as_arguments_and_request_as_data(self):
        request = {"op": "status", "username": "juan'; Unlock-ADAccount"}
        with patch("ad_directory.subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "ou": OU, "users": []}).encode("utf-16"),
                stderr=b"",
            )
            completed_process = run_powershell(
                "powershell.exe", SCRIPT_PATH, DEFAULT_OU, DEFAULT_SERVER,
                DEFAULT_SERVICE_ACCOUNT, json.dumps(request),
            )
        argv = run.call_args.args[0]
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(argv[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-File"])
        self.assertEqual(argv[4], SCRIPT_PATH)
        self.assertEqual(argv[argv.index("-TestOuName") + 1], DEFAULT_OU)
        self.assertEqual(argv[argv.index("-Server") + 1], DEFAULT_SERVER)
        self.assertEqual(argv[argv.index("-ServiceAccount") + 1], DEFAULT_SERVICE_ACCOUNT)
        self.assertNotIn("Unlock-ADAccount", argv)
        encoded = argv[argv.index("-RequestBase64") + 1]
        self.assertEqual(json.loads(base64.b64decode(encoded)), request)
        self.assertEqual(json.loads(completed_process.stdout)["ok"], True)


class DirectorySelectionTests(unittest.TestCase):
    def test_demo_remains_the_default(self):
        selected = build_directory({})
        self.assertIsInstance(selected.directory, DemoDirectory)
        self.assertIs(selected.tools, TOOLS)
        self.assertIn("Fictional directory only", selected.banner)
        self.assertEqual(selected.log_path, DEFAULT_AUDIT_LOG)

    def test_ad_backend_targets_lab01_and_the_test_ou(self):
        selected = build_directory({"ADPILOT_DIRECTORY": "ad"})
        self.assertIsInstance(selected.directory, ActiveDirectoryDirectory)
        self.assertEqual(selected.directory.ou_name, "AgenticAI-AD-OPs-test")
        self.assertEqual(selected.directory.server, "LAB01.ninety7.local")
        self.assertEqual(selected.directory.service_account, "svc-adpilot")
        self.assertIn("LAB02", selected.banner)
        self.assertIn("svc-adpilot", selected.banner)
        self.assertIn("svc-adpilot", selected.instructions)
        self.assertIn("AgenticAI-AD-OPs-test", selected.instructions)
        self.assertIn("LAB01.ninety7.local", selected.instructions)
        self.assertIn("disabled", selected.tools[2]["description"])
        self.assertEqual(
            [tool["name"] for tool in selected.tools],
            ["find_users", "get_account_status", "unlock_account"],
        )

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            build_directory({"ADPILOT_DIRECTORY": "production"})

    def test_audit_log_records_the_call_and_result(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = os.path.join(temp, "audit.log")
            audited = AuditedDirectory(DemoDirectory(), log_path, "banner", "instructions", TOOLS)
            audited.find_users("Juan", "Marketing")
            with open(log_path, encoding="utf-8") as handle:
                entry = json.loads(handle.readline())
        self.assertEqual(entry["tool"], "find_users")
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["arguments"], {"name": "Juan", "department": "Marketing"})
        self.assertEqual(entry["result"]["users"][0]["username"], "juan.marketing")

    def test_audit_log_records_a_failed_call(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = os.path.join(temp, "audit.log")
            inner = Mock()
            inner.get_account_status.side_effect = RuntimeError("dc down")
            audited = AuditedDirectory(inner, log_path, "banner", "instructions", TOOLS)
            with self.assertRaisesRegex(RuntimeError, "dc down"):
                audited.get_account_status("juan.marketing")
            with open(log_path, encoding="utf-8") as handle:
                entry = json.loads(handle.readline())
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["error"], "dc down")


if __name__ == "__main__":
    unittest.main()
