"""Read-only Active Directory backend for the LAB02 app."""

import base64
import json
import os
import subprocess
from types import SimpleNamespace

DEFAULT_OU = "AgenticAI-AD-OPs-test"
DEFAULT_SERVER = "LAB01.ninety7.local"
DEFAULT_SERVICE_ACCOUNT = "svc-adpilot"
SOURCE = "active_directory"
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ad.ps1")


def _setting(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{0} is required".format(label))
    if any(ord(char) < 32 or char in "'\"" for char in value):
        raise ValueError("{0} contains unsupported characters".format(label))
    return value.strip()


def _valid_username(username):
    return isinstance(username, str) and bool(username.strip()) and all(
        ord(char) >= 32 for char in username
    )


def in_test_ou(distinguished_name, ou_dn):
    name = (distinguished_name or "").casefold()
    base = (ou_dn or "").casefold()
    return bool(base) and (name == base or name.endswith("," + base))


def _decode(raw):
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff") or b"\x00" in raw[:200]:
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _account_name(value):
    account = _setting(value, "service account").split("\\")[-1]
    if not account:
        raise ValueError("service account is required")
    return account


def run_powershell(powershell, script_path, ou_name, server, service_account, request_json):
    """Run the fixed query script. The request travels as data, not as script text."""
    request_b64 = base64.b64encode(request_json.encode("utf-8")).decode("ascii")
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            script_path,
            "-TestOuName",
            ou_name,
            "-Server",
            server,
            "-ServiceAccount",
            service_account,
            "-RequestBase64",
            request_b64,
        ],
        capture_output=True,
        check=False,
    )
    return SimpleNamespace(
        returncode=completed.returncode,
        stdout=_decode(completed.stdout),
        stderr=_decode(completed.stderr),
    )


def _parse_payload(completed):
    text = (completed.stdout or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _failure(reason, message=""):
    result = {"source": SOURCE, "status": "failed", "reason": reason}
    if message:
        result["message"] = message[:500]
    return result


def _mapped_error(payload):
    reason = payload.get("error") or "powershell_failed"
    message = payload.get("message") or ""
    if reason == "user_not_found":
        return {"source": SOURCE, "user": None, "status": "not_found"}
    if reason == "outside_test_ou":
        return {
            "source": SOURCE,
            "user": None,
            "status": "denied",
            "reason": "outside_test_ou",
        }
    return _failure(str(reason), str(message) if message else "")


def _users(payload):
    users = payload.get("users") or []
    if isinstance(users, dict):
        return [users]
    if isinstance(users, list):
        return users
    return []


def _public_user(user):
    distinguished_name = user.get("distinguished_name") or ""
    username = user.get("username") or ""
    name = user.get("name") or ""
    if not distinguished_name or not username or not name:
        return None
    department = user.get("department") or ""
    return {
        "username": username,
        "name": name,
        "department": department,
        "enabled": bool(user.get("enabled")),
        "locked": bool(user.get("locked")),
        "test_ou": True,
        "distinguished_name": distinguished_name,
    }


def _matches(user, name, department):
    needle = name.casefold()
    haystack = "{name} {username}".format(**user).casefold()
    if needle not in haystack:
        return False
    department_needle = department.casefold()
    if department_needle and user["department"].casefold() != department_needle:
        return False
    return True


class ActiveDirectoryDirectory:
    """Queries and unlocks accounts only inside the configured test OU on LAB01."""

    def __init__(self, ou_name=DEFAULT_OU, server=DEFAULT_SERVER,
                 service_account=DEFAULT_SERVICE_ACCOUNT, powershell="powershell.exe",
                 script_path=SCRIPT_PATH, runner=None):
        self.ou_name = _setting(ou_name, "test OU")
        self.server = _setting(server, "directory server")
        self.service_account = _account_name(service_account)
        self.powershell = _setting(powershell, "powershell")
        self.script_path = script_path
        self.runner = runner or run_powershell

    def find_users(self, name, department):
        payload, error = self._request({"op": "search"})
        if error:
            return error
        return {
            "source": SOURCE,
            "ou": payload["ou"],
            "server": self.server,
            "users": self._filter(payload, name, department),
        }

    def get_account_status(self, username):
        if not _valid_username(username):
            return _failure("invalid_username")
        requested = username.strip()
        payload, error = self._request({"op": "status", "username": requested})
        if error:
            return error
        matches = [
            user for user in self._filter(payload, "", "")
            if user["username"].casefold() == requested.casefold()
        ]
        if matches:
            return {"source": SOURCE, "user": matches[0]}
        if any(isinstance(user, dict) for user in _users(payload)):
            return {
                "source": SOURCE,
                "user": None,
                "status": "denied",
                "reason": "outside_test_ou",
            }
        return {"source": SOURCE, "user": None, "status": "not_found"}

    def unlock_account(self, username, confirm):
        if not _valid_username(username):
            return _failure("invalid_username")
        requested = username.strip()
        current = self.get_account_status(requested)
        user = current.get("user") if isinstance(current, dict) else None
        if user is None:
            return current
        if not user.get("test_ou"):
            return {
                "source": SOURCE,
                "status": "denied",
                "reason": "outside_test_ou",
                "username": requested,
            }
        if not user.get("locked"):
            return {
                "source": SOURCE,
                "status": "unchanged",
                "reason": "not_locked",
                "user": user,
            }
        if not confirm(user):
            return {
                "source": SOURCE,
                "status": "denied",
                "reason": "confirmation_denied",
                "username": requested,
            }

        payload, error = self._request({"op": "unlock", "username": requested})
        if error:
            return error
        status = payload.get("status")
        matches = [
            item for item in self._filter(payload, "", "")
            if item["username"].casefold() == requested.casefold()
        ]
        if status not in {"unlocked", "unchanged"} or not matches:
            return _failure("unlock_verification_failed")
        updated = matches[0]
        if updated["locked"]:
            return _failure("unlock_verification_failed")
        return {"source": SOURCE, "status": status, "user": updated}

    def _filter(self, payload, name, department):
        ou_dn = payload.get("ou")
        if not isinstance(ou_dn, str) or not ou_dn.strip():
            return []
        selected = []
        for user in _users(payload):
            if not isinstance(user, dict):
                continue
            public = _public_user(user)
            if public is None or not in_test_ou(public["distinguished_name"], ou_dn):
                continue
            if name and not _matches(public, name, department):
                continue
            if not name and department and not _matches(public, "", department):
                continue
            selected.append(public)
        selected.sort(key=lambda user: user["username"])
        return selected

    def _request(self, request):
        try:
            completed = self.runner(
                self.powershell,
                self.script_path,
                self.ou_name,
                self.server,
                self.service_account,
                json.dumps(request, sort_keys=True),
            )
        except OSError as exc:
            return None, _failure("powershell_unavailable", str(exc))
        payload = _parse_payload(completed)
        if payload is None:
            message = (completed.stderr or "").strip()
            return None, _failure("invalid_response", message)
        if completed.returncode not in (0, None) or payload.get("ok") is not True:
            return None, _mapped_error(payload)
        if not isinstance(payload.get("ou"), str):
            return None, _failure("invalid_response")
        return payload, None
