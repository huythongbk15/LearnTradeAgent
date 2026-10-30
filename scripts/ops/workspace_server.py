#!/usr/bin/env python3
"""Serve the workspace page on this machine only (docs/operations/WORKSPACE_UI.md).

GET only, on four fixed routes, bound to 127.0.0.1, with a Host check against
DNS rebinding. It serves no file but its own page and takes no path from a
request. It is read-only and is not a scheduler: start and stop it yourself.

    .venv/bin/python scripts/ops/workspace_server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace_status  # noqa: E402

HOST = "127.0.0.1"
PAGE = Path(__file__).with_name("workspace_page.html")
# Seconds a section is reused before it is read again.
CACHE_SECONDS = {"doing": 5.0, "done": 60.0, "have": 3600.0}
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class SectionCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[float, bytes]] = {}

    def get(self, section: str) -> bytes:
        with self._lock:
            cached = self._values.get(section)
            if cached and time.monotonic() - cached[0] < CACHE_SECONDS[section]:
                return cached[1]
            try:
                body = workspace_status.to_json(workspace_status.SECTIONS[section]())
            except Exception as exc:  # noqa: BLE001 - the page shows the failure
                body = workspace_status.to_json(
                    {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                )
            encoded = body.encode()
            self._values[section] = (time.monotonic(), encoded)
            return encoded


def make_handler(port: int, cache: SectionCache) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}"}
    routes = {f"/api/{section}": section for section in workspace_status.SECTIONS}

    class Handler(BaseHTTPRequestHandler):
        server_version = "workspace"
        sys_version = ""

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(403, b"forbidden host", "text/plain; charset=utf-8")
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path in routes:
                self._send(200, cache.get(routes[path]), "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        def _refuse(self) -> None:
            self._send(405, b"read-only: GET only", "text/plain; charset=utf-8")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _refuse

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            sys.stderr.write(f"{self.log_date_time_string()} {format % args}\n")

    return Handler


def serve(port: int, *, warm: bool = False) -> ThreadingHTTPServer:
    cache = SectionCache()
    if warm:
        # The first read imports the execution modules (~15 s cold); do it now.
        threading.Thread(target=cache.get, args=("doing",), daemon=True).start()
    return ThreadingHTTPServer((HOST, port), make_handler(port, cache))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = serve(args.port, warm=True)
    print(f"Workspace page: http://{HOST}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
