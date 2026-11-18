"""Private ADPilot chat. On LAB02 the Windows service keeps it running after logoff."""

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from main import (
    CONFIG_PATH,
    ConfirmationRequired,
    build_directory,
    ensure_config,
    get_answer,
    make_client,
    runtime_settings,
)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "server.log")
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ADPilot</title>
<style>
body { margin: 0; font: 16px/1.4 sans-serif; background: #f4f6f8; color: #1c2430; }
main { max-width: 40rem; margin: 0 auto; padding: 1.5rem 1rem 6rem; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
p.note { margin: 0 0 1rem; color: #526070; }
#transcript { display: flex; flex-direction: column; gap: 0.75rem; }
#transcript p { margin: 0; padding: 0.75rem 0.9rem; border-radius: 0.6rem; white-space: pre-wrap; }
.you { background: #d9e7f7; }
.adpilot { background: white; border: 1px solid #d5dde6; }
form { position: fixed; left: 0; right: 0; bottom: 0; background: white; border-top: 1px solid #d5dde6; }
form div { max-width: 40rem; margin: 0 auto; display: flex; gap: 0.5rem; padding: 0.75rem 1rem; }
input { flex: 1; font: inherit; padding: 0.6rem 0.7rem; border: 1px solid #b7c3d0; border-radius: 0.4rem; }
button { font: inherit; padding: 0.6rem 0.9rem; border: 0; border-radius: 0.4rem; background: #1f4e79; color: white; }
button:disabled { background: #8aa0b5; }
</style>
</head>
<body>
<main>
<h1>ADPilot</h1>
<p class="note">Read-only chat for the test OU. Unlock is disabled.</p>
<div id="transcript"></div>
</main>
<form>
<div>
<label for="message">Message</label>
<input id="message" name="message" autocomplete="off" required>
<button type="submit">Send</button>
</div>
</form>
<script>
const transcript = document.querySelector("#transcript");
const form = document.querySelector("form");
const input = document.querySelector("#message");
const button = form.querySelector("button");

function add(role, text) {
  const item = document.createElement("p");
  item.className = role;
  item.textContent = text;
  transcript.appendChild(item);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  add("you", text);
  button.disabled = true;
  try {
    const response = await fetch("/message", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text})
    });
    const payload = await response.json();
    if (payload.confirmation_required) {
      const confirmed = window.confirm(
        `Unlock ${payload.name} (${payload.username}) in the configured test OU?`
      );
      const confirmationResponse = await fetch("/confirm", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token: payload.token, confirmed})
      });
      const confirmation = await confirmationResponse.json();
      add("adpilot", confirmation.answer || confirmation.error || "No answer");
    } else {
      add("adpilot", payload.answer || payload.error || "No answer");
    }
  } catch (error) {
    add("adpilot", "The request failed.");
  } finally {
    button.disabled = false;
    input.focus();
  }
});
</script>
</body>
</html>
"""


def log(message):
    directory = os.path.dirname(LOG_PATH)
    os.makedirs(directory, exist_ok=True)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("{0} {1}\n".format(timestamp, message))


class ChatSession:
    """One conversation and its short-lived, per-account unlock confirmations."""

    CONFIRMATION_TTL_SECONDS = 300

    def __init__(self, client, directory):
        self.client = client
        self.directory = directory
        self.messages = []
        self.lock = threading.Lock()
        self.pending_unlocks = {}

    def reply(self, text):
        with self.lock:
            try:
                return get_answer(
                    self.client,
                    text,
                    directory=self.directory,
                    confirm=self._require_confirmation,
                    messages=self.messages,
                    instructions=self.directory.instructions,
                    tools=self.directory.tools,
                )
            except ConfirmationRequired as request:
                now = time.monotonic()
                self.pending_unlocks = {
                    token: pending
                    for token, pending in self.pending_unlocks.items()
                    if pending["expires_at"] > now
                }
                token = secrets.token_urlsafe(32)
                user = request.user
                self.pending_unlocks[token] = {
                    "username": user["username"],
                    "expires_at": now + self.CONFIRMATION_TTL_SECONDS,
                }
                return {
                    "confirmation_required": True,
                    "token": token,
                    "username": user["username"],
                    "name": user["name"],
                    "department": user["department"],
                }

    @staticmethod
    def _require_confirmation(user):
        raise ConfirmationRequired(user)

    def confirm_unlock(self, token, confirmed):
        with self.lock:
            pending = self.pending_unlocks.pop(token, None)
            if pending is None or pending["expires_at"] <= time.monotonic():
                return "Unlock confirmation expired or is invalid. Please try again."
            username = pending["username"]
            if not confirmed:
                return "Unlock cancelled for {0}.".format(username)

            current = self.directory.get_account_status(username)
            user = current.get("user") if isinstance(current, dict) else None
            if user is None:
                status = current.get("status", "failed") if isinstance(current, dict) else "failed"
                reason = current.get("reason", status) if isinstance(current, dict) else status
                return "Could not verify {0}; no change was made ({1}).".format(username, reason)
            if user.get("test_ou") is not True:
                return "Unlock denied: {0} is outside the configured test OU.".format(username)
            if user.get("locked") is not True:
                return "{0} is no longer locked; no change was made.".format(username)

            result = self.directory.unlock_account(username, confirm=lambda _user: True)
            if not isinstance(result, dict) or result.get("status") != "unlocked":
                status = result.get("status", "failed") if isinstance(result, dict) else "failed"
                reason = result.get("reason", status) if isinstance(result, dict) else status
                return "Could not unlock {0}; no success was confirmed ({1}).".format(
                    username, reason,
                )

            verified = self.directory.get_account_status(username)
            updated = verified.get("user") if isinstance(verified, dict) else None
            if not updated or updated.get("locked") is not False:
                return "Unlock was attempted for {0}, but verification did not confirm success.".format(
                    username,
                )
            return "Unlocked {0} and verified the account is no longer locked.".format(username)


def dispatch(method, path, body, session):
    if method == "GET" and path == "/":
        return 200, PAGE, "text/html; charset=utf-8"
    if method == "POST" and path == "/message":
        return _message(body, session)
    if method == "POST" and path == "/confirm":
        return _confirm(body, session)
    return 404, "Not found", "text/plain; charset=utf-8"


def _message(body, session):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return 400, json.dumps({"error": "invalid json"}), "application/json"
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return 400, json.dumps({"error": "text is required"}), "application/json"
    try:
        result = session.reply(text.strip())
    except Exception as error:
        log("message failed: {0}: {1}".format(type(error).__name__, error))
        result = {"answer": str(error)}
    if isinstance(result, dict):
        payload = result
    else:
        payload = {"answer": result}
    return 200, json.dumps(payload), "application/json"


def _confirm(body, session):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return 400, json.dumps({"error": "invalid json"}), "application/json"
    if (
        not isinstance(payload, dict)
        or set(payload) != {"token", "confirmed"}
        or not isinstance(payload["token"], str)
        or not payload["token"]
        or len(payload["token"]) > 128
        or not isinstance(payload["confirmed"], bool)
    ):
        return 400, json.dumps({"error": "token and confirmed are required"}), "application/json"
    try:
        answer = session.confirm_unlock(payload["token"], payload["confirmed"])
    except (OSError, RuntimeError, ValueError) as error:
        log("unlock confirmation failed: {0}".format(error))
        return 500, json.dumps({"error": "Unlock failed: {0}".format(error)}), "application/json"
    return 200, json.dumps({"answer": answer}), "application/json"


def make_handler(session):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._respond(*dispatch("GET", self.path.split("?", 1)[0], None, session))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length < 0 or length > 16384:
                self._respond(413, json.dumps({"error": "message too large"}), "application/json")
                return
            body = self.rfile.read(length) if length else b""
            self._respond(*dispatch("POST", self.path.split("?", 1)[0], body, session))

        def log_message(self, fmt, *args):
            log(fmt % args)

        def _respond(self, status, payload, content_type):
            data = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def open_server(config_path=None):
    """Create the listening chat server. The caller decides how long it runs."""
    path = config_path or os.environ.get("ADPILOT_CONFIG", CONFIG_PATH)
    ensure_config(path)
    directory = build_directory(config_path=path)
    settings = runtime_settings(path)
    host = settings.get("ADPILOT_HOST", "127.0.0.1")
    port = int(settings.get("ADPILOT_PORT", "8765"))
    session = ChatSession(make_client(settings), directory)
    server = ThreadingHTTPServer((host, port), make_handler(session))
    log("ADPilot listening on http://{0}:{1}".format(host, port))
    print("ADPilot listening on http://{0}:{1}".format(host, port))
    return server


def serve(config_path=None):
    open_server(config_path).serve_forever()


if __name__ == "__main__":
    serve()
