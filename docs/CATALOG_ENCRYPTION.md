# Skill-authored encrypted catalog editions

The maintainer skill finishes each new official edition by running
`tools.catalog_seal`. It validates the private JSON, encrypts it with AES-256-GCM,
signs the envelope with an independent Ed25519 publisher key, and verifies the
result through the actual client reader before writing it. No extra server,
registration, account or key-fetch request is needed.

The application contains a **recoverable reader key**. This deliberately modest
scraping deterrent makes a hosted file unreadable to a generic JSON crawler;
anyone can reproduce the open client's decryption or export its local cache.
It is not installation verification, DRM or a confidentiality boundary against
recipients. The separate signing seed stays private, so knowing the reader key
does not let a recipient forge a signed edition.

## Author, encrypt and deliver

Maintain plain drafts and evidence in the private editorial workspace, outside
the app and website. Provide the full production JSON, including the channel's
edition version. Channel-specific validation and editorial review still apply.

```powershell
python -m tools.catalog_seal --channel training --draft ../practicegraph-site/editorial/training/drafts/training.json --out ../practicegraph-site/editorial/training/prepared/training.json
python -m tools.content_release validate --channel training --draft ../practicegraph-site/editorial/training/prepared/training.json
```

The default signing-key location is
`~/.practicegraph-publisher/catalog-signing-2026-09.key`, outside Git. Use
`--signing-key <private-path>` for explicit private custody and `--key-id` for a
reader/publisher key already supported by the client. The current key ID is
`teamlandi-2026-09`. Never paste a signing seed into a prompt, output, repository,
skill or website. A missing or mismatched key stops encryption; do not generate
a replacement silently. Back up the signing key using the maintainer's private
key-custody process. This key is independent of the installer/update signing key.

Once the client-compatible edition is authorized for website delivery, use the
same command with `--out <site>/<channel>.json`, then run the normal
`tools.content_release prepare` and `check` gates. The website path still ends in
`.json`, but its body is the encrypted envelope. **Do not copy raw JSON or run an
older plaintext-writing publish command afterwards.** The normal publication
authority and path-specific Git process remain applicable; encryption does not
authorize a push or deployment. Keep a readable private draft for editorial review.

For a news/models helper that normalizes a source draft, generate its full
artifact into a *private staging directory*, then seal that artifact into the
delivery location. Avoid writing a transient plaintext feed into the website.
The news draft's explicit root `news_version` already supports direct sealing.

Read an existing encrypted edition for editorial work with:

```powershell
python -m tools.catalog_seal --channel training --read <site>/training.json --out ../practicegraph-site/editorial/training/previous/training.json
```

The read command refuses to create a plaintext copy in the app, website or
source file's directory. It is a local tool; source downloads use the existing
bounded HTTPS workflow. To check deployment, compare the live encrypted bytes
with the prepared artifact and manifest, then validate the downloaded file with
`tools.content_release validate`; version fields are inside the encrypted body.

## Immutable editions and compatibility

- The envelope has exactly `schema`, `channel`, `edition_version`, `key_id`,
  `nonce`, `ciphertext`, and `signature`. Metadata is bound to both encryption
  and signature. Unknown keys, wrong channels, altered contents, unsupported
  transport versions and invalid inner catalogs fail closed.
- Nonces are randomly generated for each new encryption. Re-running a skill
  against an identical encrypted output reuses its exact bytes. Changed content,
  conversion from plaintext, and reader-key rotation require a new edition
  version; do not replace an immutable release with fresh ciphertext.
- Client and release tooling bound plaintext at 512 KiB and envelopes at 710,000
  bytes. No compression or key URL is accepted from an envelope.
- Existing plaintext feeds and custom hosts remain compatible. Publisher
  authentication applies to sealed envelopes; ordinary feeds retain their TLS
  and schema boundary. An older client cannot read sealed editions. Release a
  compatible client before switching an existing production feed, and decide
  the older-client support window explicitly.
- The website manifest inventories and hashes the *encrypted* served bytes.
  The private release archive preserves those exact bytes. Local client caches
  contain validated plaintext, with existing source receipts bound to that
  plaintext digest. Neither repeated downloads nor resealing refreshes dates.
- These changes do not encrypt already-served editions or remove old plaintext
  URLs/archives. Audit those during the separately authorized rollout.

Cryptography is now a pinned runtime dependency. Windows packaging stages wheels
for the shipped Python 3.14 ABI, including CFFI, rather than the build host's
interpreter. Frozen builds include the crypto modules and their notices. Run
runtime smoke tests when preparing actual platform releases.
