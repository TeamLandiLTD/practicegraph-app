"""Server entry point: ``serve`` runs the passive API; ``render-dashboard``
writes the dashboard as a static build servable by any web server (FR-DSH-5).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from practicegraph_server.app import make_server
from practicegraph_server.config import load_config
from practicegraph_server.render import render_dashboard
from practicegraph_server.storage import MemoryStorage, ServerStorage, SqliteStorage
from practicegraph_server.view import build_summary


def _storage(db_path: str) -> ServerStorage:
    if db_path == ":memory:":
        return MemoryStorage()
    return SqliteStorage(db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="practicegraph-server")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the passive API (default)")
    render = subparsers.add_parser("render-dashboard", help="write the dashboard as static HTML")
    render.add_argument("--out", type=Path, required=True, help="output directory")
    render.add_argument("--days", type=int, default=14, help="window size in days")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = load_config(dict(os.environ))
    except ValueError:
        print("invalid server credential configuration", file=sys.stderr)
        return 2
    if not config.org_id or not (config.org_token or config.ingest_tokens):
        print("organization and ingest credentials must be set", file=sys.stderr)
        return 2
    storage = _storage(config.db_path)

    if args.command == "render-dashboard":
        to_day = datetime.now(UTC).date()
        from_day = to_day - timedelta(days=max(1, args.days) - 1)
        grouped = storage.payloads_by_day(config.org_id, from_day.isoformat(), to_day.isoformat())
        summary = build_summary(
            grouped, config.k_threshold, from_day.isoformat(), to_day.isoformat()
        )
        page = render_dashboard(summary, config.org_id, datetime.now(UTC))
        out_dir: Path = args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(page, encoding="utf-8", newline="")
        print(f"wrote {out_dir / 'index.html'}")
        return 0

    server = make_server(config, storage)
    print(
        f"practicegraph-server listening on {config.bind}:{config.port} "
        f"(k={config.k_threshold}, dashboard={'on' if config.serve_dashboard else 'off'})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entry()
