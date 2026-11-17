import unittest
from unittest.mock import Mock

from slack_integration import create_slack_handler


SETTINGS = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "SLACK_ALLOWED_USER_ID": "U_ALLOWED",
    "SLACK_ALLOWED_TEAM_ID": "T_ALLOWED",
}


class FakeApp:
    def __init__(self, token):
        self.token = token
        self.events = {}
        self.actions = {}

    def event(self, name):
        def register(callback):
            self.events[name] = callback
            return callback
        return register

    def action(self, name):
        def register(callback):
            self.actions[name] = callback
            return callback
        return register


class SlackIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.apps = []

        def app_factory(token):
            app = FakeApp(token)
            self.apps.append(app)
            return app

        self.handler_factory = Mock(return_value="handler")
        self.handler = create_slack_handler(
            SETTINGS, session=self.session, app_factory=app_factory,
            handler_factory=self.handler_factory,
        )
        self.app = self.apps[0]

    def test_slack_message_replies_only_to_allowed_user_in_dm(self):
        self.session.reply.return_value = "ADPilot response"
        say = Mock()
        callback = self.app.events["message"]
        allowed = {
            "team_id": "T_ALLOWED",
            "event": {
                "channel_type": "im",
                "user": "U_ALLOWED",
                "text": "Find Juan",
            },
        }

        callback(event=allowed["event"], body=allowed, say=say)

        self.session.reply.assert_called_once_with("Find Juan")
        say.assert_called_once_with("ADPilot response")

        self.session.reset_mock()
        say.reset_mock()
        callback(
            event={"channel_type": "im", "user": "U_OTHER", "text": "Unlock account"},
            body={"team_id": "T_ALLOWED"},
            say=say,
        )
        callback(
            event={"channel_type": "channel", "user": "U_ALLOWED", "text": "Find Juan"},
            body={"team_id": "T_ALLOWED"},
            say=say,
        )
        self.session.reply.assert_not_called()
        say.assert_not_called()

    def test_unlock_request_sends_scoped_confirmation_buttons(self):
        self.session.reply.return_value = {
            "confirmation_required": True,
            "token": "one-use-token",
            "username": "juan.marketing",
            "name": "Juan Reyes",
            "department": "Marketing",
        }
        say = Mock()

        self.app.events["message"](
            event={
                "channel_type": "im",
                "user": "U_ALLOWED",
                "text": "Unlock Juan Reyes",
            },
            body={"team_id": "T_ALLOWED"},
            say=say,
        )

        blocks = say.call_args.kwargs["blocks"]
        self.assertIn("configured test OU", blocks[0]["text"]["text"])
        self.assertEqual(
            [button["action_id"] for button in blocks[1]["elements"]],
            ["adpilot_unlock_confirm", "adpilot_unlock_cancel"],
        )
        self.assertEqual(blocks[1]["elements"][0]["value"], "one-use-token")

    def test_unlock_action_rechecks_slack_identity_and_updates_private_reply(self):
        self.session.confirm_unlock.return_value = "Unlock verified"
        ack, client = Mock(), Mock()
        body = {
            "team": {"id": "T_ALLOWED"},
            "user": {"id": "U_ALLOWED"},
            "actions": [{"value": "one-use-token"}],
            "channel": {"id": "D_ALLOWED"},
            "message": {"ts": "123.456"},
        }

        self.app.actions["adpilot_unlock_confirm"](ack=ack, body=body, client=client)

        ack.assert_called_once_with()
        self.session.confirm_unlock.assert_called_once_with("one-use-token", True)
        client.chat_update.assert_called_once_with(
            channel="D_ALLOWED", ts="123.456", text="Unlock verified", blocks=[],
        )

        ack.reset_mock()
        client.reset_mock()
        self.session.reset_mock()
        body["user"] = {"id": "U_OTHER"}
        self.app.actions["adpilot_unlock_confirm"](ack=ack, body=body, client=client)
        ack.assert_called_once_with()
        self.session.confirm_unlock.assert_not_called()
        client.chat_update.assert_not_called()

    def test_cancel_action_does_not_confirm_unlock(self):
        self.session.confirm_unlock.return_value = "Unlock cancelled"
        self.app.actions["adpilot_unlock_cancel"](
            ack=Mock(),
            body={
                "team": {"id": "T_ALLOWED"},
                "user": {"id": "U_ALLOWED"},
                "actions": [{"value": "one-use-token"}],
                "channel": {"id": "D_ALLOWED"},
                "message": {"ts": "123.456"},
            },
            client=Mock(),
        )
        self.session.confirm_unlock.assert_called_once_with("one-use-token", False)

    def test_configuration_is_optional_but_partial_configuration_fails(self):
        self.assertIsNone(create_slack_handler({}))
        with self.assertRaisesRegex(RuntimeError, "SLACK_APP_TOKEN"):
            create_slack_handler(
                {"SLACK_BOT_TOKEN": "xoxb-test"},
                session=self.session,
                app_factory=lambda **_kwargs: FakeApp("token"),
            )

    def test_creates_socket_mode_handler_using_app_tokens(self):
        self.assertEqual(self.handler, "handler")
        self.handler_factory.assert_called_once()
        app, app_token = self.handler_factory.call_args.args
        self.assertIs(app, self.app)
        self.assertEqual(app_token, "xapp-test")


if __name__ == "__main__":
    unittest.main()
