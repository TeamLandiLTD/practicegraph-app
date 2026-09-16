# Provision a code-signing certificate for PracticeGraph (self-signed, org-internal).
#
# This is the FREE signing path for a fleet you administer: a self-signed
# code-signing certificate whose PUBLIC half you push to Trusted Publishers via
# Group Policy / Intune (see docs/CODE_SIGNING.md). On managed machines that
# trust the cert, signed installs show "TeamLandiLTD" as a verified publisher
# and skip the "Unknown publisher" wall — at zero cost. It is NOT publicly
# trusted: machines outside your GPO will not trust it (that needs a paid CA).
#
# Run ONCE, elevated (LocalMachine store). Idempotent: re-running finds the
# existing cert by subject and re-exports the public .cer without minting a new
# key. The PRIVATE key never leaves the machine and is never exported here.
#
#   powershell -ExecutionPolicy Bypass -File packaging\sign\New-SigningCert.ps1
#
# Outputs:
#   - a code-signing cert in Cert:\LocalMachine\My (private key stays here)
#   - packaging\sign\PracticeGraph-Publisher.cer  (PUBLIC cert, safe to share/push)
#   - prints the thumbprint to pass to build_workstation.ps1 -Sign -CertThumbprint

param(
    [string]$Subject = "CN=TeamLandiLTD, O=TeamLandiLTD, C=GB",
    # Friendly name the publisher shows as; keep stable so the fleet trusts one identity.
    [string]$FriendlyName = "PracticeGraph Publisher (TeamLandiLTD)",
    [int]$ValidYears = 5,
    [string]$PublicCerOut = "$PSScriptRoot\PracticeGraph-Publisher.cer"
)

$ErrorActionPreference = "Stop"

# LocalMachine\My requires elevation; fail early with a clear message rather
# than a cryptic access error deep in New-SelfSignedCertificate.
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
    throw "Run elevated: this writes to Cert:\LocalMachine\My. Right-click PowerShell > Run as Administrator."
}

# Reuse an existing cert with this subject if present (don't mint a second key
# every build machine reboot). Match on Subject AND the code-signing EKU.
$codeSignEku = "1.3.6.1.5.5.7.3.3"

# A cert is REUSABLE only if it can ACTUALLY sign. Inspecting the provider is
# not enough: a cert can report HasPrivateKey=$true yet have a missing key
# container ("Keyset does not exist") or a CNG key that Set-AuthenticodeSignature
# cannot drive ("No provider specified"). The only test that can't be fooled by
# metadata is to attempt a real signature on a scratch file. No timestamp here -
# we only care whether the key drives the signing engine, not local root trust.
function Test-CanSign {
    param($Certificate)
    if (-not $Certificate.HasPrivateKey) { return $false }
    $probe = Join-Path $env:TEMP ("pg-signprobe-" + $Certificate.Thumbprint + ".ps1")
    try {
        Set-Content -Path $probe -Value '# signability probe' -Encoding ascii
        $r = Set-AuthenticodeSignature -FilePath $probe -Certificate $Certificate `
            -HashAlgorithm SHA256 -ErrorAction Stop
        # 'Valid' or the specific untrusted-root state both mean the KEY signed;
        # any other status (missing keyset, no provider) means it cannot.
        return ($r.Status -eq 'Valid') -or
               ($r.Status -eq 'UnknownError' -and
                $r.StatusMessage -match 'not trusted by the trust provider')
    } catch {
        return $false
    } finally {
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    }
}

$subjectCodeSign = Get-ChildItem Cert:\LocalMachine\My |
    Where-Object {
        $_.Subject -eq $Subject -and
        $_.EnhancedKeyUsageList.ObjectId -contains $codeSignEku
    }
$existing = $subjectCodeSign |
    Where-Object { Test-CanSign $_ } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1

# Retire any subject-matching code-signing cert that CANNOT sign (broken key
# container, CNG-backed) so it never lingers for the build's auto-select. Uses
# X509Store.Remove on an open ReadWrite store - the reliable delete (Remove-Item
# on the Cert: drive can silently no-op).
$stale = $subjectCodeSign | Where-Object {
    ($null -eq $existing -or $_.Thumbprint -ne $existing.Thumbprint) -and
    -not (Test-CanSign $_)
}
if ($stale) {
    $machineStore = New-Object System.Security.Cryptography.X509Certificates.X509Store(
        "My", "LocalMachine")
    $machineStore.Open("ReadWrite")
    foreach ($old in $stale) {
        Write-Host "Removing an unusable signing cert (cannot sign): $($old.Thumbprint)" -ForegroundColor Yellow
        $machineStore.Remove($old)
    }
    $machineStore.Close()
}

if ($existing) {
    Write-Host "Reusing existing signing cert (verified it can sign)." -ForegroundColor Green
    $cert = $existing
} else {
    Write-Host "Minting a new self-signed code-signing certificate..."
    # Force a LEGACY CSP for the private key ("-Provider"), not the CNG/KSP
    # default. Set-AuthenticodeSignature drives a CSP-backed key reliably; a
    # CNG-stored key fails at sign time with "No provider was specified for the
    # store or object". RSA-3072 lives in the Enhanced RSA and AES CSP.
    $cert = New-SelfSignedCertificate `
        -Subject $Subject `
        -FriendlyName $FriendlyName `
        -Type CodeSigningCert `
        -KeyUsage DigitalSignature `
        -KeyAlgorithm RSA `
        -KeyLength 3072 `
        -HashAlgorithm SHA256 `
        -Provider "Microsoft Enhanced RSA and AES Cryptographic Provider" `
        -CertStoreLocation Cert:\LocalMachine\My `
        -NotAfter (Get-Date).AddYears($ValidYears)
}

# Export the PUBLIC certificate only (DER .cer). This is what goes to Trusted
# Publishers via GPO/Intune. It carries NO private key — safe to commit/share.
Export-Certificate -Cert $cert -FilePath $PublicCerOut -Type CERT -Force | Out-Null

Write-Host ""
Write-Host "=== Signing certificate ready ===" -ForegroundColor Cyan
Write-Host "Subject      : $($cert.Subject)"
Write-Host "Thumbprint   : $($cert.Thumbprint)"
Write-Host "Valid until  : $($cert.NotAfter.ToString('yyyy-MM-dd'))"
Write-Host "Public .cer  : $PublicCerOut"
Write-Host ""
Write-Host "Next:" -ForegroundColor Yellow
Write-Host "  1. Build signed:  packaging\build_workstation.ps1 -Engine -Msi -Sign -CertThumbprint $($cert.Thumbprint)"
Write-Host "  2. Trust the fleet: push $PublicCerOut to Trusted Publishers (see docs\CODE_SIGNING.md)."
Write-Host ""
Write-Host "The PRIVATE key stays in Cert:\LocalMachine\My on THIS machine and is never exported." -ForegroundColor DarkGray
