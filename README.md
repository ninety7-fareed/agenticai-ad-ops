# ADPilot - Agentic AI for Active Directory Operations

## Tool-calling demo

`main.py` runs a chat against a fictional directory. The model can request three tools:

- `find_users` searches by name and department
- `get_account_status` reports whether an account is enabled and locked
- `unlock_account` unlocks a locked account in the test OU

Python executes the calls, returns each JSON result with its matching call ID, and asks for a separate yes or no before changing an account. Two fictional users are named Juan. Only Juan Reyes (`juan.marketing`) is in Marketing and locked. `sam.ops` is locked outside the test OU, so an unlock is denied. Unsupported tools and invalid arguments are rejected, and tool rounds are limited.

ADPilot is intended for this app's directory account search, status, and unlock tasks; it should decline unrelated questions.

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

The chat runs on member server **LAB02** as `svc-adpilot` and keeps running after you log off. Active Directory stays on domain controller **LAB01**. Searches and unlocks are restricted to the configured test OU, `AgenticAI-AD-OPs-test`. Each unlock requires a separate browser confirmation for the specific account, and the app verifies the account is in the OU and still locked before changing it and verifies the result afterward.

Settings live in `adpilot.json` in this folder. Git ignores that file, so it stays on the machine and is not pushed. Put the Entra app values there once. Do not put the `svc-adpilot` Windows password in it. Windows stores that password in the service logon.

The model is called by an Entra app registration, not by an interactive sign-in. Create an app, give it a client secret, and on the Foundry resource assign that app the **Cognitive Services User** role. No API permissions are required on the app. Copy the tenant ID, client ID, and secret into `azure_tenant_id`, `azure_client_id`, and `azure_client_secret` in `adpilot.json`.

The chat is a Windows service named `ADPilot`, set to start automatically and run as `ninety7\svc-adpilot`. That account needs **Log on as a service** on LAB02 and delegated Active Directory permission to unlock accounts only in the test OU. Do not make it a Domain Admin. The registration script grants the service account write access to the app's `logs` folder for server and audit logs. Both logs include ISO 8601 UTC timestamps. One-time setup from an elevated PowerShell on LAB02:

```powershell
python -m pip install openai azure-identity pywin32 slack-bolt python-dotenv
.\register-adpilot-service.ps1
```

Enter the `svc-adpilot` password at the prompt. After that, the chat survives logoff and reboot. On LAB02, open `http://127.0.0.1:8765`.

`python server.py` runs the same chat in the foreground. `python main.py` is the console demo.

## Slack direct messages

The ADPilot service also starts the Slack Socket Mode bot when Slack is configured. The bot accepts direct messages only from the configured Slack user and workspace; it ignores channels, other users, and bot messages. Chat answers, errors, and unlock outcomes are sent only as DM replies. Unlock requests show an explicit Slack confirmation button and remain restricted to the configured test OU.

Configure these values in the ignored `.env` file on LAB02; do not commit or paste token values. The service-registration script restricts `.env` access to the service account, SYSTEM, and local Administrators. Install Slack dependencies into the same all-users Python installation used by the ADPilot service, not only into a per-user virtual environment.

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `SLACK_ALLOWED_USER_ID`
- `SLACK_ALLOWED_TEAM_ID`

Enable Socket Mode and interactivity for the Slack app, subscribe to the `message.im` event, grant the bot `chat:write` and `im:history` scopes, and grant the app-level token `connections:write`. Reinstall the app after changing scopes. Restart the Windows service after updating `.env`:

```powershell
Restart-Service ADPilot
```

The bot runs in the same service as the web chat. If Slack settings are absent, the web chat remains available and Slack is not started. Partial Slack configuration or connection failures are written to the service logs.
