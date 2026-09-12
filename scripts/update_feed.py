#!/usr/bin/env python
"""Serve a fake update feed to test desktop update detection.

Writes a `latest.yml` advertising a chosen version and serves it over plain HTTP,
so a packaged desktop build can be pointed at it with QL_UPDATE_FEED_URL and
asked what it would do. The feed only has to be *readable* for detection: the
self-test runs with QL_UPDATE_AUTODOWNLOAD=false, so no installer is fetched.

    python scripts/update_feed.py --version 9.9.9 --port 8199
"""

from __future__ import annotations

import argparse
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FEED = """version: {version}
files:
  - url: QuickLaunch-Setup-{version}-x64.exe
    sha512: {sha}
    size: {size}
path: QuickLaunch-Setup-{version}-x64.exe
sha512: {sha}
releaseDate: '2026-01-01T00:00:00.000Z'
"""


def write_feed(directory: Path, version: str) -> Path:
    target = directory / "latest.yml"
    # The hash only has to be well formed for the check; nothing is downloaded.
    target.write_text(
        FEED.format(version=version, sha="A" * 88, size=1024),
        encoding="utf-8",
    )
    return target


def serve(directory: Path, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            # electron-updater appends a cache buster (`latest.yml?noCache=...`).
            path = urllib.parse.urlparse(self.path).path
            name = path.lstrip("/") or "latest.yml"
            candidate = (directory / name).resolve()
            if not candidate.is_file() or directory.resolve() not in candidate.parents:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"feed: {self.command} {self.path}", flush=True)

    print(f"serving {directory} on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="9.9.9")
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--directory", default=".cache/update-feed")
    args = parser.parse_args()

    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_feed(directory, args.version)
    serve(directory, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
