# PracticeGraph SERVER install (native Windows, machine-wide). Run elevated.
#
# Copies the two stdlib-only packages the server needs onto the machine, writes
# an ACL-restricted environment file, and registers a Scheduled Task (SYSTEM or
# a service account, At Startup, restart on failure) that runs
# `pythonw -m practicegraph_server serve` via a small launcher which loads the
# environment file first (Scheduled Tasks cannot read env files natively).
# Secrets are never echoed. The server binds LOOPBACK ONLY; put a
# TLS-terminating proxy in front before exposing it (deploy\nginx, Caddyfile).
#
# Usage (elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File install-server.ps1 `
#     -PythonExe "C:\Program Files\Python312\python.exe" `
#     -OrgId "acme-eng" -OrgToken "<fresh secret>" `
#     -DashboardPassword "<different fresh secret>"
#
# Generate secrets:  python -c "import secrets; print(secrets.token_urlsafe(32))"
#             or:    openssl rand -base64 32

param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [Parameter(Mandatory = $true)][string]$OrgId,
    [string]$OrgToken = "", # deprecated shared fallback: counts as one source
    [string]$IngestTokensFile = "", # private JSON source-id -> credential map
    [string]$DashboardPassword = "",
    [int]$KThreshold = 5,     # NFR-PRV-3: org minimum is 5 (2 is pilot-only)
    [int]$RetentionDays = 400, # 0 = keep forever
    [int]$Port = 8321,
    [string]$ServiceAccount = "SYSTEM", # or a gMSA, e.g. DOMAIN\svc-pg$
    [string]$InstallDir = "$env:ProgramFiles\PracticeGraph-Server",
    [string]$DataDir = "$env:ProgramData\PracticeGraph-Server"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot           # deploy\ lives at the repo root
$taskName = "PracticeGraph Server"
$ingestTokens = if ($IngestTokensFile) { (Get-Content -LiteralPath $IngestTokensFile -Raw).Trim() } else { "" }
if (-not $ingestTokens -and -not $OrgToken) { throw "Provide -IngestTokensFile (recommended) or the legacy shared token" }
if ($KThreshold -lt 5) { throw "KThreshold must be at least 5" }
foreach ($value in @($OrgId, $OrgToken, $DashboardPassword)) {
    if ($value -match '[\r\n]') { throw "Configuration values must be single-line" }
}
if ($ingestTokens) {
    # JSON is compacted to one environment-file line; the server performs full
    # credential validation and rejects invalid/overlapping configuration.
    $ingestTokens = ($ingestTokens | ConvertFrom-Json | ConvertTo-Json -Compress)
}

# -- prerequisites -------------------------------------------------------------
if (-not (Test-Path $PythonExe)) { throw "PythonExe not found: $PythonExe" }
$pyVersion = & $PythonExe -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([Version]$pyVersion -lt [Version]"3.12") {
    throw "Python 3.12+ required (found $pyVersion at $PythonExe)"
}
$pythonw = Join-Path (Split-Path $PythonExe) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $PythonExe }  # console fallback

# -- program files: the server package + the stdlib-only core it imports --------
Write-Host "installing PracticeGraph server to $InstallDir"
New-Item -ItemType Directory -Force "$InstallDir\lib" | Out-Null
foreach ($package in @("practicegraph", "practicegraph_server")) {
    robocopy "$repoRoot\src\$package" "$InstallDir\lib\$package" /MIR /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "file copy failed (robocopy exit $LASTEXITCODE)" }
}
# robocopy sets non-zero success codes; normalize so callers see success.
$global:LASTEXITCODE = 0
Copy-Item -LiteralPath "$repoRoot\LICENSE", "$repoRoot\NOTICE" -Destination $InstallDir

# -- environment file (the only place secrets live; ACL-restricted) -------------
New-Item -ItemType Directory -Force $DataDir | Out-Null
# Restrict the directory BEFORE writing any secrets, including on first install.
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
    $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
if ($ServiceAccount -ne 'SYSTEM') {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($ServiceAccount, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $DataDir -AclObject $acl
$envFile = Join-Path $DataDir "server.env"
@(
    "# PracticeGraph server environment. Secrets live here ONLY - keep the ACL tight.",
    "# Loopback bind: NEVER change to a non-loopback address without a",
    "# TLS-terminating reverse proxy in front (deploy\nginx, deploy\Caddyfile).",
    "PG_SERVER_BIND=127.0.0.1",
    "PG_SERVER_PORT=$Port",
    "PG_SERVER_ORG_ID=$OrgId",
    "PG_SERVER_ORG_TOKEN=$OrgToken",
    "PG_SERVER_INGEST_TOKENS=$ingestTokens",
    "PG_SERVER_DASHBOARD_PASSWORD=$DashboardPassword",
    "PG_SERVER_K_THRESHOLD=$KThreshold",
    "PG_SERVER_RETENTION_DAYS=$RetentionDays",
    "PG_SERVER_DB=$DataDir\practicegraph-server.db",
    "PYTHONPATH=$InstallDir\lib"
) | Set-Content -Path $envFile -Encoding ascii
# Owner/admins + the service account only; break inheritance (SIDs, not names,
# so this survives localized Windows).
icacls $envFile /inheritance:r /grant:r "*S-1-5-18:(R)" "*S-1-5-32-544:(F)" | Out-Null
if ($ServiceAccount -ne "SYSTEM") {
    icacls $envFile /grant:r "${ServiceAccount}:(R)" | Out-Null
}
$dashState = if ($DashboardPassword) { "configured (masked)" } else { "NOT set - administrative reads disabled" }
Write-Host "environment written: org_id=$OrgId; k=$KThreshold; retention=$RetentionDays d; org token: configured (masked); dashboard password: $dashState"

# -- launcher: load server.env, then hand off to pythonw ------------------------
$launcher = Join-Path $InstallDir "run-server.ps1"
@"
# Generated by install-server.ps1 - loads the env file, then runs the server.
`$ErrorActionPreference = "Stop"
foreach (`$line in Get-Content "$envFile") {
    if (`$line -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') {
        Set-Item -Path "env:`$(`$Matches[1])" -Value `$Matches[2]
    }
}
`$process = Start-Process -FilePath "$pythonw" ``
    -ArgumentList "-m", "practicegraph_server", "serve" ``
    -PassThru -Wait -WindowStyle Hidden
exit `$process.ExitCode
"@ | Set-Content -Path $launcher -Encoding ascii

# -- scheduled task: At Startup, restart on failure ------------------------------
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $ServiceAccount `
    -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "PracticeGraph passive aggregate server (loopback)" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "scheduled task '$taskName' registered (account: $ServiceAccount) and started"

# -- health probe ---------------------------------------------------------------
$healthy = $false
foreach ($attempt in 1..10) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" `
            -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
}
if (-not $healthy) {
    Write-Warning "server did not answer /healthz on port $Port yet - check Task Scheduler history"
}

Write-Host ""
Write-Host "PracticeGraph server installed."
Write-Host "  program:  $InstallDir"
Write-Host "  data:     $DataDir (DB + env file; preserved by uninstall by default)"
Write-Host "  health:   http://127.0.0.1:$Port/healthz $(if ($healthy) { '- OK' } else { '- not responding yet' })"
Write-Host "  binding:  127.0.0.1 only. Front it with a TLS proxy before any"
Write-Host "            non-loopback exposure (see deploy\nginx, deploy\Caddyfile)."
