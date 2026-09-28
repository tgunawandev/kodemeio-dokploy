"""App-level validation of a restored Authentik (Wave 0 Task 8, spec D13).

Runs as a one-shot container on the drill's `internal: true` network (stdlib
only — the network has no egress to install anything). Checks:

  ready              GET /-/health/ready/ == 200
  user_count         the restored DB's user count read from SQL BEFORE boot
                     (--expected-users) equals `ak shell`'s count AFTER boot,
                     excluding the drill's own user (--actual-users)
  login              the drill user logs in through the flow executor API:
                     identification -> password -> xak-flow-redirect
  wrong_password     the same flow with a wrong password is REFUSED (proves the
                     login check is not vacuous)
  egress_blocked     a TCP connect to a public address fails (isolation)

The drill user's password comes from env DRILL_PASS and is never printed.
Output: one JSON object on stdout ({"ok": bool, "checks": {...}}); exit 0 only
when every check passed. Challenge component names verified against
ghcr.io/goauthentik/server:2026.2.3 (ak-stage-identification with
`uid_field`, ak-stage-password with `password`, final xak-flow-redirect).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IDENT = "ak-stage-identification"
PASSWORD = "ak-stage-password"
REDIRECT = "xak-flow-redirect"


def http_status(url: str, timeout: float = 10) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


class FlowSession:
    def __init__(self, base: str, flow: str):
        self.url = f"{base}/api/v3/flows/executor/{urllib.parse.quote(flow)}/?query=next%3D%2F"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, payload: dict | None = None) -> dict:
        req = urllib.request.Request(self.url, method="POST" if payload is not None else "GET")
        req.add_header("Accept", "application/json")
        body = None
        if payload is not None:
            req.add_header("Content-Type", "application/json")
            body = json.dumps(payload).encode()
        with self.opener.open(req, body, timeout=30) as r:
            return json.loads(r.read())


def login(base: str, flow: str, user: str, password: str) -> tuple[bool, str]:
    """Walk the flow. Returns (reached_redirect, trail of components)."""
    s = FlowSession(base, flow)
    trail = []
    try:
        ch = s.call()
        for _ in range(6):
            comp = ch.get("component", "?")
            trail.append(comp)
            if comp == REDIRECT:
                return True, " > ".join(trail)
            if ch.get("response_errors"):
                trail.append("errors")
                return False, " > ".join(trail)
            if comp == IDENT:
                ch = s.call({"component": IDENT, "uid_field": user})
            elif comp == PASSWORD:
                if trail.count(PASSWORD) > 1:
                    return False, " > ".join(trail)
                ch = s.call({"component": PASSWORD, "password": password})
            else:
                return False, " > ".join(trail) + " (unexpected stage)"
        return False, " > ".join(trail) + " (too many stages)"
    except (OSError, ValueError) as e:
        return False, " > ".join(trail) + f" ({type(e).__name__})"


def egress_blocked(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            return False
    except OSError:
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://server:9000")
    ap.add_argument("--flow", default="default-authentication-flow")
    ap.add_argument("--user", default="drill_validator")
    ap.add_argument("--expected-users", type=int, required=True)
    ap.add_argument("--actual-users", type=int, required=True)
    ap.add_argument("--egress-host", default="1.1.1.1")
    ap.add_argument("--egress-port", type=int, default=443)
    ap.add_argument("--ready-timeout", type=int, default=60)
    a = ap.parse_args()
    password = os.environ.get("DRILL_PASS", "")

    checks: dict[str, dict] = {}
    deadline = time.time() + a.ready_timeout
    status = http_status(f"{a.base}/-/health/ready/")
    while status != 200 and time.time() < deadline:
        time.sleep(2)
        status = http_status(f"{a.base}/-/health/ready/")
    checks["ready"] = {"ok": status == 200, "status": status}

    checks["user_count"] = {
        "ok": a.expected_users == a.actual_users and a.expected_users > 0,
        "expected": a.expected_users,
        "actual": a.actual_users,
    }

    ok, trail = login(a.base, a.flow, a.user, password) if password else (False, "DRILL_PASS unset")
    checks["login"] = {"ok": ok, "flow": a.flow, "trail": trail}

    bad_ok, bad_trail = login(a.base, a.flow, a.user, password + "-wrong") if password else (True, "")
    checks["wrong_password"] = {"ok": not bad_ok, "trail": bad_trail}

    checks["egress_blocked"] = {"ok": egress_blocked(a.egress_host, a.egress_port), "probe": a.egress_host}

    result = {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
