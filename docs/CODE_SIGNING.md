# Code signing (free, org-internal fleet)

PracticeGraph ships an unsigned MSI by default. Unsigned, Windows SmartScreen
shows "Unknown publisher" and enterprise policy may block the install. This is
the **free** signing path for a fleet **you administer**: a self-signed
code-signing certificate, pushed to Trusted Publishers via Group Policy or
Intune. On machines that trust the cert, signed installs show *TeamLandiLTD* as
a verified publisher and install cleanly — at zero cost.

**Scope and honesty.** A self-signed cert is trusted **only** on machines you
push it to. Anyone outside your GPO/Intune scope still sees "Unknown
publisher." If this software is ever distributed to machines you do **not**
control, you need a publicly-trusted cert from a CA (cheapest headless-friendly
option: **Azure Trusted Signing**, ~$10/mo, if the org is 3+ years old). This
document covers only the internal-fleet path.

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
  Those need a CA-issued (paid) certificate; self-signed will warn there.
- **Kernel-mode drivers** — require an EV cert and Microsoft attestation.
  PracticeGraph ships no drivers, so this does not apply.
