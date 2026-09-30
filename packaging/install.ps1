# PracticeGraph workstation install (pilot-grade, per-user).
#
# Zero-touch provisioning (FR-DEP-2 spirit): pass -ApiBaseUrl/-OrgId/-OrgToken
# to seed the config file. Empty parameters are omitted and never blank
# existing values; the token is never echoed. Data survives reinstall and
# uninstall by default (FR-DEP-3).
#
# Usage (silent):
#   powershell -ExecutionPolicy Bypass -File install.ps1 `
#     -ApiBaseUrl "https://practicegraph.example-intranet" `
#     -OrgId "acme-eng" -OrgToken "<org token>"

param(
    [string]$ApiBaseUrl = "",
    [string]$OrgId = "",
    [string]$OrgToken = "",
    [switch]$NoScheduledTask
)

$ErrorActionPreference = "Stop"
$source = $PSScriptRoot
$target = "$env:LOCALAPPDATA\Programs\PracticeGraph"
$dataDir = "$env:LOCALAPPDATA\PracticeGraph"
$taskName = "PracticeGraph Agent"

Write-Host "installing PracticeGraph to $target"
robocopy $source $target /MIR /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "file copy failed (robocopy exit $LASTEXITCODE)" }
# robocopy sets non-zero success codes; normalize so callers see success.
$global:LASTEXITCODE = 0

# Initialize the data directory and state store.
& "$target\practicegraph.cmd" init

# Seed connection config (merge; only non-empty parameters are written).
$configPath = Join-Path $dataDir "config.json"
$config = @{}
if (Test-Path $configPath) {
    try {
        $existing = Get-Content $configPath -Raw | ConvertFrom-Json
        $existing.PSObject.Properties | ForEach-Object { $config[$_.Name] = $_.Value }
    } catch {
        Write-Warning "existing config.json was unreadable; rewriting"
    }
}
if ($ApiBaseUrl) { $config["api_base_url"] = $ApiBaseUrl }
if ($OrgId) { $config["org_id"] = $OrgId }
# The org token is NEVER written to config.json in plaintext (NFR-SEC-1). Only
# the non-secret connection settings are seeded here; the token is stored
# DPAPI-protected below.
$config.Remove("org_token") | Out-Null
if ($config.Count -gt 0) {
    $config | ConvertTo-Json | Set-Content -Path $configPath -Encoding utf8
    Write-Host "config seeded: api_base_url=$($config['api_base_url']); org_id=$($config['org_id'])"
}

# Store the org token through the core so it lands in the DPAPI-protected,
# ACL-locked store. `config set-token` reads it from stdin and never echoes it.
if ($OrgToken) {
    $OrgToken | & "$target\practicegraph.cmd" config set-token | Out-Null
    Write-Host "org token stored (DPAPI-protected, masked)"
}

# Per-user agent loop at logon (windowless). The MSI + Windows service +
# native tray shell replace this in milestone M7 (FR-DEP-1, C-2).
if (-not $NoScheduledTask) {
    $action = New-ScheduledTaskAction -Execute "$target\runtime\pythonw.exe" `
        -Argument "-m practicegraph agent run"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "PracticeGraph endpoint agent (local analysis only)" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "scheduled task '$taskName' registered and started"
}

# practicegraph: protocol (per-user): toast click-through and the report's
# action links (timer verbs) dispatch to the shell. Closed verbs only (C-5).
$shellExe = Join-Path $target "practicegraph-shell.exe"
if (Test-Path $shellExe) {
    $protoKey = "HKCU:\Software\Classes\practicegraph"
    New-Item -Path "$protoKey\shell\open\command" -Force | Out-Null
    Set-ItemProperty -Path $protoKey -Name "(default)" -Value "URL:PracticeGraph"
    Set-ItemProperty -Path $protoKey -Name "URL Protocol" -Value ""
    Set-ItemProperty -Path "$protoKey\shell\open\command" -Name "(default)" `
        -Value "`"$shellExe`" url `"%1`""
    Write-Host "practicegraph: protocol registered (per-user)"
}

# Start Menu shortcuts: the native app window plus a one-glance status box
# (same targets as the MSI). Falls back to the report shim when the shell
# is not part of this bundle.
$startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\PracticeGraph"
New-Item -ItemType Directory -Force $startMenu | Out-Null
$shell = New-Object -ComObject WScript.Shell
if (Test-Path $shellExe) {
    $appLnk = $shell.CreateShortcut("$startMenu\PracticeGraph.lnk")
    $appLnk.TargetPath = $shellExe
    $appLnk.Arguments = "app"
    $appLnk.WorkingDirectory = $target
    $appLnk.IconLocation = "$target\practicegraph.ico"
    $appLnk.Description = "Open PracticeGraph"
    $appLnk.Save()
    $statusLnk = $shell.CreateShortcut("$startMenu\PracticeGraph status.lnk")
    $statusLnk.TargetPath = $shellExe
    $statusLnk.Arguments = "status --ui"
    $statusLnk.WorkingDirectory = $target
    $statusLnk.IconLocation = "$target\practicegraph.ico"
    $statusLnk.Description = "PracticeGraph install health"
    $statusLnk.Save()
} else {
    $reportLnk = $shell.CreateShortcut("$startMenu\PracticeGraph Report.lnk")
    $reportLnk.TargetPath = "$target\practicegraph.cmd"
    $reportLnk.Arguments = "report --open"
    $reportLnk.WorkingDirectory = $target
    $reportLnk.Description = "Open today's PracticeGraph report"
    $reportLnk.Save()
}

Write-Host ""
Write-Host "PracticeGraph installed."
Write-Host "  program:  $target"
Write-Host "  data:     $dataDir (preserved across reinstall/uninstall)"
Write-Host "  sharing:  OFF by default - nothing leaves this machine."
Write-Host "            Enable anonymous org aggregates: practicegraph consent on"
