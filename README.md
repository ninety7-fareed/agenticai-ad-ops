# ADPilot - Agentic AI for Active Directory Operations

## Tool-calling demo

`main.py` exposes `lookup_user` to the model, executes requested calls, and
returns each JSON result with its matching call ID before requesting the final
answer. The lookup uses fictional local data (`demo.user`), not live Active
Directory. Unsupported tools and invalid arguments are rejected; tool rounds
are limited to prevent an endless loop.

Run the offline tests (no dependencies, credentials, or network required):

```sh
python3 -m unittest discover -v
```

The tests verify tool execution, arguments, result/call-ID pairing, preserved
response context, multiple calls and rounds, plain text responses, and error
paths. They mock model responses, so they do not establish that the deployed
Azure model selects tools correctly.

To run the live Azure demo with your existing Azure identity configured:

```sh
python3 -m pip install openai azure-identity
python3 main.py
```

The live demo uses the existing endpoint and `agenticai-ad-ops-model` deployment.
Responses API tool-call handling follows the
[official OpenAI documentation](https://developers.openai.com/api/docs/guides/function-calling).
