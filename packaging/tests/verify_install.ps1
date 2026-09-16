<#
Smoke the shipped per-user runtime and dashboard using synthetic logs.
Does not install an MSI, register a service, or test the native window.
#>
[CmdletBinding()]
param([string]$BundleDir, [int]$EndpointTimeoutSec = 45)
$ErrorActionPreference = 'Stop'
if (-not $BundleDir) { $BundleDir = Join-Path $PSScriptRoot '..\..\dist\PracticeGraph' }
$bundle = (Resolve-Path -LiteralPath $BundleDir).Path
$python = Join-Path $bundle 'runtime\python.exe'
$dataDir = Join-Path ([IO.Path]::GetTempPath()) ('pg-verify-' + [IO.Path]::GetRandomFileName())
$uiProcess = $null
$names = @('PRACTICEGRAPH_DATA_DIR', 'PRACTICEGRAPH_SCAN_PROFILES',
           'PRACTICEGRAPH_CLAUDE_HOME', 'PRACTICEGRAPH_CODEX_HOME')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PRACTICEGRAPH_DATA_DIR = $dataDir
    Remove-Item Env:\PRACTICEGRAPH_SCAN_PROFILES -ErrorAction SilentlyContinue
    $fixtureRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\tests\fixtures'))
    $env:PRACTICEGRAPH_CLAUDE_HOME = Join-Path $fixtureRoot 'claude_code'
    $env:PRACTICEGRAPH_CODEX_HOME = Join-Path $fixtureRoot 'codex'
    foreach ($rel in 'runtime\python.exe', 'app\practicegraph\__init__.py',
                     'webui\index.html', 'practicegraph-shell.exe', 'THIRD_PARTY_NOTICES.txt') {
        if (-not (Test-Path -LiteralPath (Join-Path $bundle $rel))) { throw "missing $rel" }
    }
    & $python -B -c 'import practicegraph, encodings, sqlite3, ssl, sys; print(sys.version); print(practicegraph.__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'shipped interpreter import failed' }
    # Pass source on stdin: Windows PowerShell 5.1 strips embedded quotes from
    # native -c arguments, which otherwise breaks the bytes literals below.
    @'
from practicegraph.catalog_crypto import AESGCM, Ed25519PrivateKey
import os

key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)
cipher = AESGCM(key)
message = b"catalog smoke"
context = b"training"
assert cipher.decrypt(nonce, cipher.encrypt(nonce, message, context), context) == message
signer = Ed25519PrivateKey.generate()
signer.public_key().verify(signer.sign(message), message)
'@ | & $python -B -
    if ($LASTEXITCODE -ne 0) { throw 'shipped catalog cryptography failed' }
    & $python -B -m practicegraph init
    if ($LASTEXITCODE -ne 0) { throw 'personal initialization failed' }
    & $python -B -m practicegraph agent run --once
    if ($LASTEXITCODE -ne 0) { throw 'personal agent tick failed' }
    foreach ($name in 'state.db', 'status.json') {
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir $name))) { throw "tick did not create $name" }
    }
    $start = @{
        FilePath = $python; ArgumentList = @('-B', '-m', 'practicegraph', 'ui', 'serve')
        WindowStyle = 'Hidden'; PassThru = $true; WorkingDirectory = $bundle
        RedirectStandardOutput = (Join-Path $dataDir 'ui-stdout.txt')
        RedirectStandardError = (Join-Path $dataDir 'ui-stderr.txt')
    }
    $uiProcess = Start-Process @start
    $deadline = (Get-Date).AddSeconds($EndpointTimeoutSec)
    $endpoint = $null
    while ((Get-Date) -lt $deadline) {
        if ($uiProcess.HasExited) { throw 'personal UI process exited before becoming ready' }
        $state = Join-Path $dataDir 'ui.json'
        if (Test-Path -LiteralPath $state) {
            try {
                $candidate = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
                $ping = Invoke-RestMethod "http://127.0.0.1:$($candidate.port)/api/ping?token=$($candidate.token)" -TimeoutSec 3
                if ($ping.ok) { $endpoint = $candidate; break }
            } catch { }
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $endpoint) { throw 'personal dashboard did not become ready' }
    $code = 0
    try {
        Invoke-WebRequest "http://127.0.0.1:$($endpoint.port)/api/view?token=invalid-test-token" -TimeoutSec 5 -UseBasicParsing | Out-Null
    } catch { $code = [int]$_.Exception.Response.StatusCode }
    if ($code -ne 403) { throw "wrong token returned $code instead of 403" }
    $view = Invoke-RestMethod "http://127.0.0.1:$($endpoint.port)/api/view?token=$($endpoint.token)" -TimeoutSec 30
    foreach ($key in 'advisor', 'skills', 'news', 'observation', 'app_version') {
        if ($null -eq $view.$key) { throw "view missing $key" }
    }
    if (-not $view.observation.caveat) { throw 'observation missing its qualification' }
    Write-Host 'PASS: shipped runtime, personal initialization, tick, UI token rejection, dashboard view'
} finally {
    if ($uiProcess -and -not $uiProcess.HasExited) { Stop-Process -Id $uiProcess.Id -Force -ErrorAction SilentlyContinue }
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    $resolvedData = [IO.Path]::GetFullPath($dataDir)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedData.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'verification cleanup escaped the temporary directory'
    }
    Remove-Item -LiteralPath $resolvedData -Recurse -Force -ErrorAction SilentlyContinue
}
