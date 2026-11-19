"""Slack DM integration for the ADPilot Windows service."""

import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.web import WebClient

from main import build_directory, make_client, runtime_settings
from net_retry import call_with_retry
from server import ChatSession, log

REQUIRED_SETTINGS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_ALLOWED_USER_ID",
    "SLACK_ALLOWED_TEAM_ID",
)


def _hostname(url):
    try:
        return urlsplit(url).hostname or "unknown"
    except ValueError:
        return "unknown"


class InstrumentedSlackWebClient(WebClient):
    """Log each Socket Mode URL HTTP attempt without recording request data."""

    def __init__(self, *args, log_func=log, **kwargs):
        self._connection_log = log_func
        self._socket_mode_url_attempt = 0
        self._socket_mode_url_attempt_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def _perform_urllib_http_request_internal(self, url, req):
        parsed_url = urlsplit(url)
        if not parsed_url.path.rstrip("/").endswith("/apps.connections.open"):
            return super()._perform_urllib_http_request_internal(url, req)

        with self._socket_mode_url_attempt_lock:
            self._socket_mode_url_attempt += 1
            attempt = self._socket_mode_url_attempt
        destination = parsed_url.hostname or "unknown"
        started = time.perf_counter()
        try:
            response = super()._perform_urllib_http_request_internal(url, req)
        except Exception as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            error_type = "{0}.{1}".format(type(error).__module__, type(error).__name__)
            self._connection_log(
                "Slack connection attempt={0} stage=socket_mode_url "
                "destination_hostname={1} elapsed_ms={2:.1f} result=failed error_type={3}".format(
                    attempt, destination, elapsed_ms, error_type,
                )
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        self._connection_log(
            "Slack connection attempt={0} stage=socket_mode_url "
            "destination_hostname={1} elapsed_ms={2:.1f} result=http_{3}".format(
                attempt, destination, elapsed_ms, response.get("status", "unknown"),
            )
        )
        return response


def _instrument_websocket_connect(handler, log_func=log):
    client = handler.client
    original_connect = client.connect
    attempt_lock = threading.Lock()
    attempts = 0

    def connect():
        nonlocal attempts
        if client.wss_uri is None:
            client.wss_uri = client.issue_new_wss_url()
        destination = _hostname(client.wss_uri)
        with attempt_lock:
            attempts += 1
            attempt = attempts
        started = time.perf_counter()
        try:
            result = original_connect()
        except Exception as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            error_type = "{0}.{1}".format(type(error).__module__, type(error).__name__)
            log_func(
                "Slack connection attempt={0} stage=websocket_connect "
                "destination_hostname={1} elapsed_ms={2:.1f} result=failed error_type={3}".format(
                    attempt, destination, elapsed_ms, error_type,
                )
            )
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        result_name = "connected" if client.is_connected() else "not_connected"
        log_func(
            "Slack connection attempt={0} stage=websocket_connect "
            "destination_hostname={1} elapsed_ms={2:.1f} result={3}".format(
                attempt, destination, elapsed_ms, result_name,
            )
        )
        return result

    client.connect = connect
    return handler


def _authorized(team_id, user_id, allowed_team, allowed_user):
    return team_id == allowed_team and user_id == allowed_user


def _action_identity(body):
    team = body.get("team")
    user = body.get("user")
    team_id = team.get("id") if isinstance(team, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    return team_id or body.get("team_id"), user_id or body.get("user_id")


def _send_reply(say, *args, **kwargs):
    try:
        call_with_retry(lambda: say(*args, **kwargs), "Slack reply", log)
    except Exception as error:
        log("Slack reply delivery failed: {0}.{1}: {2}".format(
            type(error).__module__, type(error).__name__, error,
        ))


def create_slack_handler(environ=None, session=None, app_factory=App,
                         handler_factory=SocketModeHandler):
    if environ is None:
        load_dotenv(Path(__file__).with_name(".env"))
    settings = os.environ if environ is None else environ
    values = {key: (settings.get(key) or "").strip() for key in REQUIRED_SETTINGS}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "Slack configuration is incomplete; missing: {0}".format(", ".join(missing))
        )

    if session is None:
        directory = build_directory()
        session = ChatSession(make_client(runtime_settings()), directory)
    web_client = InstrumentedSlackWebClient(token=values["SLACK_BOT_TOKEN"])
    app = app_factory(client=web_client, token_verification_enabled=False)

    @app.event("message")
    def handle_message(event, body, say):
        if event.get("bot_id") or event.get("subtype") or event.get("channel_type") != "im":
            return
        if not _authorized(
            body.get("team_id"), event.get("user"),
            values["SLACK_ALLOWED_TEAM_ID"], values["SLACK_ALLOWED_USER_ID"],
        ):
            log("Slack message ignored: sender or workspace is not allow-listed")
            return
        text = event.get("text", "").strip()
        if not text:
            _send_reply(say, "Send a text message to chat with ADPilot.")
            return
        try:
            result = session.reply(text)
        except Exception as error:
            log("Slack-triggered ADPilot response failed: {0}.{1}: {2}".format(
                type(error).__module__, type(error).__name__, error,
            ))
            _send_reply(
                say,
                "ADPilot could not complete that request. Please try again; details are logged on LAB02.",
            )
            return

        if isinstance(result, dict) and result.get("confirmation_required") is True:
            _send_reply(
                say,
                text="Confirm unlock for {0} ({1}, {2}) in the configured test OU?".format(
                    result["name"], result["username"], result["department"],
                ),
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "text": (
                            "Confirm account unlock?\n"
                            "{name} ({username}) — {department}\n"
                            "Limited to the configured test OU."
                        ).format(**result),
                    },
                }, {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Unlock account"},
                        "style": "danger",
                        "action_id": "adpilot_unlock_confirm",
                        "value": result["token"],
                        "confirm": {
                            "title": {"type": "plain_text", "text": "Unlock account?"},
                            "text": {
                                "type": "plain_text",
                                "text": "Unlock {0} in the configured test OU?".format(
                                    result["username"],
                                ),
                            },
                            "confirm": {"type": "plain_text", "text": "Unlock"},
                            "deny": {"type": "plain_text", "text": "Cancel"},
                        },
                    }, {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "action_id": "adpilot_unlock_cancel",
                        "value": result["token"],
                    }],
                }],
            )
            return
        _send_reply(say, str(result))

    def handle_confirmation(confirmed, ack, body, client):
        ack()
        team_id, user_id = _action_identity(body)
        if not _authorized(
            team_id, user_id,
            values["SLACK_ALLOWED_TEAM_ID"], values["SLACK_ALLOWED_USER_ID"],
        ):
            log("Slack action ignored: team_match={0}, user_match={1}".format(
                team_id == values["SLACK_ALLOWED_TEAM_ID"],
                user_id == values["SLACK_ALLOWED_USER_ID"],
            ))
            return
        action = (body.get("actions") or [{}])[0]
        try:
            answer = session.confirm_unlock(action.get("value", ""), confirmed)
        except Exception as error:
            log("Slack unlock processing failed: {0}.{1}: {2}".format(
                type(error).__module__, type(error).__name__, error,
            ))
            answer = "ADPilot could not complete that unlock. Details are logged on LAB02."
        channel = (body.get("channel") or {}).get("id")
        message_ts = (body.get("message") or {}).get("ts")
        if not channel or not message_ts:
            log("Slack unlock confirmation lacked channel or message timestamp")
            return
        try:
            call_with_retry(
                lambda: client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    text=answer,
                    blocks=[],
                ),
                "Slack confirmation",
                log,
            )
        except Exception as error:
            log("Slack confirmation delivery failed: {0}.{1}: {2}".format(
                type(error).__module__, type(error).__name__, error,
            ))

    @app.action("adpilot_unlock_confirm")
    def confirm_unlock(ack, body, client):
        handle_confirmation(True, ack, body, client)

    @app.action("adpilot_unlock_cancel")
    def cancel_unlock(ack, body, client):
        handle_confirmation(False, ack, body, client)

    handler = handler_factory(app, values["SLACK_APP_TOKEN"])
    return _instrument_websocket_connect(handler)
