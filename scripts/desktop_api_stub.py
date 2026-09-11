#!/usr/bin/env python
"""A stub of the QuickLaunch API for the desktop self-test.

The `desktop-self-test` job runs on a Windows runner without Docker, so it
cannot bring up Postgres. This stands in for the API and answers exactly the
requests the shell makes on boot:

    GET /api/health  -> 200 {"status": "ok", ...}
    GET /api/auth/me -> 401 {"error": {...}}   (the normal signed-out state)

It also carries the CORS headers, because the desktop renderer calls it from the
`app://bundle` origin - precisely the cross-origin path a packaged desktop app
depends on. Credentialed requests must never be answered with a `*` wildcard, so
the caller's origin is echoed back when it is one we know.

200 here means "the shell is wired correctly"; the point is not to test the API
(the backend job does that against real Postgres).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8123
ALLOWED_ORIGINS = {"app://bundle", "http://localhost", "https://localhost"}


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/health"):
            self._send(200, {"status": "ok", "database": "stub", "version": "1.0.0"})
        elif self.path.startswith("/api/auth/me") or self.path.startswith("/api/todos"):
            self._send(
                401,
                {
                    "error": {
                        "code": "unauthenticated",
                        "message": "Sign in to continue.",
                    }
                },
            )
        else:
            self._send(404, {"error": {"code": "not_found", "message": "stub"}})

    def log_message(self, *_: object) -> None:
        pass


if __name__ == "__main__":
    print(f"stub API on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Stub).serve_forever()
