# Register ADPilot as a Windows service on LAB02.
# Run once from an elevated PowerShell. Windows stores the password; this script does not.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python was not found on PATH. Install Python for all users, then run this script again."
}

& $python -c "import openai, azure.identity, win32serviceutil, slack_bolt, dotenv"
if ($LASTEXITCODE -ne 0) {
    throw "Install openai, azure-identity, pywin32, slack-bolt, and python-dotenv for this Python, then run this script again."
}

$credential = Get-Credential -UserName "ninety7\svc-adpilot" -Message "Password for the ADPilot service. Windows stores it for the service logon."
$password = $credential.GetNetworkCredential().Password
if (-not $password) {
    throw "A password is required so the service can run after logoff."
}

$logs = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$logPermission = "{0}:(OI)(CI)M" -f $credential.UserName
& icacls.exe $logs /grant $logPermission /T
if ($LASTEXITCODE -ne 0) {
    throw "Could not grant the service account access to the logs folder."
}
$slackEnv = Join-Path $repo ".env"
if (Test-Path $slackEnv) {
    & icacls.exe $slackEnv /inheritance:r
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict inherited access to the Slack configuration file."
    }
    $serviceReadPermission = "{0}:R" -f $credential.UserName
    & icacls.exe $slackEnv /grant:r "NT AUTHORITY\SYSTEM:F" "BUILTIN\Administrators:F" $serviceReadPermission
    if ($LASTEXITCODE -ne 0) {
        throw "Could not grant the service account read access to the Slack configuration file."
    }
}

$existing = Get-Service -Name "ADPilot" -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Service -Name "ADPilot" -Force -ErrorAction SilentlyContinue
    & $python (Join-Path $repo "adpilot_service.py") remove
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove the existing ADPilot service."
    }
}

& $python (Join-Path $repo "adpilot_service.py") --startup auto install
if ($LASTEXITCODE -ne 0) {
    throw "Service install failed."
}

$service = Get-CimInstance -ClassName Win32_Service -Filter "Name='ADPilot'"
$change = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{
    StartName = $credential.UserName
    StartPassword = $password
    StartMode = "Automatic"
    DesktopInteract = $false
}
$password = $null
if ($change.ReturnValue -ne 0) {
    throw "Windows could not set the service account. Change returned $($change.ReturnValue). Give ninety7\svc-adpilot the 'Log on as a service' right on LAB02."
}

Start-Service -Name "ADPilot"
Write-Output "ADPilot service is running as ninety7\svc-adpilot. It keeps running after logoff. On LAB02, open http://127.0.0.1:8765"
