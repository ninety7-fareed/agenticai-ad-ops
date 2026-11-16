# Register ADPilot to start at boot on LAB02 and keep running after logoff.
# Run once from an elevated PowerShell. Windows stores the password; this script does not.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python was not found on PATH. Install Python for all users, then run this script again."
}

& $python -c "import openai, azure.identity"
if ($LASTEXITCODE -ne 0) {
    throw "Install openai and azure-identity for this Python, then run this script again."
}

$credential = Get-Credential -UserName "ninety7\svc-adpilot" -Message "Password for the ADPilot startup task. Windows stores it for the task."
$password = $credential.GetNetworkCredential().Password
if (-not $password) {
    throw "A password is required so the task can run after logoff."
}

New-Item -ItemType Directory -Force -Path (Join-Path $repo "logs") | Out-Null
$action = New-ScheduledTaskAction -Execute $python -Argument "server.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "ADPilot" -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $password -RunLevel Limited -Force | Out-Null
$password = $null

Stop-ScheduledTask -TaskName "ADPilot" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "ADPilot"
Write-Output "ADPilot is registered. It keeps running after logoff. On LAB02, open http://127.0.0.1:8765"
