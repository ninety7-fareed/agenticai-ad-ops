"""ADPilot chat. The default directory is fictional; Active Directory is optional."""

import copy
import json
import os
from datetime import datetime, timezone

ENDPOINT = "https://agenticai-ad-ops-resource.services.ai.azure.com/openai/v1"
DEPLOYMENT_NAME = "agenticai-ad-ops-model"

INSTRUCTIONS = (
    "You are ADPilot, a lab assistant for a fictional directory, not live Active Directory. "
    "Keep replies concise, usually one to three short sentences. Use plain text; avoid headings, "
    "bullets, decorative punctuation, and Markdown emphasis such as asterisks unless needed "
    "for clarity. Add detail when the user asks for it. "
    "Use find_users to search by name and department. Pass an empty department when the user "
    "does not name one. If more than one person matches, ask which account is meant. "
    "Use get_account_status before requesting a change. "
    "Ask the user to confirm before you call unlock_account. "
    "Python asks for a separate yes or no before it changes an account. "
    "After a tool reports an unlock, call get_account_status and base your reply on that result. "
    "If a tool returns denied, not_found, or unchanged, say so and do not claim the account was unlocked."
)

AD_INSTRUCTIONS = (
    "You are ADPilot, running on member server LAB02 as {account}. "
    "Keep replies concise, usually one to three short sentences. Use plain text; avoid headings, "
    "bullets, decorative punctuation, and Markdown emphasis such as asterisks unless needed "
    "for clarity. Add detail when the user asks for it. "
    "Read Active Directory on {server}. Search only the test OU {ou}. "
    "Use find_users to search by name and department. Pass an empty department when the user "
    "does not name one. If more than one person matches, ask which account is meant. "
    "Use get_account_status to report whether that account is enabled and locked. "
    "You may request unlock_account only for a locked account in the configured test OU, "
    "after the user identifies the account. The application requires a separate browser "
    "confirmation before changing the directory. "
    "If a tool returns denied, not_found, failed, or unchanged, say so and do not claim a change."
)

AD_TOOL_DESCRIPTIONS = (
    "Find accounts in the configured test OU whose name or username contains the given name. "
    "Pass an empty department to search every department. Read-only.",
    "Return the enabled and locked state for one account in the configured test OU. Read-only.",
    "Request unlocking one locked account in the configured test OU. "
    "The application requires a separate browser confirmation before it changes Active Directory.",
)

