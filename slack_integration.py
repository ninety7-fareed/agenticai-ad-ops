"""Slack DM integration for the ADPilot Windows service."""

import os
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from main import build_directory, make_client, runtime_settings
from server import ChatSession, log

REQUIRED_SETTINGS = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_ALLOWED_USER_ID",
    "SLACK_ALLOWED_TEAM_ID",
)


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
        say(*args, **kwargs)
    except Exception as error:
        log("Slack reply delivery failed: {0}: {1}".format(type(error).__name__, error))


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
    app = app_factory(token=values["SLACK_BOT_TOKEN"], token_verification_enabled=False)

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
            log("Slack message failed: {0}: {1}".format(type(error).__name__, error))
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
            log("Slack unlock confirmation failed: {0}: {1}".format(
                type(error).__name__, error,
            ))
            answer = "ADPilot could not complete that unlock. Details are logged on LAB02."
        channel = (body.get("channel") or {}).get("id")
        message_ts = (body.get("message") or {}).get("ts")
        if not channel or not message_ts:
            log("Slack unlock confirmation lacked channel or message timestamp")
            return
        try:
            client.chat_update(
                channel=channel,
                ts=message_ts,
                text=answer,
                blocks=[],
            )
        except Exception as error:
            log("Slack confirmation reply delivery failed: {0}: {1}".format(
                type(error).__name__, error,
            ))

    @app.action("adpilot_unlock_confirm")
    def confirm_unlock(ack, body, client):
        handle_confirmation(True, ack, body, client)

    @app.action("adpilot_unlock_cancel")
    def cancel_unlock(ack, body, client):
        handle_confirmation(False, ack, body, client)

    return handler_factory(app, values["SLACK_APP_TOKEN"])
