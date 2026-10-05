"""Synthetic local health target; no production app or data."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Target(BaseHTTPRequestHandler):
    healthy = True

    def do_GET(self):
        self.send_response(200 if self.path == "/live" or Target.healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok" if Target.healthy else "failed"}).encode())

    def do_POST(self):
        if self.path not in {"/fail", "/recover"}:
            self.send_error(404)
            return
        Target.healthy = self.path == "/recover"
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", 8000), Target).serve_forever()
