import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from main import DEPLOYMENT_NAME, INSTRUCTIONS, TOOLS, DemoDirectory, get_answer


def tool_call(name="find_users", arguments='{"name":"Juan","department":"Marketing"}',
              call_id="call_123"):
    return SimpleNamespace(type="function_call", name=name,
                           arguments=arguments, call_id=call_id)


def response(*items, text=""):
    return SimpleNamespace(output=list(items), output_text=text)


def assert_tools_not_called(test, directory):
    directory.find_users.assert_not_called()
    directory.get_account_status.assert_not_called()
    directory.unlock_account.assert_not_called()


class ToolCallingTests(unittest.TestCase):
    def test_tool_call_executes_function_and_returns_result(self):
        client = Mock()
        directory = Mock()
        directory.find_users.return_value = {"users": [{"username": "juan.marketing"}]}
        call = tool_call()
        reasoning = SimpleNamespace(type="reasoning")
        client.responses.create.side_effect = [
            response(reasoning, call), response(text="Juan Reyes in Marketing is locked."),
        ]

        answer = get_answer(client, "Check Juan in marketing", directory=directory)

        directory.find_users.assert_called_once_with(name="Juan", department="Marketing")
        self.assertEqual(answer, "Juan Reyes in Marketing is locked.")
        self.assertEqual(client.responses.create.call_count, 2)
        first, second = client.responses.create.call_args_list
        self.assertEqual(first.kwargs, {
            "model": DEPLOYMENT_NAME,
            "instructions": INSTRUCTIONS,
            "input": [{"role": "user", "content": "Check Juan in marketing"}],
            "tools": TOOLS,
        })
        self.assertEqual(second.kwargs["input"][:-1],
                         first.kwargs["input"] + [reasoning, call])
        output = second.kwargs["input"][-1]
        self.assertEqual(output["type"], "function_call_output")
        self.assertEqual(output["call_id"], "call_123")
        self.assertEqual(json.loads(output["output"]), directory.find_users.return_value)

    def test_plain_text_response_does_not_execute_tool(self):
        client, directory = Mock(), Mock()
        client.responses.create.return_value = response(text="Paris")
        self.assertEqual(get_answer(client, "Capital of France?", directory=directory), "Paris")
        assert_tools_not_called(self, directory)
        client.responses.create.assert_called_once()

    def test_later_turn_keeps_prior_messages(self):
        client, directory = Mock(), Mock()
        messages = []
        clarification = SimpleNamespace(type="message")
        client.responses.create.side_effect = [
            response(clarification, text="Which Juan?"),
            response(text="Juan Reyes."),
        ]

        get_answer(client, "Unlock Juan", directory=directory, messages=messages)
        get_answer(client, "The one in marketing", directory=directory, messages=messages)

        second_input = client.responses.create.call_args_list[1].kwargs["input"]
        self.assertEqual(second_input[0], {"role": "user", "content": "Unlock Juan"})
        self.assertEqual(second_input[1], clarification)
        self.assertEqual(second_input[-1], {"role": "user", "content": "The one in marketing"})

    def test_multiple_calls_keep_matching_call_ids(self):
        client, directory = Mock(), Mock()
        directory.find_users.side_effect = [{"users": [1]}, {"users": [2]}, {"users": [3]}]
        client.responses.create.side_effect = [
            response(tool_call(call_id="one"), tool_call(call_id="two")),
            response(tool_call(call_id="three")), response(text="Done"),
        ]
        self.assertEqual(get_answer(client, "Look up users", directory=directory), "Done")
        outputs = [item for item in client.responses.create.call_args.kwargs["input"]
                   if isinstance(item, dict) and item.get("type") == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["one", "two", "three"])

    def test_unsupported_tool_is_rejected(self):
        client, directory = Mock(), Mock()
        messages = []
        client.responses.create.return_value = response(tool_call(name="delete_user"))
        with self.assertRaisesRegex(ValueError, "Unsupported tool"):
            get_answer(client, "Check user", directory=directory, messages=messages)
        assert_tools_not_called(self, directory)
        self.assertEqual(messages, [])

    def test_invalid_arguments_do_not_execute_tool(self):
        cases = [
            ("get_account_status", "not json"),
            ("get_account_status", "[]"),
            ("get_account_status", "{}"),
            ("get_account_status", '{"username":42}'),
            ("get_account_status", '{"username":" "}'),
            ("get_account_status", '{"username":"juan.marketing","extra":true}'),
            ("find_users", '{"name":"Juan"}'),
            ("find_users", '{"name":"","department":""}'),
            ("find_users", '{"name":"Juan","department":null}'),
            ("unlock_account", '{"username":"juan.marketing","confirmed":true}'),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                client, directory = Mock(), Mock()
                client.responses.create.return_value = response(
                    tool_call(name=name, arguments=arguments)
                )
                with self.assertRaises(ValueError):
                    get_answer(client, "Check user", directory=directory)
                assert_tools_not_called(self, directory)

    def test_repeated_tool_calls_stop_at_round_limit(self):
        client, directory = Mock(), Mock()
        directory.find_users.return_value = {"users": []}
        client.responses.create.return_value = response(tool_call())
        with self.assertRaisesRegex(RuntimeError, "round limit"):
            get_answer(client, "Check user", directory=directory, max_tool_rounds=1)
        self.assertEqual(client.responses.create.call_count, 2)
        directory.find_users.assert_called_once()

    def test_unlock_tool_asks_for_confirmation(self):
        client = Mock()
        directory = DemoDirectory()
        confirm = Mock(return_value=False)
        call = tool_call(name="unlock_account", arguments='{"username":"juan.marketing"}')
        client.responses.create.side_effect = [
            response(call), response(text="Unlock was not confirmed."),
        ]

        answer = get_answer(client, "Unlock Juan Reyes", directory=directory, confirm=confirm)

        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[0]["username"], "juan.marketing")
        self.assertTrue(directory.get_account_status("juan.marketing")["user"]["locked"])
        sent = json.loads(client.responses.create.call_args.kwargs["input"][-1]["output"])
        self.assertEqual(sent["reason"], "confirmation_denied")
        self.assertEqual(answer, "Unlock was not confirmed.")


