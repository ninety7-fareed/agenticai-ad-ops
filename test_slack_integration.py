import unittest
from unittest.mock import Mock, patch
from urllib.request import Request
from urllib.error import URLError

from slack_sdk.web import WebClient

from slack_integration import (
    InstrumentedSlackWebClient,
    _instrument_websocket_connect,
    create_slack_handler,
)


SETTINGS = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "SLACK_ALLOWED_USER_ID": "U_ALLOWED",
    "SLACK_ALLOWED_TEAM_ID": "T_ALLOWED",
}


class FakeApp:
    def __init__(self, client, token_verification_enabled=True):
        self.client = client
        self.token = client.token
        self.token_verification_enabled = token_verification_enabled
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

        def app_factory(client, token_verification_enabled):
            app = FakeApp(client, token_verification_enabled)
            self.apps.append(app)
            return app

        self.socket_client = Mock()
        self.socket_client.wss_uri = None
        self.socket_client.issue_new_wss_url.return_value = (
            "wss://wss-primary.slack.com/link?secret=do-not-log"
        )
        self.socket_client.is_connected.return_value = True
        self.handler_factory = Mock(return_value=Mock(client=self.socket_client))
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

    def test_slack_model_failure_is_logged_as_response_failure(self):
        self.session.reply.side_effect = TimeoutError("Request timed out")
        say = Mock()
        with patch("slack_integration.log") as log_mock:
            self.app.events["message"](
                event={
                    "channel_type": "im",
                    "user": "U_ALLOWED",
                    "text": "Find Juan",
                },
                body={"team_id": "T_ALLOWED"},
                say=say,
            )

        log_mock.assert_called_once_with(
            "Slack-triggered ADPilot response failed: builtins.TimeoutError: Request timed out",
        )
        say.assert_called_once()

    def test_slack_reply_retries_a_handshake_timeout(self):
        self.session.reply.return_value = "ADPilot response"
        say = Mock(side_effect=[URLError("handshake timed out"), None])

        with patch("net_retry.time.sleep") as sleep, patch("slack_integration.log") as log_mock:
            self.app.events["message"](
                event={
                    "channel_type": "im",
                    "user": "U_ALLOWED",
                    "text": "Find Juan",
                },
                body={"team_id": "T_ALLOWED"},
                say=say,
            )

        self.assertEqual(say.call_count, 2)
        sleep.assert_called_once_with(20)
        log_mock.assert_called_once()
        self.assertIn("retrying in 20s", log_mock.call_args.args[0])

    def test_slack_reply_reports_delivery_failure_after_retries(self):
        self.session.reply.return_value = "ADPilot response"
        say = Mock(side_effect=URLError("handshake timed out"))

        with patch("net_retry.time.sleep") as sleep, patch("slack_integration.log") as log_mock:
            self.app.events["message"](
                event={
                    "channel_type": "im",
                    "user": "U_ALLOWED",
                    "text": "Find Juan",
                },
                body={"team_id": "T_ALLOWED"},
                say=say,
            )

        self.assertEqual(say.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [20, 45])
        self.assertIn("Slack reply delivery failed", log_mock.call_args_list[-1].args[0])

    def test_slack_reply_does_not_retry_a_rejected_message(self):
        self.session.reply.return_value = "ADPilot response"
        say = Mock(side_effect=RuntimeError("invalid_blocks"))

        with patch("net_retry.time.sleep") as sleep, patch("slack_integration.log"):
            self.app.events["message"](
                event={
                    "channel_type": "im",
                    "user": "U_ALLOWED",
                    "text": "Find Juan",
                },
                body={"team_id": "T_ALLOWED"},
                say=say,
            )

        say.assert_called_once()
        sleep.assert_not_called()

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
                "team_id": "T_ALLOWED",
                "user_id": "U_ALLOWED",
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
        self.assertIs(self.handler.client, self.socket_client)
        self.assertFalse(self.app.token_verification_enabled)
        self.handler_factory.assert_called_once()
        app, app_token = self.handler_factory.call_args.args
        self.assertIs(app, self.app)
        self.assertEqual(app_token, "xapp-test")
        self.assertIsInstance(self.app.client, InstrumentedSlackWebClient)

    def test_socket_mode_url_http_attempt_logs_only_host_stage_and_elapsed(self):
        entries = []
        client = InstrumentedSlackWebClient(token="xapp-do-not-log", log_func=entries.append)
        request = Request("https://slack.com/api/apps.connections.open")
        response = {"status": 200, "headers": {}, "body": '{"ok": true}'}

        with patch.object(
            WebClient, "_perform_urllib_http_request_internal",
            side_effect=[URLError("wss://should-not-be-logged"), response],
        ) as send:
            with self.assertRaises(URLError):
                client._perform_urllib_http_request_internal(
                    "https://slack.com/api/apps.connections.open", request,
                )
            client._perform_urllib_http_request_internal(
                "https://slack.com/api/apps.connections.open", request,
            )

        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(entries), 2)
        self.assertIn("attempt=1 stage=socket_mode_url", entries[0])
        self.assertIn("destination_hostname=slack.com", entries[0])
        self.assertRegex(entries[0], r"elapsed_ms=\d+\.\d")
        self.assertIn("result=failed", entries[0])
        self.assertIn("urllib.error.URLError", entries[0])
        self.assertIn("attempt=2 stage=socket_mode_url", entries[1])
        self.assertRegex(entries[1], r"elapsed_ms=\d+\.\d")
        self.assertIn("result=http_200", entries[1])
        for entry in entries:
            self.assertNotIn("xapp-do-not-log", entry)
            self.assertNotIn("wss://", entry)

    def test_websocket_attempt_logs_hostname_without_logging_url(self):
        entries = []
        handler = Mock()
        handler.client = Mock()
        handler.client.wss_uri = "wss://wss-primary.slack.com/link?secret=do-not-log"
        handler.client.is_connected.return_value = True

        _instrument_websocket_connect(handler, entries.append)
        handler.client.connect()

        self.assertEqual(len(entries), 1)
        self.assertIn("attempt=1 stage=websocket_connect", entries[0])
        self.assertIn("destination_hostname=wss-primary.slack.com", entries[0])
        self.assertRegex(entries[0], r"elapsed_ms=\d+\.\d")
        self.assertIn("result=connected", entries[0])
        self.assertNotIn("secret", entries[0])
        self.assertNotIn("/link", entries[0])
        self.assertNotIn("wss://", entries[0])

    def test_websocket_url_failure_does_not_log_a_websocket_url_or_stage(self):
        entries = []
        handler = Mock()
        handler.client.wss_uri = None
        handler.client.issue_new_wss_url.side_effect = TimeoutError("request timed out")

        _instrument_websocket_connect(handler, entries.append)
        with self.assertRaises(TimeoutError):
            handler.client.connect()

        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