TOOLS = [
    {
        "type": "function",
        "name": "find_users",
        "description": (
            "Find fictional demo users whose name or username contains the given name. "
            "Pass an empty department to search every department. Not live Active Directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "department": {"type": "string"},
            },
            "required": ["name", "department"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_account_status",
        "description": "Return the enabled and locked state for one fictional demo account.",
        "parameters": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "unlock_account",
        "description": (
            "Unlock one locked fictional account in the test OU. "
            "Call this only after the user has agreed. "
            "Python still asks for yes or no before changing the account."
        ),
        "parameters": {
            "type": "object",
            "properties": {"username": {"type": "string"}},
            "required": ["username"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

DEFAULT_USERS = [
    {
        "username": "juan.marketing",
        "name": "Juan Reyes",
        "department": "Marketing",
        "enabled": True,
        "locked": True,
        "test_ou": True,
    },
    {
        "username": "juan.finance",
        "name": "Juan Alvarez",
        "department": "Finance",
        "enabled": True,
        "locked": False,
        "test_ou": True,
    },
    {
        "username": "sam.ops",
        "name": "Sam Patel",
        "department": "Operations",
        "enabled": True,
        "locked": True,
        "test_ou": False,
    },
]

TOOL_ARGUMENTS = {
    "find_users": {"name", "department"},
    "get_account_status": {"username"},
    "unlock_account": {"username"},
}

PUBLIC_FIELDS = ("username", "name", "department", "enabled", "locked", "test_ou")


class DemoDirectory:
    """Fictional accounts for the unlock conversation. Changes stay in this object."""

    def __init__(self, users=None):
        source = DEFAULT_USERS if users is None else users
        self.users = {user["username"].casefold(): dict(user) for user in source}

    def find_users(self, name, department):
        needle = name.casefold()
        department_needle = department.casefold()
        matches = []
        for user in self.users.values():
            haystack = "{name} {username}".format(**user).casefold()
            if needle not in haystack:
                continue
            if department_needle and user["department"].casefold() != department_needle:
                continue
            matches.append(_public(user))
        matches.sort(key=lambda user: user["username"])
        return {"source": "local_demo", "users": matches}

    def get_account_status(self, username):
        user = self._user(username)
        return {"source": "local_demo", "user": None if user is None else _public(user)}

    def unlock_account(self, username, confirm):
        user = self._user(username)
        if user is None:
            return {"source": "local_demo", "status": "not_found", "username": username}
        if not user["test_ou"]:
            return {
                "source": "local_demo",
                "status": "denied",
                "reason": "outside_test_ou",
                "username": user["username"],
            }
        if not user["locked"]:
            return {
                "source": "local_demo",
                "status": "unchanged",
                "reason": "not_locked",
                "user": _public(user),
            }
        if not confirm(_public(user)):
            return {
                "source": "local_demo",
                "status": "denied",
                "reason": "confirmation_denied",
                "username": user["username"],
            }
        user["locked"] = False
        return {"source": "local_demo", "status": "unlocked", "user": _public(user)}

    def _user(self, username):
        return self.users.get(username.casefold())


def _public(user):
    return {field: user[field] for field in PUBLIC_FIELDS}


def deny_unlock(user):
    """Refuse the change when the caller does not supply a confirmation callback."""
    return False


class ConfirmationRequired(Exception):
    """Signal that a browser confirmation is needed before changing an account."""

    def __init__(self, user):
        self.user = dict(user)
        super().__init__("Browser confirmation is required to unlock this account")


def prompt_confirm(user):
    """Ask on the terminal before a fictional test-OU unlock."""
    answer = input(
        "confirm unlock {username} ({name}, {department})? [y/n] ".format(**user)
    )
    return answer.strip().casefold() in {"y", "yes"}


def _arguments(call):
    if call.name not in TOOL_ARGUMENTS:
        raise ValueError("Unsupported tool: {0}".format(call.name))
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("{0} arguments must be a JSON object".format(call.name)) from exc
    expected = TOOL_ARGUMENTS[call.name]
    if not isinstance(arguments, dict) or set(arguments) != expected:
        raise ValueError("{0} requires arguments {1}".format(call.name, sorted(expected)))
    if "name" in expected:
        if not isinstance(arguments["name"], str) or not arguments["name"].strip():
            raise ValueError("find_users requires a non-empty name")
        if not isinstance(arguments["department"], str):
            raise ValueError("find_users department must be a string")
    if "username" in expected:
        username = arguments["username"]
        if not isinstance(username, str) or not username.strip():
            raise ValueError("{0} requires a non-empty username".format(call.name))
    return arguments


def _execute(directory, name, arguments, confirm):
    if name == "find_users":
        return directory.find_users(name=arguments["name"], department=arguments["department"])
    if name == "get_account_status":
        return directory.get_account_status(username=arguments["username"])
    return directory.unlock_account(username=arguments["username"], confirm=confirm)


def get_answer(client, prompt, *, directory=None, confirm=deny_unlock,
               max_tool_rounds=5, messages=None, instructions=None, tools=None):
    """Execute supported function calls and return the model's final text."""
    if max_tool_rounds < 0:
        raise ValueError("max_tool_rounds must be non-negative")
    if directory is None:
        directory = DemoDirectory()
    if messages is None:
        messages = []
    if instructions is None:
        instructions = INSTRUCTIONS
    if tools is None:
        tools = TOOLS
    # Keep the caller's history unchanged until the whole turn succeeds.
    turn = list(messages)
    turn.append({"role": "user", "content": prompt})
    for round_number in range(max_tool_rounds + 1):
        response = client.responses.create(
            model=DEPLOYMENT_NAME,
            instructions=instructions,
            input=list(turn),
            tools=tools,
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            turn.extend(response.output)
            messages[:] = turn
            return response.output_text
        if round_number == max_tool_rounds:
            raise RuntimeError("Tool-calling round limit exceeded")
        # Preserve all output items, including reasoning, for the next request.
        turn.extend(response.output)
        for call in calls:
            arguments = _arguments(call)
            result = _execute(directory, call.name, arguments, confirm)
            turn.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result),
            })
    raise RuntimeError("Tool-calling round limit exceeded")


class AuditedDirectory:
    """Append one JSON line for each directory call."""

    def __init__(self, directory, log_path, banner, instructions, tools):
        self.directory = directory
        self.log_path = log_path
        self.banner = banner
        self.instructions = instructions
        self.tools = tools

    def find_users(self, name, department):
        return self._record(
            "find_users",
            {"name": name, "department": department},
            lambda: self.directory.find_users(name, department),
        )

    def get_account_status(self, username):
        return self._record(
            "get_account_status",
            {"username": username},
            lambda: self.directory.get_account_status(username),
        )

    def unlock_account(self, username, confirm):
        return self._record(
            "unlock_account",
            {"username": username},
            lambda: self.directory.unlock_account(username, confirm=confirm),
        )

    def _record(self, tool, arguments, call):
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            result = call()
        except ConfirmationRequired as request:
            self._write({
                "time": timestamp,
                "tool": tool,
                "arguments": arguments,
                "status": "confirmation_required",
                "username": request.user.get("username"),
            })
            raise
        except Exception as exc:
            self._write({
                "time": timestamp,
                "tool": tool,
                "arguments": arguments,
                "status": "error",
                "error": str(exc),
            })
            raise
        self._write({
            "time": timestamp,
            "tool": tool,
            "arguments": arguments,
            "status": "ok",
            "result": result,
        })
        return result

    def _write(self, entry):
        with open(self.log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adpilot.json")
DEFAULT_AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "audit.log")
CONFIG_KEYS = {
    "directory": "ADPILOT_DIRECTORY",
    "dc": "ADPILOT_DC",
    "test_ou": "ADPILOT_TEST_OU",
    "service_account": "ADPILOT_SERVICE_ACCOUNT",
    "audit_log": "ADPILOT_AUDIT_LOG",
    "powershell": "ADPILOT_POWERSHELL",
    "host": "ADPILOT_HOST",
    "port": "ADPILOT_PORT",
    "azure_tenant_id": "ADPILOT_AZURE_TENANT_ID",
    "azure_client_id": "ADPILOT_AZURE_CLIENT_ID",
    "azure_client_secret": "ADPILOT_AZURE_CLIENT_SECRET",
}
DEFAULT_CONFIG = {
    "directory": "ad",
    "dc": "LAB01.ninety7.local",
    "test_ou": "AgenticAI-AD-OPs-test",
    "service_account": "svc-adpilot",
    "host": "127.0.0.1",
    "port": 8765,
    "azure_tenant_id": "",
    "azure_client_id": "",
    "azure_client_secret": "",
}


def read_config(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("adpilot.json must be an object")
    settings = {}
    for key, env_name in CONFIG_KEYS.items():
        if data.get(key) is not None:
            settings[env_name] = str(data[key])
    return settings


def ensure_config(path=CONFIG_PATH):
    """Create the lab settings file once. It contains names, not the password."""
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(DEFAULT_CONFIG, handle, indent=2)
        handle.write("\n")


def runtime_settings(config_path=CONFIG_PATH, environ=None):
    """Read adpilot.json, then let environment variables replace individual values."""
    settings = {}
    if config_path and os.path.exists(config_path):
        settings.update(read_config(config_path))
    source = os.environ if environ is None else environ
    for key, value in source.items():
        if key.startswith("ADPILOT_") and value:
            settings[key] = value
    return settings


def build_directory(environ=None, config_path=None):
    """Use the fictional directory unless adpilot.json or ADPILOT_DIRECTORY selects ad."""
    if environ is None:
        environ = runtime_settings(CONFIG_PATH if config_path is None else config_path)
    backend = environ.get("ADPILOT_DIRECTORY", "demo").strip().casefold()
    log_path = environ.get("ADPILOT_AUDIT_LOG", DEFAULT_AUDIT_LOG)
    if backend == "demo":
        return AuditedDirectory(DemoDirectory(), log_path, DEMO_BANNER, INSTRUCTIONS, TOOLS)
    if backend != "ad":
        raise ValueError("ADPILOT_DIRECTORY must be demo or ad")
    from ad_directory import (
        DEFAULT_OU, DEFAULT_SERVER, DEFAULT_SERVICE_ACCOUNT, ActiveDirectoryDirectory,
    )

    directory = ActiveDirectoryDirectory(
        ou_name=environ.get("ADPILOT_TEST_OU", DEFAULT_OU),
        server=environ.get("ADPILOT_DC", DEFAULT_SERVER),
        service_account=environ.get("ADPILOT_SERVICE_ACCOUNT", DEFAULT_SERVICE_ACCOUNT),
        powershell=environ.get("ADPILOT_POWERSHELL", "powershell.exe"),
    )
    tools = copy.deepcopy(TOOLS)
    for tool, description in zip(tools, AD_TOOL_DESCRIPTIONS):
        tool["description"] = description
    banner = (
        "ADPilot on LAB02 as {0}. Reading OU {1} from {2}. "
        "Unlocks require browser confirmation. Type quit to exit."
    ).format(directory.service_account, directory.ou_name, directory.server)
    instructions = AD_INSTRUCTIONS.format(
        ou=directory.ou_name, server=directory.server, account=directory.service_account,
    )
    return AuditedDirectory(directory, log_path, banner, instructions, tools)


DEMO_BANNER = "ADPilot demo. Fictional directory only. Type quit to exit."
AZURE_TOKEN_SCOPE = "https://ai.azure.com/.default"


def credential_from_settings(settings, secret_credential, default_credential):
    """Use the Entra app when adpilot.json has its tenant, client, and secret."""
    values = [
        (settings.get("ADPILOT_AZURE_TENANT_ID") or "").strip(),
        (settings.get("ADPILOT_AZURE_CLIENT_ID") or "").strip(),
        (settings.get("ADPILOT_AZURE_CLIENT_SECRET") or "").strip(),
    ]
    if any(values) and not all(values):
        raise ValueError(
            "adpilot.json needs azure_tenant_id, azure_client_id, and azure_client_secret"
        )
    if all(values):
        return secret_credential(*values)
    return default_credential()


def make_client(settings):
    # Import and authenticate only when a live client is created.
    from openai import OpenAI
    from azure.identity import ClientSecretCredential, DefaultAzureCredential, get_bearer_token_provider

    credential = credential_from_settings(settings, ClientSecretCredential, DefaultAzureCredential)
    token_provider = get_bearer_token_provider(credential, AZURE_TOKEN_SCOPE)
    return OpenAI(base_url=ENDPOINT, api_key=token_provider)


def main():
    directory = build_directory()
    client = make_client(runtime_settings())
    messages = []
    print(directory.banner)
    while True:
        try:
            prompt = input("you: ").strip()
        except EOFError:
            print()
            break
        if not prompt:
            continue
        if prompt.casefold() in {"quit", "exit"}:
            break
        try:
            answer = get_answer(
                client,
                prompt,
                directory=directory,
                confirm=prompt_confirm,
                messages=messages,
                instructions=directory.instructions,
                tools=directory.tools,
            )
        except (ValueError, RuntimeError, OSError) as error:
            print("adpilot: {0}".format(error))
            continue
        print("adpilot: {0}".format(answer))


if __name__ == "__main__":
    main()
