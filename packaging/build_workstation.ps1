# Build the free, open-source per-user Windows application and optional MSI.
# -Engine optionally compiles Python for deployment convenience.
# Machine-wide personal stores and in-place shared installation refits are retired.

param(
    [string]$PythonVersion = "3.14.7",
    # Empty resolves from pyproject.toml below. A hardcoded default here once
    # went stale and quietly built an OLD version number onto NEW code — the
    # exact same-version-upgrade trap the 2026-07-19 MSI incident documents.
    # Pass -Version only to deliberately build something other than the tree.
    [string]$Version = "",
    [string]$RustToolchain = "stable-gnu",
    [string]$OutDir = "$PSScriptRoot\..\dist",
    [switch]$SkipShell,
    [switch]$Msi,
    [switch]$Engine,
    # Compatibility flag: explicitly rejected below.
    [switch]$Machine,
    # Authenticode signing (FR-DEP-5). -Sign signs the shipped executables and
    # the MSI with the cert whose thumbprint is given (default: the machine's
    # PracticeGraph self-signed publisher cert from New-SigningCert.ps1). SHA256
    # + RFC-3161 timestamp so signatures outlive the cert. See docs\CODE_SIGNING.md.
    [switch]$Sign,
    [string]$CertThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    # Azure Artifact Signing (formerly Trusted Signing): publicly trusted
    # Authenticode through the TeamLandi account, for builds that leave the
    # fleet. Needs the TrustedSigning PowerShell module and an Azure identity
    # holding "Artifact Signing Certificate Profile Signer" on the account.
    # Implies -Sign. Profile certs rotate every few days, so the build pins
    # the signer subject, not a thumbprint. See docs\CODE_SIGNING.md.
    [switch]$SignAzure,
    [string]$AzureEndpoint = "https://weu.codesigning.azure.net/",
    [string]$AzureAccount = "teamlandi-signing-cert",
    [string]$AzureProfile = "PracticeGraph",
    [string]$AzureSubject = "CN=TeamLandi OOD",
    # Compatibility flag: use a rebuilt per-user installer instead.
    [switch]$RefreshInPlace,
    [string]$InstallDir = "$env:ProgramFiles\PracticeGraph"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path "$PSScriptRoot\.."
$cacheDir = "$PSScriptRoot\cache"
$bundleDir = Join-Path $OutDir "PracticeGraph"
$resolvedOutput = [IO.Path]::GetFullPath($OutDir).TrimEnd('\') + '\'
$bundleDir = [IO.Path]::GetFullPath($bundleDir)
if (-not $bundleDir.StartsWith($resolvedOutput, [StringComparison]::OrdinalIgnoreCase)) {
    throw "bundle path escaped the requested output directory"
}

# The tree's own version is the default (see the param note).
if ($Machine) { throw "Machine-wide personal stores are retired. Build the per-user MSI." }
if (-not $Version) {
    $pyproject = Get-Content "$repoRoot\pyproject.toml" -Raw
    if ($pyproject -match '(?m)^version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "could not read version from pyproject.toml; pass -Version"
    }
    Write-Host "version $Version (from pyproject.toml)"
}

if ($RefreshInPlace) { throw "In-place shared refits are retired. Build and install the per-user MSI." }

# --- Authenticode signing helper (Set-AuthenticodeSignature: no Windows SDK /
# signtool needed). Resolves the signing cert once, then signs any file with
# SHA256 + a trusted timestamp. Every signature is verified before we continue,
# so an unsigned or mis-signed artifact fails the build instead of shipping.
$script:SigningCert = $null
if ($SignAzure) {
    $Sign = $true
    if (-not $PSBoundParameters.ContainsKey('TimestampUrl')) {
        $TimestampUrl = "http://timestamp.acs.microsoft.com"
    }
    if (-not (Get-Module -ListAvailable TrustedSigning)) {
        throw "-SignAzure: TrustedSigning module missing. Run: Install-Module TrustedSigning -Scope CurrentUser"
    }
    Import-Module TrustedSigning -ErrorAction Stop
    # Fail before the slow compile if nothing can authenticate: a service
    # principal in the environment, or an az login on this machine.
    if ($env:AZURE_CLIENT_ID) {
        $azIdentity = "service principal $env:AZURE_CLIENT_ID"
    } elseif (Get-Command az -ErrorAction SilentlyContinue) {
        $azIdentity = & az account show --query user.name -o tsv 2>$null
        if (-not $azIdentity) {
            throw "-SignAzure: no Azure identity. Run 'az login', or set AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET."
        }
    } else {
        $azIdentity = "default Azure credential (az CLI not found; another credential source must be available)"
    }
    Write-Host "signing with Azure Artifact Signing: $AzureAccount/$AzureProfile as $azIdentity" -ForegroundColor Cyan
} elseif ($Sign) {
    if ($CertThumbprint) {
        $script:SigningCert = Get-ChildItem Cert:\LocalMachine\My, Cert:\CurrentUser\My `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.Thumbprint -eq ($CertThumbprint -replace '\s','') } |
            Select-Object -First 1
    } else {
        # Default to the PracticeGraph publisher cert (code-signing EKU).
        $script:SigningCert = Get-ChildItem Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Subject -like "*TeamLandiLTD*" -and
                $_.EnhancedKeyUsageList.ObjectId -contains "1.3.6.1.5.5.7.3.3" -and
                $_.NotAfter -gt (Get-Date)
            } | Sort-Object NotAfter -Descending | Select-Object -First 1
    }
    if (-not $script:SigningCert) {
        throw "-Sign: no signing certificate found. Run packaging\sign\New-SigningCert.ps1 (elevated) first, or pass -CertThumbprint."
    }
    if (-not $script:SigningCert.HasPrivateKey) {
        throw "-Sign: certificate $($script:SigningCert.Thumbprint) has no private key on this machine (public-only import cannot sign)."
    }
    Write-Host "signing with: $($script:SigningCert.Subject) [$($script:SigningCert.Thumbprint)]" -ForegroundColor Cyan
}

function Invoke-Sign {
    param([string[]]$Path)
    if (-not $Sign) { return }
    foreach ($file in $Path) {
        if ($SignAzure) {
            Invoke-TrustedSigning -Endpoint $AzureEndpoint `
                -CodeSigningAccountName $AzureAccount `
                -CertificateProfileName $AzureProfile `
                -Files $file -FileDigest SHA256 `
                -TimestampRfc3161 $TimestampUrl -TimestampDigest SHA256 `
                -ErrorAction Stop
            $applied = Get-AuthenticodeSignature $file
            # Public trust: the chain must build to Valid right here, no GPO caveat.
            if ($applied.Status -ne 'Valid') {
                throw "Azure signing failed for $file : $($applied.Status) - $($applied.StatusMessage)"
            }
            if ($applied.SignerCertificate.Subject -notlike "$AzureSubject*") {
                throw "signing verification failed for $file : signer '$($applied.SignerCertificate.Subject)' is not $AzureSubject"
            }
            if (-not $applied.TimeStamperCertificate) {
                throw "signing produced no timestamp for $file (Artifact Signing certs expire within days)"
            }
            $script:SigningCert = $applied.SignerCertificate
            Write-Host "  $([IO.Path]::GetFileName($file)): signed via Azure Artifact Signing, timestamped, publicly trusted" -ForegroundColor DarkGreen
            continue
        }
        $result = Set-AuthenticodeSignature -FilePath $file `
            -Certificate $script:SigningCert `
            -HashAlgorithm SHA256 `
            -TimestampServer $TimestampUrl
        # 'Valid' means this build machine also trusts our root. A self-signed
        # publisher cert normally is NOT in this machine's Trusted Root, so a
        # correctly-applied signature reports 'UnknownError' with the specific
        # "terminated in a root certificate which is not trusted" message. That
        # is a LOCAL-TRUST state, not a signing failure - the signature and its
        # timestamp are attached correctly, and target machines that receive
        # the cert via GPO (docs\CODE_SIGNING.md) verify it as Valid. Accept
        # that case; reject any real failure (bad key, no timestamp, tamper).
        $untrustedRoot = $result.Status -eq 'UnknownError' -and
            $result.StatusMessage -match 'not trusted by the trust provider'
        if ($result.Status -ne 'Valid' -and -not $untrustedRoot) {
            throw "signing failed for $file : $($result.Status) - $($result.StatusMessage)"
        }
        # Confirm the bytes actually carry our signer + a timestamp, independent
        # of local root trust, so we never ship an unsigned file thinking it signed.
        $applied = Get-AuthenticodeSignature $file
        if ($applied.SignerCertificate.Thumbprint -ne $script:SigningCert.Thumbprint) {
            throw "signing verification failed for $file : signer not our cert"
        }
        if (-not $applied.TimeStamperCertificate) {
            throw "signing produced no timestamp for $file (would expire with the cert)"
        }
        $note = if ($untrustedRoot) { "signed, timestamped (trust via GPO on targets)" }
                else { "signed, timestamped, trusted locally" }
        Write-Host "  $([IO.Path]::GetFileName($file)): $note" -ForegroundColor DarkGreen
    }
}

New-Item -ItemType Directory -Force $cacheDir | Out-Null
if (Test-Path $bundleDir) { Remove-Item -LiteralPath $bundleDir -Recurse -Force }
New-Item -ItemType Directory -Force $bundleDir | Out-Null

if ($Engine) {
    # Compiled engine: one exe replaces runtime\ + app\ (no separate Python runtime).
    $venvPython = "$repoRoot\.venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "-Engine needs the repo venv with nuitka: python -m venv .venv; .venv\Scripts\pip install nuitka zstandard"
    }
    $env:NUITKA_CACHE_DIR = "$cacheDir\nuitka"   # short real path: MinGW breaks under long/virtualized dirs
    # Nuitka 4.2 compiles with a Zig toolchain it downloads into a private pip
    # space under the cache, and on Windows it takes the newest ziglang. With
    # ziglang 0.16.0, `zig cc -flto` fails to link (dozens of undefined libc
    # symbols, reproducible on a three-line C file); 0.15.2 links fine. Seed
    # the private space with the pinned version before Nuitka looks, so the
    # engine keeps LTO. Nuitka reuses whatever ziglang it finds there.
    $zigPin = "0.15.2"
    $privateBase = & $venvPython -c "from nuitka.utils.PrivatePipSpace import getPrivatePipBaseFolder as f; print(f())"
    if ($LASTEXITCODE -ne 0 -or -not $privateBase) { throw "could not resolve Nuitka's private pip space" }
    $privateSite = Join-Path "$privateBase".Trim() "Lib\site-packages"
    $zigInfo = Get-ChildItem "$privateSite\ziglang-*.dist-info" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $zigInfo -or $zigInfo.Name -ne "ziglang-$zigPin.dist-info") {
        Write-Host "pinning Nuitka's private ziglang to $zigPin (LTO link breaks with newer)" -ForegroundColor Cyan
        Get-ChildItem "$privateSite\ziglang*" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
        New-Item -ItemType Directory -Force $privateSite | Out-Null
        & $venvPython -m pip install --quiet --target $privateSite "ziglang==$zigPin"
        if ($LASTEXITCODE -ne 0) { throw "could not install ziglang==$zigPin into $privateSite" }
    }
    # Optional compilation changes delivery format; source remains Apache-2.0.
    & $venvPython -OO -m nuitka --onefile --assume-yes-for-downloads `
        --python-flag=-OO `
        --python-flag=no_docstrings `
        --lto=yes `
        --output-filename=practicegraph-engine.exe `
        --output-dir="$repoRoot\build\engine" `
        --include-package=practicegraph `
        --include-package=practicegraph_server `
        --include-package=tzdata `
        --include-package-data=tzdata `
        --include-package=tzlocal `
        --include-package=truststore `
        --include-package=cryptography `
        --include-package=cffi `
        --include-module=_cffi_backend `
        --product-name=PracticeGraph `
        --product-version=$Version `
        --file-description="PracticeGraph engine" `
        --onefile-tempdir-spec="{CACHE_DIR}/PracticeGraph/engine/{VERSION}" `
        --remove-output `
        "$repoRoot\packaging\engine_entry.py"
    if ($LASTEXITCODE -ne 0) { throw "nuitka engine build failed" }
    Copy-Item "$repoRoot\build\engine\practicegraph-engine.exe" $bundleDir
    # Sign before hashing so BUILD-INFO records the signed binary's hash.
    Invoke-Sign -Path "$bundleDir\practicegraph-engine.exe"
    $engineHash = (Get-FileHash -Algorithm SHA256 "$bundleDir\practicegraph-engine.exe").Hash

    # The CLI shim keeps the same entry point; it just delegates to the engine.
    @"
@echo off
"%~dp0practicegraph-engine.exe" %*
"@ | Set-Content -Path "$bundleDir\practicegraph.cmd" -Encoding ascii

    $iconPython = $venvPython
    $runtimeLine = "engine: practicegraph-engine.exe (Nuitka onefile, sha256 $engineHash) - no separate Python runtime shipped"
} else {
    $zipName = "python-$PythonVersion-embed-amd64.zip"
    $zipUrl = "https://www.python.org/ftp/python/$PythonVersion/$zipName"
    $zipPath = Join-Path $cacheDir $zipName

    if (-not (Test-Path $zipPath)) {
        Write-Host "downloading $zipUrl"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    }

    # The interpreter that will run as a background agent on other people's
    # machines. Until 2026-07-26 this hash was COMPUTED AND PRINTED but never
    # checked, so a tampered download - or a poisoned build cache, which is the
    # likelier path since the file is reused across builds - was recorded
    # faithfully and shipped. Now a mismatch stops the build.
    #
    # Pin the SHA-256 published by python.org, independently of the download.
    # https://www.python.org/downloads/release/python-3147/
    $expected = @{
        "3.14.7" = @{
            sha256 = "D297E5FF019966817AD8502465176139F2D3D840FA4ED84B13BED399A6AB1F15"
        }
    }
    if (-not $expected.ContainsKey($PythonVersion)) {
        throw ("no pinned hash for Python $PythonVersion - add one to " +
               "build_workstation.ps1 after checking python.org's published SHA-256. " +
               "Shipping an unverified interpreter is not an option.")
    }
    $zipHash = (Get-FileHash -Algorithm SHA256 $zipPath).Hash
    if ($zipHash -ne $expected[$PythonVersion].sha256) {
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        throw ("python embeddable FAILED verification - refusing to build.`n" +
               "  expected sha256 $($expected[$PythonVersion].sha256)`n" +
               "  got      sha256 $zipHash`n" +
               "The cached copy has been deleted; re-run to download again.")
    }
    Write-Host "python embeddable sha256: $zipHash (verified)"

    New-Item -ItemType Directory -Force "$bundleDir\runtime" | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath "$bundleDir\runtime"

    # Make the app package importable by the embedded runtime.
    $pthFile = Get-ChildItem "$bundleDir\runtime\python*._pth" | Select-Object -First 1
    Add-Content -Path $pthFile.FullName -Value "..\app"

    New-Item -ItemType Directory -Force "$bundleDir\app" | Out-Null
    Copy-Item -Recurse "$repoRoot\src\practicegraph" "$bundleDir\app\practicegraph"
    $buildPython = "$repoRoot\.venv\Scripts\python.exe"
    if (-not (Test-Path $buildPython)) {
        throw "readable bundle needs .venv with project dependencies installed"
    }
    # The only third-party Python that ships. Version pins alone still trust
    # whatever PyPI serves for that version, so these install --require-hashes
    # from a lockfile: a re-uploaded or substituted artifact fails the install
    # instead of landing inside a background agent.
    $reqFile = "$PSScriptRoot\shipped-requirements.txt"
    if (-not (Test-Path $reqFile)) { throw "missing $reqFile" }
    & $buildPython -m pip install --no-compile --no-deps --require-hashes `
        --only-binary=:all: --platform win_amd64 --implementation cp --python-version $PythonVersion `
        --target "$bundleDir\app" -r $reqFile
    if ($LASTEXITCODE -ne 0) { throw "runtime dependency staging failed" }
    Get-ChildItem -Recurse "$bundleDir\app" -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force

    @"
@echo off
"%~dp0runtime\python.exe" -B -m practicegraph %*
"@ | Set-Content -Path "$bundleDir\practicegraph.cmd" -Encoding ascii

    $iconPython = "$bundleDir\runtime\python.exe"
    $runtimeLine = "python embeddable: $PythonVersion (sha256 $zipHash)"
}

