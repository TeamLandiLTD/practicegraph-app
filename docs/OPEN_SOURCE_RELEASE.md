# Free, open-source release

Owner decision: September 5, 2026. This replaces the earlier paid, FSL,
trial-key, and proprietary-server proposals. PracticeGraph itself is free;
subscriptions to AI providers are independent.

The release covers the endpoint, UI, native shells, and optional aggregate server.
Keep existing third-party licenses and attributions. Apache-2.0 is the proposed
permissive license for this branch; the owner's license preference can still
supersede it before publication.

## Release evidence

- [x] New branch: `codex/free-open-source-release-baseline`.
- [x] Product plan updated for usefulness, adoption, and contributions.
- [x] Contribution guide, security policy, and public issue templates prepared.
- [x] Public contribution CI moved off self-hosted machines; frontend gate included.
- [x] Root license and Windows installer notices included; MSI payload verified.
- [x] Recovered capability implementation integrated and exercised in the current UI.
- [x] Python/frontend gates, lint/types, Rust tests, and Windows dependency audits passed.
- [ ] Fresh install and signed update journey on supported platforms.
- [ ] Published release identifies source commit, asset hashes, and catalog versions.
- [x] Prepare an explicit public file inventory and export without private history.
- [x] Separate private editorial drafts from the app and public website; prepare
      validated catalogs, private version archives, and separate content terms.
      See [hosted-content delivery](HOSTED_CONTENT.md). Old private Git history
      and the earlier 0.2.0 source ZIP still require review; the MSI predates this change.
- [x] Windows dependency notices and exact MSI payload SBOM generation implemented.
- [x] Match Microsoft SDK binaries to their official package and retain redistribution notices.
- [ ] Run the updated frozen macOS notice verification on a Mac.
- [ ] Enable private vulnerability reporting and establish maintainer contacts.
- [ ] Align the public site and direct download links with the free product.
- [ ] Publish repository/release and verify public access.

Checked items describe this branch's preparation, not a public deployment.
Repository visibility, release publication, signing services, and hosted
deployments are separate external state and must be verified when performed.

See [security remediation](SECURITY_REMEDIATION.md) for corrections and remaining
release gates, and [releasing](RELEASING.md) for the clean-publication process.
The private development history is not a public-source candidate.

## Source and binary provenance

Maintainer curation prompts and source-selection lists stay outside the public
source inventory. The clean export retains their reusable contracts and
validators; optional maintainer installation checks skip when those private
skills are absent. See [editorial workflows](EDITORIAL_WORKFLOWS.md).

Record commit, dirty state, application version, bundled UI identity, build tool
versions, platform, and SHA-256 of distributed artifacts. A source archive should
be available beside the binary release. Build signing authenticates updates; it
does not restrict use and must remain independent of retired paid entitlement code.

## Pilot

Invite users to test a free candidate after trust and activation gates pass.
Measure useful readings, misunderstandings, practices actually tried, helpful
curated items, voluntary returns, and useful contributions. Do not collect raw
personal activity or increase app engagement as an end in itself.

Publish realistic maintenance scope for adapters, editions, packaging and support.
No trial clock, purchase flow, license key, account, or mandatory hosted server
belongs in the personal user journey.
