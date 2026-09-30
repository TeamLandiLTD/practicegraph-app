# PracticeGraph workstation uninstall.
# Data is preserved by default; pass -RemoveData for explicit removal
# (FR-DEP-3 semantics).

param(
    [switch]$RemoveData
)

$ErrorActionPreference = "Continue"
$target = "$env:LOCALAPPDATA\Programs\PracticeGraph"
$dataDir = "$env:LOCALAPPDATA\PracticeGraph"
$taskName = "PracticeGraph Agent"
$startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\PracticeGraph"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "scheduled task removed"
}

if (Test-Path $startMenu) {
    Remove-Item -Recurse -Force $startMenu
    Write-Host "start menu entries removed"
}

if (Test-Path $target) {
    try {
        Remove-Item -Recurse -Force $target -ErrorAction Stop
        Write-Host "program files removed"
    } catch {
        Write-Warning "could not fully remove $target (files in use); remove it after closing running agents"
    }
}

if ($RemoveData) {
    if (Test-Path $dataDir) {
        Remove-Item -Recurse -Force $dataDir
        Write-Host "data directory removed ($dataDir)"
    }
} else {
    Write-Host "data preserved at $dataDir (pass -RemoveData to delete)"
}
