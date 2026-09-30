# Code signing

Two signing paths exist. **Azure Artifact Signing** (the `-SignAzure` build
flag) gives a publicly trusted signature for anything that leaves the fleet:
the download page, GitHub releases, other orgs. The **self-signed fleet path**
(`-Sign`) below is free and only trusted where you push the certificate. Public
releases use Artifact Signing; see the section at the end.

## Free, org-internal fleet path

PracticeGraph ships an unsigned MSI by default. Unsigned, Windows SmartScreen
shows "Unknown publisher" and enterprise policy may block the install. This is
the **free** signing path for a fleet **you administer**: a self-signed
code-signing certificate, pushed to Trusted Publishers via Group Policy or
Intune. On machines that trust the cert, signed installs show *TeamLandiLTD* as
a verified publisher and install cleanly — at zero cost.

**Scope and honesty.** A self-signed cert is trusted **only** on machines you
push it to. Anyone outside your GPO/Intune scope still sees "Unknown
publisher." If this software is ever distributed to machines you do **not**
control, use the Azure Artifact Signing path at the end of this document
instead.

---

## One-time: provision the signing certificate

On the **build machine**, in an **elevated** PowerShell (writes to
`Cert:\LocalMachine\My`):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\sign\New-SigningCert.ps1
```

This mints a self-signed code-signing cert (RSA-3072, SHA256, 5-year), keeps
its **private key on this machine only**, and exports the **public** half to
`packaging\sign\PracticeGraph-Publisher.cer`. It prints the thumbprint. Re-runs
reuse the existing cert (no new key).

> The private key never leaves the build machine. Only the public `.cer` is
> distributed — it cannot sign anything, only *verify* signatures.

## Build a signed installer

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_workstation.ps1 `
    -Engine -Msi -Sign -CertThumbprint <thumbprint-from-above>
```

`-Sign` signs the shipped executables (`practicegraph-engine.exe`,
`practicegraph-shell.exe`) **before** WiX packages them, and the finished MSI
**after** ICE validation — each with SHA256 and an RFC-3161 timestamp so the
signatures outlive the certificate. `WebView2Loader.dll` is left as-is (it
ships already signed by Microsoft). Omit `-CertThumbprint` to auto-select the
TeamLandiLTD publisher cert.

> On the build machine the signature reports as "signed, timestamped (trust via
> GPO on targets)" rather than locally trusted — that is expected. The build
> verifies the signer thumbprint and timestamp on every file regardless, so an
> unsigned or mis-signed artifact fails the build.

## Trust the fleet: push the public cert to Trusted Publishers

Target machines must have the public cert in **two** stores:
`Trusted Root Certification Authorities` (so the chain builds) and
`Trusted Publishers` (so the signed install runs without prompting).

### Option A — Group Policy (domain-joined machines)

1. Copy `PracticeGraph-Publisher.cer` to a location the GPO can reach.
2. Group Policy Management → edit a GPO linked to the target OU →
   **Computer Configuration → Policies → Windows Settings → Security Settings →
   Public Key Policies**.
3. Right-click **Trusted Publishers → Import** → select the `.cer`.
4. Right-click **Trusted Root Certification Authorities → Import** → the same
   `.cer`.
5. `gpupdate /force` on a client (or wait for the refresh) to confirm.

### Option B — Microsoft Intune (MDM-managed machines)

1. Intune admin center → **Devices → Configuration → Create → New policy**.
2. Platform **Windows 10 and later**, profile **Templates → Trusted
   certificate**.
3. Upload `PracticeGraph-Publisher.cer`, destination store **Computer
   certificate store – Root**. Assign to the device group.
4. Create a **second** Trusted-certificate profile for the same `.cer` — some
   tenants surface a Trusted-Publisher destination; where they do not, the Root
   profile plus the signed MSI is generally sufficient for a clean install, and
   the Root import is the load-bearing one for chain trust.

### Option C — manual / per-machine (a handful of machines)

Elevated PowerShell on each target:

```powershell
Import-Certificate -FilePath .\PracticeGraph-Publisher.cer `
    -CertStoreLocation Cert:\LocalMachine\Root
Import-Certificate -FilePath .\PracticeGraph-Publisher.cer `
    -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
```

## Verify it worked

On a target machine after the cert is deployed:

```powershell
Get-AuthenticodeSignature "C:\Program Files\PracticeGraph\practicegraph-engine.exe" |
    Select-Object Status, @{n='Signer';e={$_.SignerCertificate.Subject}}
