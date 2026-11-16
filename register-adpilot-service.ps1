# Register ADPilot as a Windows service on LAB02.
# Run once from an elevated PowerShell. Windows stores the password; this script does not.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python was not found on PATH. Install Python for all users, then run this script again."
}

& $python -c "import openai, azure.identity, win32serviceutil"
if ($LASTEXITCODE -ne 0) {
    throw "Install openai, azure-identity, and pywin32 for this Python, then run this script again."
}

$credential = Get-Credential -UserName "ninety7\svc-adpilot" -Message "Password for the ADPilot service. Windows stores it for the service logon."
$password = $credential.GetNetworkCredential().Password
if (-not $password) {
    throw "A password is required so the service can run after logoff."
}

New-Item -ItemType Directory -Force -Path (Join-Path $repo "logs") | Out-Null

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
