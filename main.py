"""Azure OpenAI tool-calling demo using a local, fictional directory."""

import json

ENDPOINT = "https://agenticai-ad-ops-resource.services.ai.azure.com/openai/v1"
DEPLOYMENT_NAME = "agenticai-ad-ops-model"

TOOLS = [{
    "type": "function",
    "name": "lookup_user",
    "description": "Look up a user in the local demo directory, not live Active Directory.",
    "parameters": {
        "type": "object",
        "properties": {"username": {"type": "string"}},
        "required": ["username"],
        "additionalProperties": False,
    },
    "strict": True,
}]


def lookup_user(username):
    """Return fictional demo data; no Active Directory connection is made."""
    users = {"demo.user": {"username": "demo.user", "enabled": True}}
    return {"source": "local_demo", "user": users.get(username)}


def get_answer(client, prompt, *, lookup=lookup_user, max_tool_rounds=5):
    """Execute supported function calls and return the model's final text."""
    if max_tool_rounds < 0:
        raise ValueError("max_tool_rounds must be non-negative")
    messages = [{"role": "user", "content": prompt}]
    for round_number in range(max_tool_rounds + 1):
        response = client.responses.create(
            model=DEPLOYMENT_NAME, input=list(messages), tools=TOOLS,
        )
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response.output_text
        if round_number == max_tool_rounds:
            raise RuntimeError("Tool-calling round limit exceeded")
        # Preserve all output items, including reasoning, for the next request.
        messages.extend(response.output)
        for call in calls:
            if call.name != "lookup_user":
                raise ValueError(f"Unsupported tool: {call.name}")
            arguments = json.loads(call.arguments)
            if (not isinstance(arguments, dict)
                    or set(arguments) != {"username"}
                    or not isinstance(arguments["username"], str)):
                raise ValueError("lookup_user requires one string username")
            result = lookup(**arguments)
            messages.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result),
            })
    raise RuntimeError("Tool-calling round limit exceeded")


def main():
    # Import and authenticate only when running the live demo.
    from openai import OpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://ai.azure.com/.default"
    )
    client = OpenAI(base_url=ENDPOINT, api_key=token_provider)
    answer = get_answer(client, "Use lookup_user to check whether demo.user is enabled.")
    print(f"answer: {answer}")


if __name__ == "__main__":
    main()
