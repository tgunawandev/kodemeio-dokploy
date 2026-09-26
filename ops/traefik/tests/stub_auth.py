"""Stub Authentik outpost for the local Traefik gate test (W11/W12).

As `authentik-outpost` on the test network (port 9000):
  /outpost.goauthentik.io/ping          -> 204
  /outpost.goauthentik.io/auth/traefik  -> 200 if the forwarded request carries
                                           Cookie `stub=ok`, else 401
  any other /outpost.goauthentik.io/*   -> 200 {"outpost": "stub"}

For check-admin-gates.sh's own cases (run directly on the host, any port):
  /_status/<code>[?loc=<Location>]      -> <code> with that Location header

stdlib only. Usage: python3 stub_auth.py [port]
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlsplit(self.path)
        if u.path.startswith("/_status/"):
            code = int(u.path.rsplit("/", 1)[1])
            loc = parse_qs(u.query).get("loc", [None])[0]
            return self._send(code, b"", {"Location": loc} if loc else None)
        if u.path == "/outpost.goauthentik.io/ping":
            return self._send(204)
        if u.path == "/outpost.goauthentik.io/auth/traefik":
            cookies = self.headers.get("Cookie", "")
            if "stub=ok" in [c.strip() for c in cookies.split(";")]:
                return self._send(200, b"", {"X-authentik-username": "drill"})
            return self._send(401, b"unauthenticated")
        if u.path.startswith("/outpost.goauthentik.io/"):
            return self._send(200, b'{"outpost": "stub"}')
        return self._send(404)

    do_HEAD = do_GET  # noqa: N815
    do_POST = do_GET  # noqa: N815


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
