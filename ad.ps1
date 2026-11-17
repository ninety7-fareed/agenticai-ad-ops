# Active Directory queries and scoped account unlocks for ADPilot.
# Run on member server LAB02. Server is the domain controller, LAB01.
# Unlock requests are revalidated against the configured test OU before execution.

param(
    [Parameter(Mandatory = $true)]
    [string]$TestOuName,

    [Parameter(Mandatory = $true)]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$ServiceAccount,

    [Parameter(Mandatory = $true)]
    [string]$RequestBase64
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Result($Result) {
    Write-Output (ConvertTo-Json -InputObject $Result -Compress -Depth 6)
}

function Convert-User($User) {
    $name = $User.DisplayName
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = $User.Name
    }
    $department = $User.Department
    if ($null -eq $department) {
        $department = ""
    }
    $locked = $false
    if ($User.LockedOut -eq $true) {
        $locked = $true
    }
    return @{
        username = $User.SamAccountName
        name = $name
        department = $department
        enabled = [bool]$User.Enabled
        locked = $locked
        distinguished_name = $User.DistinguishedName
    }
}

try {
    $raw = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestBase64))
    if ([string]::IsNullOrWhiteSpace($raw)) {
        Write-Result @{ ok = $false; error = "empty_request" }
        exit 1
    }
    $request = $raw | ConvertFrom-Json
    $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $account = $current
    $separator = $current.LastIndexOf("\")
    if ($separator -ge 0) {
        $account = $current.Substring($separator + 1)
    }
    if ($account -ne $ServiceAccount) {
        Write-Result @{ ok = $false; error = "wrong_identity"; message = $current }
        exit 0
    }
    Import-Module ActiveDirectory
    $escaped = $TestOuName.Replace("'", "''")
    $filter = "Name -eq '{0}'" -f $escaped
    $ous = @(Get-ADOrganizationalUnit -Server $Server -Filter $filter)
    if ($ous.Count -eq 0) {
        Write-Result @{ ok = $false; error = "ou_not_found" }
        exit 0
    }
    if ($ous.Count -gt 1) {
        Write-Result @{ ok = $false; error = "ou_ambiguous" }
        exit 0
    }
    $ouDn = $ous[0].DistinguishedName
    $operation = [string]$request.op

    if ($operation -eq "search") {
        $found = @(Get-ADUser -Server $Server -SearchBase $ouDn -SearchScope Subtree -Filter * -Properties Department, LockedOut, DisplayName, DistinguishedName)
        $users = New-Object System.Collections.Generic.List[object]
        foreach ($user in $found) {
            $users.Add((Convert-User $user))
        }
        Write-Result @{ ok = $true; ou = $ouDn; users = $users }
        exit 0
    }

    if ($operation -eq "status") {
        $username = [string]$request.username
        if ([string]::IsNullOrWhiteSpace($username)) {
            Write-Result @{ ok = $false; error = "invalid_username" }
            exit 0
        }
        try {
            $user = Get-ADUser -Server $Server -Identity $username -Properties Department, LockedOut, DisplayName, DistinguishedName
        } catch {
            $typeName = $_.Exception.GetType().Name
            if ($typeName -eq "ADIdentityNotFoundException") {
                Write-Result @{ ok = $false; error = "user_not_found" }
                exit 0
            }
            throw
        }
        $distinguishedName = [string]$user.DistinguishedName
        $suffix = ",{0}" -f $ouDn
        if (-not $distinguishedName.EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase)) {
            Write-Result @{ ok = $false; error = "outside_test_ou" }
            exit 0
        }
        $users = New-Object System.Collections.Generic.List[object]
        $users.Add((Convert-User $user))
        Write-Result @{ ok = $true; ou = $ouDn; users = $users }
        exit 0
    }

    if ($operation -eq "unlock") {
        $username = [string]$request.username
        if ([string]::IsNullOrWhiteSpace($username)) {
            Write-Result @{ ok = $false; error = "invalid_username" }
            exit 0
        }
        try {
            $user = Get-ADUser -Server $Server -Identity $username -Properties Department, LockedOut, DisplayName, DistinguishedName
        } catch {
            $typeName = $_.Exception.GetType().Name
            if ($typeName -eq "ADIdentityNotFoundException") {
                Write-Result @{ ok = $false; error = "user_not_found" }
                exit 0
            }
            throw
        }
        $distinguishedName = [string]$user.DistinguishedName
        $suffix = ",{0}" -f $ouDn
        if (-not $distinguishedName.EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase)) {
            Write-Result @{ ok = $false; error = "outside_test_ou" }
            exit 0
        }
        if ($user.LockedOut -ne $true) {
            $users = New-Object System.Collections.Generic.List[object]
            $users.Add((Convert-User $user))
            Write-Result @{ ok = $true; ou = $ouDn; status = "unchanged"; users = $users }
            exit 0
        }
        Unlock-ADAccount -Server $Server -Identity $user.DistinguishedName -Confirm:$false
        $updated = Get-ADUser -Server $Server -Identity $username -Properties Department, LockedOut, DisplayName, DistinguishedName
        $users = New-Object System.Collections.Generic.List[object]
        $users.Add((Convert-User $updated))
        if ($updated.LockedOut -eq $true) {
            Write-Result @{ ok = $false; error = "unlock_verification_failed"; ou = $ouDn; users = $users }
            exit 1
        }
        Write-Result @{ ok = $true; ou = $ouDn; status = "unlocked"; users = $users }
        exit 0
    }

    Write-Result @{ ok = $false; error = "unsupported_op" }
    exit 0
} catch {
    Write-Result @{ ok = $false; error = "powershell_failed"; message = $_.Exception.Message }
    exit 1
}