class APIConnectionError(Exception):
    """Stand-in for openai.APIConnectionError."""


class NetworkRetryTests(unittest.TestCase):
    def test_transient_model_error_retries_without_repeating_tools(self):
        client, directory = Mock(), Mock()
        directory.find_users.return_value = {"users": []}
        client.responses.create.side_effect = [
            response(tool_call()),
            APIConnectionError("Connection error."),
            response(text="Done"),
        ]

        with patch("net_retry.time.sleep") as sleep, patch("server.log"):
            answer = get_answer(client, "Check Juan", directory=directory)

        self.assertEqual(answer, "Done")
        directory.find_users.assert_called_once()
        self.assertEqual(client.responses.create.call_count, 3)
        sleep.assert_called_once_with(20)

    def test_model_error_is_raised_after_retries_are_exhausted(self):
        client, directory = Mock(), Mock()
        client.responses.create.side_effect = APIConnectionError("Connection error.")

        with patch("net_retry.time.sleep") as sleep, patch("server.log"):
            with self.assertRaises(APIConnectionError):
                get_answer(client, "Check Juan", directory=directory)

        self.assertEqual(client.responses.create.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [20, 45])
        assert_tools_not_called(self, directory)

    def test_non_network_model_error_is_not_retried(self):
        client, directory = Mock(), Mock()
        client.responses.create.side_effect = RuntimeError("content filter")

        with patch("net_retry.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "content filter"):
                get_answer(client, "Check Juan", directory=directory)

        sleep.assert_not_called()
        self.assertEqual(client.responses.create.call_count, 1)


class DemoDirectoryTests(unittest.TestCase):
    def test_find_users_can_return_both_juans_or_marketing_only(self):
        directory = DemoDirectory()
        both = directory.find_users("Juan", "")
        self.assertEqual(
            [user["username"] for user in both["users"]],
            ["juan.finance", "juan.marketing"],
        )
        marketing = directory.find_users("juan", "marketing")
        self.assertEqual([user["username"] for user in marketing["users"]], ["juan.marketing"])
        self.assertTrue(marketing["users"][0]["locked"])
        self.assertEqual(directory.find_users("nobody", "")["users"], [])

    def test_unlock_requires_confirmation_and_stays_inside_test_ou(self):
        directory = DemoDirectory()

        def refuse(user):
            raise AssertionError("confirmation is not required for {0}".format(user["username"]))

        outside = directory.unlock_account("sam.ops", confirm=refuse)
        self.assertEqual(outside["reason"], "outside_test_ou")
        self.assertTrue(directory.get_account_status("sam.ops")["user"]["locked"])

        unchanged = directory.unlock_account("juan.finance", confirm=refuse)
        self.assertEqual(unchanged["status"], "unchanged")

        denied = directory.unlock_account("juan.marketing", confirm=lambda user: False)
        self.assertEqual(denied["reason"], "confirmation_denied")
        self.assertTrue(directory.get_account_status("juan.marketing")["user"]["locked"])

        unlocked = directory.unlock_account("juan.marketing", confirm=lambda user: True)
        self.assertEqual(unlocked["status"], "unlocked")
        self.assertFalse(directory.get_account_status("Juan.Marketing")["user"]["locked"])

        missing = directory.unlock_account("missing", confirm=lambda user: True)
        self.assertEqual(missing["status"], "not_found")
        self.assertIsNone(directory.get_account_status("missing")["user"])

    def test_unlock_does_not_change_another_directory(self):
        first = DemoDirectory()
        first.unlock_account("juan.marketing", confirm=lambda user: True)
        second = DemoDirectory()
        self.assertTrue(second.get_account_status("juan.marketing")["user"]["locked"])


if __name__ == "__main__":
    unittest.main()
