"""Fake kod endpoints for the Gatus local test (W9).

Gatus's test config rewrites every production URL
``https://<host><path>`` to ``http://fake-targets:8000/<host><path>``; this
server strips the host segment and answers like the real service would.

Control (from the test): ``POST /_control`` with a JSON object merges into the
state: ``{"db": "connected"|"disconnected", "open_gates": ["dokploy.kodeme.io", ...]}``.
``GET /_control`` returns the state. stdlib only.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE: dict = {"db": "connected", "open_gates": []}
LOCK = threading.Lock()

# (host, path) pairs that are admin UIs: refused (302 to Authentik) unless
# the host is in STATE["open_gates"], in which case they serve 200 like an
# ungated login page would.
GATES = {
    ("dokploy.kodeme.io", "/"),
    ("hatchet.kodeme.io", "/"),
    ("dsh.kodeme.io", "/"),
    ("llm.kodeme.io", "/ui"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # keep container logs quiet
        pass

    def _send(self, status: int, body: object = None, headers: dict | None = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        if self.path != "/_control":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        update = json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            STATE.update(update)
            snapshot = dict(STATE)
        return self._send(200, snapshot)

    def do_GET(self):  # noqa: N802
        if self.path == "/_control":
            with LOCK:
                return self._send(200, dict(STATE))
        parts = self.path.split("/", 2)  # ["", host, rest]
        if len(parts) < 2 or not parts[1]:
            return self._send(404, {"error": "no host segment"})
        host = parts[1]
        path = "/" + (parts[2] if len(parts) > 2 else "")
        with LOCK:
            db = STATE["db"]
            open_gates = set(STATE["open_gates"])

        if (host, path) in GATES:
            if host in open_gates:
                return self._send(200, {"page": "login"})
            return self._send(302, None, {"Location": "https://auth.kodeme.io/outpost.goauthentik.io/start"})
        if host == "auth.kodeme.io" and path == "/-/health/live/":
            return self._send(200, None)
        if path == "/web/health":
            return self._send(200, {"status": "pass"})
        if host == "mm.kodeme.io" and path == "/api/v4/system/ping":
            return self._send(200, {"status": "OK"})
        if host == "llm.kodeme.io" and path == "/health/readiness":
            # Like the pinned LiteLLM image: HTTP 200 even when the DB is down.
            return self._send(200, {"status": "healthy" if db == "connected" else "unhealthy", "db": db})
        if host == "llm.kodeme.io" and path == "/health/liveliness":
            return self._send(200, "I'm alive!")
        return self._send(404, {"error": f"unknown {host}{path}"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