# Interactive dashboard assets (built from ui/ via `npm run build`).
Copy-Item -LiteralPath "$repoRoot\LICENSE", "$repoRoot\NOTICE" -Destination $bundleDir
if (Test-Path "$repoRoot\webui\index.html") {
    Copy-Item -Recurse "$repoRoot\webui" "$bundleDir\webui"
}

# Product icons (deterministic, stdlib-only generator): base + the
# green-dot presence variant the tray shows while logs are changing.
& $iconPython "$PSScriptRoot\make_icon.py" "$bundleDir\practicegraph.ico"
& $iconPython "$PSScriptRoot\make_icon.py" "$bundleDir\practicegraph-working.ico" --working

# Native shell: service host + tray + toasts (C-2).
$shellVersion = "skipped"
if (-not $SkipShell) {
    # The crate version ships inside the binary (status surfaces, ping
    # replies). It once sat at 0.1.13 while the tree shipped 0.1.15 — and a
    # shell-side check that compared engine-reported versions against its own
    # deleted every freshly published endpoint, breaking each new install on
    # first launch (field report 2026-07-28). The gate is gone; this guard
    # keeps the version honest so it can never silently drift again.
    $cargoToml = Get-Content "$repoRoot\shell\Cargo.toml" -Raw
    if ($cargoToml -notmatch "(?m)^version = `"$([regex]::Escape($Version))`"") {
        throw "shell\Cargo.toml version does not match $Version - update it (the crate version ships in the binary)"
    }
    # Run from shell\ so cargo discovers shell\.cargo\config.toml (the
    # raw-dylib dlltool pin): config discovery walks the CWD, not the
    # --manifest-path directory.
    Push-Location "$repoRoot\shell"
    try {
        cargo +$RustToolchain build --release
        if ($LASTEXITCODE -ne 0) { throw "shell build failed" }
    } finally {
        Pop-Location
    }
    Copy-Item "$repoRoot\shell\target\release\practicegraph-shell.exe" $bundleDir
    # The gnu-built shell links WebView2Loader.dll at load time (native
    # dashboard window); it must sit next to the exe in every bundle. The
    # authoritative copy is the webview2-com-sys crate's build output
    # (build\webview2-com-sys-*\out\x64\); the top-level target\release\ copy is
    # NOT reliably present on a fresh checkout (it broke CI). Prefer the crate
    # out-dir, fall back to the top-level, and fail loudly if neither exists.
    $webviewDll = @(
        Get-ChildItem "$repoRoot\shell\target\release\build\webview2-com-sys-*\out\x64\WebView2Loader.dll" `
            -ErrorAction SilentlyContinue
        Get-Item "$repoRoot\shell\target\release\WebView2Loader.dll" `
            -ErrorAction SilentlyContinue
    ) | Select-Object -First 1
    if (-not $webviewDll) {
        throw "WebView2Loader.dll not found under shell\target\release (built the shell?)"
    }
    Copy-Item $webviewDll.FullName (Join-Path $bundleDir "WebView2Loader.dll")
    # Sign our shell (not WebView2Loader.dll - that ships already signed by
    # Microsoft; re-signing would only strip that trusted signature).
    Invoke-Sign -Path "$bundleDir\practicegraph-shell.exe"
    $shellVersion = "practicegraph-shell $Version (verified Cargo manifest)"
}

Copy-Item "$PSScriptRoot\install.ps1" $bundleDir
Copy-Item "$PSScriptRoot\uninstall.ps1" $bundleDir
& "$repoRoot\.venv\Scripts\python.exe" "$PSScriptRoot\third_party_notices.py" `
    --toolchain $RustToolchain --out "$bundleDir\THIRD_PARTY_NOTICES.txt"
if ($LASTEXITCODE -ne 0) { throw "third-party notice generation failed" }

$appVersion = (& "$bundleDir\practicegraph.cmd" --version)
$sourceCommit = (git -C $repoRoot rev-parse HEAD)
$sourceDirty = [bool](git -C $repoRoot status --porcelain)
$uiIdentity = (Get-FileHash -LiteralPath "$bundleDir\webui\index.html" -Algorithm SHA256).Hash
$signingLine = if ($SignAzure) {
    "signing: Authenticode-signed (SHA256, timestamped) via Azure Artifact Signing " +
    "($AzureAccount/$AzureProfile) by $($script:SigningCert.Subject). " +
    "Publicly trusted chain; no Trusted Publishers push needed."
} elseif ($Sign) {
    "signing: Authenticode-signed (SHA256, timestamped) by " +
    "$($script:SigningCert.Subject); thumbprint $($script:SigningCert.Thumbprint). " +
    "Publisher trust requires the cert in Trusted Publishers (docs\CODE_SIGNING.md)."
} else {
    "signing: UNSIGNED PILOT BUILD - do not distribute beyond the pilot ring " +
    "(pass -Sign with a provisioned cert to sign; FR-DEP-5)"
}
@"
PracticeGraph workstation bundle
app: $appVersion
shell: $shellVersion
source commit: $sourceCommit
source has uncommitted changes: $sourceDirty
webui index SHA256: $uiIdentity
$runtimeLine
$signingLine
"@ | Set-Content -Path "$bundleDir\BUILD-INFO.txt" -Encoding utf8

if ($Msi) {
    if ($SkipShell) { throw "the MSI requires the shell; do not combine -Msi with -SkipShell" }
    # Pin the extensions to the installed wix version (NFR-SEC-4).
    $wixVersion = ((wix --version) -split '\+')[0]
    wix extension add -g "WixToolset.Util.wixext/$wixVersion" | Out-Null
    wix extension add -g "WixToolset.UI.wixext/$wixVersion" | Out-Null
    # Per-user is the default deliverable; the machine flavour carries a
    # suffix so the two are never confused on a download page.
    $flavour = if ($Machine) { "-machine" } else { "" }
    $msiName = if ($Engine) { "PracticeGraph-$Version-engine$flavour.msi" }
               else { "PracticeGraph-$Version$flavour.msi" }
    $msiPath = Join-Path $OutDir $msiName
    $wixArgs = @(
        "build", "$PSScriptRoot\msi\Package.wxs",
        "-d", "StageDir=$bundleDir", "-d", "Version=$Version",
        "-ext", "WixToolset.Util.wixext/$wixVersion",
        "-ext", "WixToolset.UI.wixext/$wixVersion",
        "-arch", "x64", "-o", $msiPath
    )
    if ($Engine) { $wixArgs += @("-d", "EngineBundle=1") }
    if ($Machine) { $wixArgs += @("-d", "MachineInstall=1") }
    wix @wixArgs
    if ($LASTEXITCODE -ne 0) { throw "wix build failed" }
    Write-Host "validating MSI (ICE)"
    # ICE38/ICE64 fire on every component in a perUser package, because both
    # assume a per-MACHINE package that happens to drop files in a profile -
    # the case where MSI genuinely cannot tell which user a file belongs to,
    # so a repair for a second user silently skips it.
    #
    # That premise does not hold here. The whole package is Scope="perUser"
    # and non-advertised: it installs into ONE profile, for the person who ran
    # it, and a second user installs their own copy. There is no cross-user
    # state for MSI to get wrong. Suppressed only for this flavour - the
    # machine build below still validates against the full ICE set, where
    # those checks do mean something.
    $iceArgs = if ($Machine) { @() } else { @("-sice", "ICE38", "-sice", "ICE64") }
    wix msi validate $msiPath @iceArgs
    if ($LASTEXITCODE -ne 0) { throw "MSI validation failed" }
    # Payload gate: the MSI's File table must carry exactly the staged bundle
    # (minus the two no-admin fallback scripts, which ship outside the MSI).
    # A silent packaging gap bricked a field upgrade (2026-07-19: a machine
    # lost runtime\python312.zip and the embedded Python could no longer
    # boot), so a mismatch fails the build loudly. Runs with the STAGED
    # runtime python, which doubles as a boots-at-all smoke test of the
    # bundle's interpreter + stdlib. (Engine bundles carry no runtime\ —
    # the compiled exe replaces it — so the gate is runtime-flavor only.)
    if (-not $Engine) {
        Write-Host "verifying MSI payload against the staged bundle"
        & "$bundleDir\runtime\python.exe" "$PSScriptRoot\verify_msi_payload.py" $msiPath $bundleDir
        if ($LASTEXITCODE -ne 0) { throw "MSI payload verification failed" }
        # End-to-end runtime gate: exercise the per-user startup chain against
        # the staged bundle (self-init -> ui server -> token endpoint -> view)
        # in a throwaway data dir. Every field failure this month passed the
        # old build because nothing exercised this chain; now the build does.
        Write-Host "verifying the end-to-end runtime chain"
        & powershell -NoProfile -ExecutionPolicy Bypass `
            -File "$PSScriptRoot\tests\verify_install.ps1" -BundleDir $bundleDir
        if ($LASTEXITCODE -ne 0) { throw "end-to-end runtime verification failed" }
    }
    # Sign the finished MSI last (after ICE validation, before hashing) so the
    # published sha256 is the signed installer's and the package itself carries
    # the publisher identity, not just the exes inside it.
    Invoke-Sign -Path $msiPath
    $msiHash = (Get-FileHash -Algorithm SHA256 $msiPath).Hash
    Add-Content -Path "$bundleDir\BUILD-INFO.txt" -Value "msi: $msiPath (sha256 $msiHash)"
    Write-Host "msi: $msiPath"
    Write-Host "msi sha256: $msiHash"

    # SBOM alongside the installer (2026-07-26). It answers the question you
    # only ask AFTER something goes wrong - "was that in the build we shipped
    # in March?" - which cannot be answered later if nobody wrote it down at
    # the time. Built from the extracted MSI payload: a
    # lockfile says what should have been installed, the bundle is what
    # actually will be, and those agree right up until it matters.
    $sbomPath = Join-Path $OutDir "sbom-$Version$flavour.json"
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    & "$repoRoot\.venv\Scripts\python.exe" "$PSScriptRoot\sbom.py" `
        --bundle $bundleDir --msi $msiPath --version $Version --out $sbomPath --timestamp $stamp
    if ($LASTEXITCODE -ne 0) { throw "sbom generation failed" }

    # Installs and update checks come from the public app repo's releases.
    # The client only trusts a signed update.json hosted on that repo's release.
    Write-Host ""
    Write-Host "=== PUBLISH TO TeamLandiLTD/practicegraph-app ===" -ForegroundColor Cyan
    Write-Host "1. sign the manifest with the offline key (never in this repo):"
    Write-Host "   uv run python packaging\sign_release.py --msi `"$msiPath`" --version $Version --key <release-signing.key>"
    Write-Host "2. publish the release with the MSI, its manifest and the SBOM:"
    Write-Host "   gh release create v$Version -R TeamLandiLTD/practicegraph-app -t `"PracticeGraph $Version`" `"$msiPath`" `"$(Join-Path $OutDir 'update.json')`" `"$sbomPath`""
    Write-Host "   installed clients check /releases/latest/download/update.json once a day and toast once per version."
}

Write-Host ""
if ($SignAzure) {
    Write-Host "=== SIGNED BUILD (Azure Artifact Signing) ===" -ForegroundColor Green
    Write-Host "publisher: $($script:SigningCert.Subject)"
    Write-Host "publicly trusted: no Trusted Publishers push needed (docs\CODE_SIGNING.md)"
} elseif ($Sign) {
    Write-Host "=== SIGNED BUILD ===" -ForegroundColor Green
    Write-Host "publisher: $($script:SigningCert.Subject)"
    Write-Host "trust the fleet: push the public .cer to Trusted Publishers (docs\CODE_SIGNING.md)"
} else {
    Write-Host "=== UNSIGNED PILOT BUILD ===" -ForegroundColor Yellow
}
Write-Host "bundle: $bundleDir"
