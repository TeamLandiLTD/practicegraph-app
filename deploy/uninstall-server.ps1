# PracticeGraph SERVER uninstall (native Windows). Run elevated.
#
# Stops and removes the scheduled task and the program files. The data
# directory (SQLite DB + server.env) is PRESERVED by default so an upgrade or
# reinstall keeps history and credentials; pass -RemoveData to wipe it.
#
# Usage (elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File uninstall-server.ps1 [-RemoveData]

param(
    [string]$InstallDir = "$env:ProgramFiles\PracticeGraph-Server",
    [string]$DataDir = "$env:ProgramData\PracticeGraph-Server",
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"
$taskName = "PracticeGraph Server"

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop } catch {}
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "scheduled task '$taskName' stopped and removed"
} else {
    Write-Host "scheduled task '$taskName' not found (already removed)"
}

if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
    Write-Host "program files removed: $InstallDir"
}

if ($RemoveData) {
    if (Test-Path $DataDir) {
        Remove-Item -Recurse -Force $DataDir
        Write-Host "data removed: $DataDir (DB and env file are gone)"
    }
} else {
    Write-Host "data preserved: $DataDir"
    Write-Host "  (SQLite DB + server.env with credentials; delete manually or"
    Write-Host "   rerun with -RemoveData if this machine is being retired)"
}
