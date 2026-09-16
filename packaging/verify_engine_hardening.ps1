<#
Engine hardening gate (IP protection, 2026-07-21 audit).

The compiled -Engine binary is the artifact we SELL. A 2026-07-21 audit found
the un-hardened build compiled every module's DOCSTRINGS in verbatim -- 68
internal spec refs + algorithm narration, the author's own commentary handed
to a reverse-engineer. -OO/no_docstrings fixed it. This gate makes a
regression fail the build loudly: it unpacks the onefile payload and asserts
the compiled module carries no docstring/source leakage.

Runs the onefile once (it self-extracts to a temp dir), audits the real
compiled module (engine_entry.dll) with audit_engine_strings.py, and fails if
internal spec refs / docstring prose / source paths survive. Cleans up.

Usage: verify_engine_hardening.ps1 -EngineExe <path> [-PyExe <python for audit>]
#>
param(
    [Parameter(Mandatory = $true)][string]$EngineExe,
    [string]$PyExe
)
$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $PSCommandPath }
if (-not $PyExe) { $PyExe = Join-Path $here '..\.venv\Scripts\python.exe' }
$engine = (Resolve-Path $EngineExe).Path
$auditPy = Join-Path $here 'audit_engine_strings.py'

Write-Host "== engine hardening gate =="
Write-Host "engine: $engine"

# Snapshot existing onefile temp dirs so we identify OURS after the run.
$tempRoot = Join-Path $env:LOCALAPPDATA 'Temp'
$before = @(Get-ChildItem $tempRoot -Directory -Filter 'onefile_*' -EA SilentlyContinue |
            Select-Object -ExpandProperty FullName)

# Unpack: `--version` extracts the payload and exits fast.
& $engine --version | Out-Null

$mineList = @(Get-ChildItem $tempRoot -Directory -Filter 'onefile_*' -EA SilentlyContinue |
              Where-Object { $before -notcontains $_.FullName } |
              Sort-Object LastWriteTime -Descending)
# The dir may be reaped on exit; fall back to the newest onefile_* dir.
$unpack = if ($mineList) { $mineList[0].FullName } else {
    (Get-ChildItem $tempRoot -Directory -Filter 'onefile_*' -EA SilentlyContinue |
     Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $unpack) { throw "onefile did not unpack a payload dir under $tempRoot" }
$dll = Join-Path $unpack 'engine_entry.dll'
if (-not (Test-Path $dll)) { throw "no engine_entry.dll in the unpacked payload ($unpack)" }

try {
    $out = & $PyExe $auditPy $dll
    $out | Write-Host
    # audit_engine_strings prints a VERDICT line; leakage => fail the build.
    if ($out -match 'LEAKS DOCSTRINGS/SOURCE') {
        throw "engine leaks docstrings/source (see audit above) -- check the Nuitka -OO/no_docstrings flags"
    }
    Write-Host "engine hardening OK (no docstring/source leakage)" -ForegroundColor Green
} finally {
    if ($mineList) {
        foreach ($d in $mineList) { Remove-Item -Recurse -Force $d.FullName -EA SilentlyContinue }
    }
}
