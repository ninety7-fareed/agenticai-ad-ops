# ADPilot - Agentic AI for Active Directory Operations

## Tool-calling demo

`main.py` runs a chat against a fictional directory. The model can request three tools:

- `find_users` searches by name and department
- `get_account_status` reports whether an account is enabled and locked
- `unlock_account` unlocks a locked account in the test OU

Python executes the calls, returns each JSON result with its matching call ID, and asks for a separate yes or no before changing an account. Two fictional users are named Juan. Only Juan Reyes (`juan.marketing`) is in Marketing and locked. `sam.ops` is locked outside the test OU, so an unlock is denied. Unsupported tools and invalid arguments are rejected, and tool rounds are limited.

Run the offline tests (no dependencies, credentials, or network required):

```sh
python3 -m unittest discover -v
```

The tests mock model responses, so they do not establish that the deployed Azure model selects tools correctly.

To run the live Azure demo with your existing Azure identity configured:

```sh
python3 -m pip install openai azure-identity
python3 main.py
```

Try `unlock Juan in marketing`. The live demo uses the existing endpoint and `agenticai-ad-ops-model` deployment. Responses API tool-call handling follows the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/function-calling).

## Active Directory on LAB02

The chat runs on member server **LAB02** as `svc-adpilot` and keeps running after you log off. Active Directory stays on domain controller **LAB01**. Searches stay in the test OU `AgenticAI-AD-OPs-test`. Unlock is still disabled.

Settings live in `adpilot.json` in this folder. Git ignores that file, so it stays on the machine and is not pushed. Put the Entra app values there once. Do not put the `svc-adpilot` Windows password in it. Windows stores that password in the service logon.

The model is called by an Entra app registration, not by an interactive sign-in. Create an app, give it a client secret, and on the Foundry resource assign that app the **Cognitive Services User** role. No API permissions are required on the app. Copy the tenant ID, client ID, and secret into `azure_tenant_id`, `azure_client_id`, and `azure_client_secret` in `adpilot.json`.

The chat is a Windows service named `ADPilot`, set to start automatically and run as `ninety7\svc-adpilot`. That account needs **Log on as a service** on LAB02. One-time setup from an elevated PowerShell on LAB02:

```powershell
python -m pip install openai azure-identity pywin32
.\register-adpilot-service.ps1
```

Enter the `svc-adpilot` password at the prompt. After that, the chat survives logoff and reboot. On LAB02, open `http://127.0.0.1:8765`.

`python server.py` runs the same chat in the foreground. `python main.py` is the console demo.
