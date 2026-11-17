import os
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv(Path(__file__).with_name(".env"))

bot_token = os.environ["SLACK_BOT_TOKEN"]
app_token = os.environ["SLACK_APP_TOKEN"]
allowed_user = os.environ["SLACK_ALLOWED_USER_ID"]
allowed_team = os.environ["SLACK_ALLOWED_TEAM_ID"]

if not allowed_user.strip() or not allowed_team.strip():
    raise RuntimeError("Configure the allowed Slack user and workspace IDs.")

app = App(token=bot_token)


@app.event("message")
def handle_message(event, body, say):
    # Process only ordinary human DMs from your account and workspace.
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("channel_type") != "im":
        return
    if body.get("team_id") != allowed_team:
        return
    if event.get("user") != allowed_user:
        return

    say("ADPilot is connected. This is a connection test; no AD actions ran.")


if __name__ == "__main__":
    print("Starting ADPilot Slack connection...")
    SocketModeHandler(app, app_token).start()