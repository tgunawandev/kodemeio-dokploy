"""Alert sink for the Gatus local test (W9).

Records every non-control request (method, path, body) and answers like the
Telegram Bot API (``{"ok": true}``), so the PRODUCTION telegram provider can be
exercised by pointing ``alerting.telegram.api-url`` here. Also receives the
heartbeat sidecar's Healthchecks-style pings (``GET /hc/gatus[/fail]``).

``GET /_records`` returns the list. stdlib only.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORDS: list[dict] = []
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _reply(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode(errors="replace") if length else ""
        with LOCK:
            RECORDS.append({"t": time.time(), "method": self.command, "path": self.path, "body": body})
        self._reply(200, {"ok": True, "result": {}})

    def do_GET(self):  # noqa: N802
        if self.path == "/_records":
            with LOCK:
                return self._reply(200, list(RECORDS))
        return self._record()

    def do_POST(self):  # noqa: N802
        return self._record()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
