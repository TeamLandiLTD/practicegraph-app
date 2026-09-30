"""Entry point for the compiled engine executable (practicegraph-engine.exe).

Compiled by Nuitka (see build_workstation.ps1 -Engine) so shipped bundles
carry no readable Python source. The surface is exactly the endpoint CLI:

    practicegraph-engine.exe <any practicegraph subcommand>

plus one passthrough so the same binary can run the passive org server:

    practicegraph-engine.exe server serve
    practicegraph-engine.exe server render-dashboard --out <dir>
"""

from __future__ import annotations

import sys


def run() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        from practicegraph_server.__main__ import main as server_main

        raise SystemExit(server_main(sys.argv[2:]))
    from practicegraph.cli import entry

    entry()


if __name__ == "__main__":
    run()
