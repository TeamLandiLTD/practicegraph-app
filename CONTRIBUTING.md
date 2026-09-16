# Contributing to PracticeGraph

PracticeGraph is a free, open-source daily reading for people who work with AI.
Both the personal app and the optional aggregate server are developed here.

Start with a small issue describing the user-visible problem or a pull request
with a focused fix. Explain what changed, why, and how you checked it.
For navigation or measurement changes, include before/after examples and the
evidence behind the measurement. Preserve the existing design unless the change
specifically calls for a new interaction.

## Develop locally

Use Python 3.14.7 and Node.js 22 or later.

```powershell
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cd ui
npm ci
npm test
npm run build
```

From the repository root, run the Python gates:

```powershell
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest
```

On macOS/Linux, use `python3.14` and `.venv/bin/python`.
Commit the generated `webui/index.html` and its referenced assets together when
changing the frontend. The bundle test detects missing tracked assets.

## Privacy and evidence

Use synthetic fixtures. Never attach real prompts, responses, session logs,
credentials, personal paths, private project names, or an unredacted database
to a public issue or pull request. Describe a parser mismatch with a minimized
synthetic example.

Personal observations and self-reports stay local. The fleet wire accepts only
its closed aggregate schema. Missing evidence must not become a zero, a score,
or an unsupported claim about a person's attention or health.

## Contributions and licensing

Contributions are provided under the root license. Preserve third-party notices.
Official hosted editorial editions have separate content terms. Keep new drafts,
candidate piles, and review notes in the private editorial workspace, outside
this repository. Public parsers, generic authoring utilities, and synthetic
test fixtures remain under the software license. See [the content boundary](docs/HOSTED_CONTENT.md).
There is no separate proprietary contribution agreement or paid feature tier.
Only contribute material you are entitled to share; identify copied or adapted
third-party material and its original license.

## Review and releases

Public pull requests use disposable hosted runners. Windows packaging on
maintainer hardware is an explicit maintainer workflow; do not request secrets
or self-hosted execution in contributor workflows. See [SECURITY.md](SECURITY.md)
for private vulnerability reporting and [the release guide](docs/OPEN_SOURCE_RELEASE.md)
for release readiness.


Windows native builds need the GNU Rust toolchain, full MinGW binutils/GCC
(including dlltool) on PATH, and WiX 5.0.2. No personal absolute tool path is
committed. Self-hosted maintainers set `MINGW_BIN` to their installation.
Python/UI contribution checks do not require the native toolchain.