```

`Status` should read **Valid** and the signer should be **TeamLandiLTD**. Right-
clicking the MSI → Properties → **Digital Signatures** shows the same, and the
UAC prompt on install now names TeamLandiLTD instead of "Unknown publisher."

---

## Renewal

The cert is valid 5 years. Timestamped signatures on already-shipped installers
remain valid past that date. To renew: re-run `New-SigningCert.ps1` (mint a new
key once the old nears expiry), push the new public `.cer` to the fleet, and
rebuild. Keep the old cert trusted until every machine has updated.

## What is NOT covered by this path

- **External distribution** — download pages, other orgs, unmanaged machines.
  Use the Azure Artifact Signing path below.
- **Kernel-mode drivers** — require an EV cert and Microsoft attestation.
  PracticeGraph ships no drivers, so this does not apply.

---

# Public distribution: Azure Artifact Signing

Azure Artifact Signing (Microsoft renamed it from Trusted Signing; the roles
in the portal carry the new name) issues short-lived, publicly trusted
code-signing certificates from a Microsoft CA. The private key never exists
anywhere we can see: the build sends a file digest, Azure signs it. Installers
signed this way verify as **Valid** on any Windows machine with no certificate
push, and the UAC prompt names **TeamLandi OOD**.

## What exists in Azure

| Item | Value |
| --- | --- |
| Resource | `teamlandi-signing-cert` in resource group `rg-signing-app` |
| Endpoint | `https://weu.codesigning.azure.net/` (West Europe) |
| Certificate profile | `PracticeGraph`, type Public Trust, status Active |
| Certificate subject | `CN=TeamLandi OOD, O=TeamLandi OOD, L=Sofia, S=Sofia City, C=BG` |

These are the defaults baked into `build_workstation.ps1` (`-AzureEndpoint`,
`-AzureAccount`, `-AzureProfile`, `-AzureSubject`). None of them is secret:
signing is gated by Azure RBAC, not by knowing the names.

## One-time, per signer

1. **Grant the signing role.** Identity validation and profile creation use
   the *Identity Verifier* role; actually signing needs a different one.
   Whoever signs (a person, or a service principal for CI) needs
   **Artifact Signing Certificate Profile Signer** on the account. A
   subscription Owner can grant it:

   ```powershell
   az role assignment create --role "Artifact Signing Certificate Profile Signer" `
       --assignee <upn-or-app-id> `
       --scope /subscriptions/<subscription-id>/resourceGroups/rg-signing-app/providers/Microsoft.CodeSigning/codeSigningAccounts/teamlandi-signing-cert
   ```

   Without it the build stops at the first file with `403 (Forbidden)`.
   Role propagation can take a few minutes.

2. **Install the signing module** (current user, no Windows SDK needed; it
   downloads signtool and the Azure signing client on first use into
   `%LOCALAPPDATA%\TrustedSigning`):

   ```powershell
   Install-Module TrustedSigning -Scope CurrentUser
   ```

3. **Authenticate.** On a workstation, an ordinary `az login` to the TeamLandi
   tenant is enough; the build refuses to start signing if neither that nor a
   service principal is present. For unattended builds set
   `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` for a
   service principal that holds the signer role. Never write those into the
   repo or a build log.

## Build a publicly signed installer

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_workstation.ps1 -Msi -SignAzure
```

That is the shipping shape since 0.1.24 (embedded Python runtime, no compiled
engine); add `-Engine` only for a compiled-engine build, which then also signs
`practicegraph-engine.exe`.

`-SignAzure` implies `-Sign` and follows the same order: both executables are
signed before WiX packages them, the MSI after ICE validation, each with
SHA256 and an RFC-3161 timestamp from `http://timestamp.acs.microsoft.com`.
After every signature the build re-reads the file and fails unless:

- the chain verifies as **Valid** on the build machine (no GPO caveat here;
  public trust must work locally or something is wrong),
- the signer subject starts with `CN=TeamLandi OOD` (profile certificates
  rotate every few days, so a thumbprint pin would break; the subject is the
  stable identity),
- a timestamp countersignature is present. This one matters more than on the
  fleet path: the issued certificate expires within days, and only the
  timestamp keeps the installer valid afterwards.

## Verify

On any machine, no setup:

```powershell
Get-AuthenticodeSignature .\PracticeGraph-<version>.msi |
    Select-Object Status, @{n='Signer';e={$_.SignerCertificate.Subject}}
```

`Status` is **Valid** and the signer is **TeamLandi OOD**.

## Honesty notes

- **SmartScreen reputation is separate from trust.** A valid signature removes
  "Unknown publisher"; SmartScreen may still show its "not commonly
  downloaded" prompt on early downloads until the publisher accumulates
  reputation. Artifact Signing accelerates this compared to a fresh OV
  certificate, but does not skip it.
- **Identity validation must stay current** in the Azure portal. If it lapses,
  new signatures stop; already-shipped, timestamped installers keep verifying.
- **Cost** is the Artifact Signing subscription, billed monthly regardless of
  how many files are signed.
- The self-signed fleet path above is unaffected and still works for pilot
  rings; do not mix the two in one release.
