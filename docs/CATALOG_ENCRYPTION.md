# Encrypted catalog editions

Each official edition is validated against its production parser, encrypted
with AES-256-GCM, and signed with an independent Ed25519 publisher key. The
client reader (`src/practicegraph/catalog_crypto.py`) verifies and decrypts it
before the normal schema and cache checks. No extra server, registration,
account or key-fetch request is needed.

The application contains a **recoverable reader key**. This deliberately modest
scraping deterrent makes a hosted file unreadable to a generic JSON crawler;
anyone can reproduce the open client's decryption or export its local cache.
It is not installation verification, DRM or a confidentiality boundary against
recipients. The separate signing seed stays private, so knowing the reader key
does not let a recipient forge a signed edition.

## Immutable editions and compatibility

- The envelope has exactly `schema`, `channel`, `edition_version`, `key_id`,
  `nonce`, `ciphertext`, and `signature`. Metadata is bound to both encryption
  and signature. Unknown keys, wrong channels, altered contents, unsupported
  transport versions and invalid inner catalogs fail closed.
- Nonces are randomly generated for each new encryption. An edition version
  is immutable: changed content, conversion from plaintext, and reader-key
  rotation require a new edition version, never fresh ciphertext under an old one.
- The client bounds plaintext at 512 KiB and envelopes at 710,000
  bytes. No compression or key URL is accepted from an envelope.
- Public pulls accept sealed editions only (client 0.2.17 and later). A valid
  but unsigned document from any public or custom host is refused as
  `invalid_artifact` and never reaches disk: the publisher signature, not TLS
  to the host, is what makes served prompts, playbooks and one-click model pins
  trustworthy. Every live channel has been sealed since September 2026, so this
  changes nothing for installed clients. Enterprise catalogs served by an
  operator's own server (`api_base_url`) are a separate, trusted path and stay
  plain. An older client cannot read sealed editions.
- Local client caches contain validated plaintext, with source receipts bound
  to that plaintext digest. Neither repeated downloads nor resealing refreshes
  dates.

Cryptography is now a pinned runtime dependency. Windows packaging stages wheels
for the shipped Python 3.14 ABI, including CFFI, rather than the build host's
interpreter. Frozen builds include the crypto modules and their notices. Run
runtime smoke tests when preparing actual platform releases.
