# Security

PracticeGraph handles private local activity. Report a suspected vulnerability
privately, without posting logs, tokens, databases, or exploit details in a public
issue.

Use the repository's **Security → Report a vulnerability** option when available.
If that option is unavailable, open a public issue containing only a request for
a private security contact; do not include sensitive details. Maintainers must
enable private vulnerability reporting before public launch.

Include the affected version, operating system, a minimal synthetic reproduction,
and the expected privacy or authorization boundary. No guaranteed response time
is advertised while the maintainer process is being established.

The current development branch is a pre-release. No historical release is
represented as independently security-audited.

## Contribution execution

Pull requests must never execute on maintainer machines or receive deployment,
code-signing, or organization secrets. Avoid `pull_request_target` workflows that
check out contribution code. Native self-hosted packaging is maintainer-dispatched.

## Privacy boundary

The local dashboard requires a session token and loopback host checks.
Do not publish its launch URL. Consent to aggregate sharing does not authorize
exporting individual coaching, paths, or self-reports. Optional external services
must explain their own data flow separately from public catalog downloads.
