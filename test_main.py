import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from main import DEPLOYMENT_NAME, TOOLS, get_answer, lookup_user


def tool_call(name="lookup_user", arguments='{"username":"demo.user"}', call_id="call_123"):
    return SimpleNamespace(type="function_call", name=name,
                           arguments=arguments, call_id=call_id)


def response(*items, text=""):
    return SimpleNamespace(output=list(items), output_text=text)


class ToolCallingTests(unittest.TestCase):
    def test_tool_call_executes_function_and_returns_result(self):
        client = Mock()
        lookup = Mock(return_value={"username": "demo.user", "enabled": True})
        call = tool_call()
        reasoning = SimpleNamespace(type="reasoning")
        client.responses.create.side_effect = [
            response(reasoning, call), response(text="demo.user is enabled."),
        ]

        answer = get_answer(client, "Check demo.user", lookup=lookup)

        lookup.assert_called_once_with(username="demo.user")
        self.assertEqual(answer, "demo.user is enabled.")
        self.assertEqual(client.responses.create.call_count, 2)
        first, second = client.responses.create.call_args_list
        self.assertEqual(first.kwargs, {
            "model": DEPLOYMENT_NAME,
            "input": [{"role": "user", "content": "Check demo.user"}],
            "tools": TOOLS,
        })
        self.assertEqual(second.kwargs["input"][:-1],
                         first.kwargs["input"] + [reasoning, call])
        output = second.kwargs["input"][-1]
        self.assertEqual(output["type"], "function_call_output")
        self.assertEqual(output["call_id"], "call_123")
        self.assertEqual(json.loads(output["output"]), lookup.return_value)

    def test_plain_text_response_does_not_execute_tool(self):
        client, lookup = Mock(), Mock()
        client.responses.create.return_value = response(text="Paris")
        self.assertEqual(get_answer(client, "Capital of France?", lookup=lookup), "Paris")
        lookup.assert_not_called()
        client.responses.create.assert_called_once()

    def test_multiple_calls_keep_matching_call_ids(self):
        client, lookup = Mock(), Mock(side_effect=[{"user": 1}, {"user": 2}, {"user": 3}])
        client.responses.create.side_effect = [
            response(tool_call(call_id="one"), tool_call(call_id="two")),
            response(tool_call(call_id="three")), response(text="Done"),
        ]
        self.assertEqual(get_answer(client, "Look up users", lookup=lookup), "Done")
        outputs = [item for item in client.responses.create.call_args.kwargs["input"]
                   if isinstance(item, dict) and item.get("type") == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["one", "two", "three"])

    def test_unsupported_tool_is_rejected(self):
        client, lookup = Mock(), Mock()
        client.responses.create.return_value = response(tool_call(name="delete_user"))
        with self.assertRaisesRegex(ValueError, "Unsupported tool"):
            get_answer(client, "Check user", lookup=lookup)
        lookup.assert_not_called()

    def test_invalid_arguments_do_not_execute_tool(self):
        for arguments in ["not json", "[]", "{}", '{"username":42}',
                          '{"username":"demo.user","extra":true}']:
            with self.subTest(arguments=arguments):
                client, lookup = Mock(), Mock()
                client.responses.create.return_value = response(tool_call(arguments=arguments))
                with self.assertRaises(ValueError):
                    get_answer(client, "Check user", lookup=lookup)
                lookup.assert_not_called()

    def test_repeated_tool_calls_stop_at_round_limit(self):
        client, lookup = Mock(), Mock(return_value={})
        client.responses.create.return_value = response(tool_call())
        with self.assertRaisesRegex(RuntimeError, "round limit"):
            get_answer(client, "Check user", lookup=lookup, max_tool_rounds=1)
        self.assertEqual(client.responses.create.call_count, 2)
        lookup.assert_called_once()

    def test_demo_lookup_returns_fictional_data_or_missing_user(self):
        self.assertTrue(lookup_user("demo.user")["user"]["enabled"])
        self.assertIsNone(lookup_user("missing")["user"])
        self.assertEqual(lookup_user("demo.user")["source"], "local_demo")


if __name__ == "__main__":
    unittest.main()
