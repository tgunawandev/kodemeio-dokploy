#!/usr/bin/env python3
"""Roadmap row 0.2 -- verify the leaked GitHub App credentials are rotated.

The leaked credential is the Dokploy GitHub App's private key + client secret
(kodemeio-dsh/docs/deployment-plan.md). Gate: the old key is rejected.

Subcommands (none prints key material or a JWT):
  fingerprint KEY.pem   GitHub's private-key fingerprint: "SHA256:" + base64
                        of SHA-256 over the DER public key (GitHub docs,
                        "Verifying private keys").
  verify                Signs a short-lived RS256 App JWT with each key and
                        calls GET <api-base>/app. Old key must get exactly 401,
                        new key 200 with the same App id. Anything else is
                        INCONCLUSIVE and fails. If the old .pem is not held,
                        --old-fingerprint + --listed-fingerprints (text copied
                        from the App settings page) proves it is no longer
                        listed.
  scan REPO...          git grep over TRACKED files for private-key headers,
                        then separates real key bodies (fail) from header-only
                        doc/test mentions (noted); prints repo:path only.

Exit: 0 pass, 1 fail/inconclusive/hits, 2 input error. Signing and hashing use
the openssl binary; key paths go on argv, key bytes never do.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_API = "https://api.github.com"
FP_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}=")
KEY_HEADER_RE = r"-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class InputError(ValueError):
    """The inputs cannot support a verdict."""


def _openssl(args: list[str], data: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(["openssl", *args], input=data, capture_output=True, check=False, timeout=30)
    except FileNotFoundError as exc:
        raise InputError("openssl is not installed") from exc
    if r.returncode != 0:
        raise InputError(f"openssl {args[0]} failed (not a readable RSA private key?)")
    return r.stdout


def _key_path(path) -> Path:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"key file not found: {p}")
    return p


def fingerprint(key: Path) -> str:
    der = _openssl(["rsa", "-in", str(_key_path(key)), "-pubout", "-outform", "DER"])
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(app_id: int, key: Path, now: float | None = None) -> str:
    """App JWT per GitHub docs: iat backdated 60 s, exp <= 10 min, iss = App id."""
    now = int(time.time() if now is None else now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = _openssl(["dgst", "-sha256", "-sign", str(_key_path(key))], signing_input)
    return f"{header}.{payload}.{_b64url(sig)}"


def _check_api_base(api_base: str) -> str:
    parsed = urllib.parse.urlparse(api_base)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS):
        return api_base.rstrip("/")
    raise InputError("api base must be https (plain http only for a localhost fake)")


def probe(api_base: str, token: str, timeout: float) -> tuple[int | None, dict | None]:
    req = urllib.request.Request(
        f"{api_base}/app",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kod-s0-verify",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, None
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = None
    return status, body if isinstance(body, dict) else None


def _old_outcome(status: int | None) -> str:
    if status == 401:
        return "rejected"
    if status == 200:
        return "accepted"
    return "inconclusive"


def _new_outcome(status: int | None, body: dict | None, app_id: int) -> str:
    if status == 200:
        return "accepted" if body is not None and body.get("id") == app_id else "inconclusive"
    if status == 401:
        return "rejected"
    return "inconclusive"


def verify(
    app_id: int,
    *,
    new_key: Path,
    api_base: str = DEFAULT_API,
    old_key: Path | None = None,
    old_fingerprint: str | None = None,
    listed_fingerprints: Path | None = None,
    timeout: float = 15.0,
) -> dict:
    if not isinstance(app_id, int) or app_id <= 0:
        raise InputError("app id must be a positive integer")
    api_base = _check_api_base(api_base)
    if old_key is None and not (old_fingerprint and listed_fingerprints):
        raise InputError("need evidence about the old key: --old-key, or --old-fingerprint + --listed-fingerprints")
    if old_fingerprint and not FP_RE.fullmatch(old_fingerprint):
        raise InputError("old fingerprint must look like SHA256:<44 base64 chars>")

    checks: dict = {}
    passes: list[bool] = []
    inconclusive = False

    status, body = probe(api_base, make_jwt(app_id, new_key), timeout)
    new = _new_outcome(status, body, app_id)
    checks["new_key"] = {"status": status, "outcome": new}
    passes.append(new == "accepted")
    inconclusive |= new == "inconclusive"

    if old_key is not None:
        status, _ = probe(api_base, make_jwt(app_id, old_key), timeout)
        old = _old_outcome(status)
        checks["old_key"] = {"status": status, "outcome": old}
        passes.append(old == "rejected")
        inconclusive |= old == "inconclusive"

    if old_fingerprint and listed_fingerprints:
        try:
            listed = set(FP_RE.findall(Path(listed_fingerprints).read_text()))
        except OSError as exc:
            raise InputError(f"cannot read {listed_fingerprints}") from exc
        if not listed:
            raise InputError("listed-fingerprints file contains no SHA256 fingerprint")
        checks["old_fingerprint_listed"] = old_fingerprint in listed
        checks["new_fingerprint_listed"] = fingerprint(new_key) in listed
        passes.append(not checks["old_fingerprint_listed"] and checks["new_fingerprint_listed"])

    if all(passes):
        verdict = "rotated"
    elif inconclusive:
        verdict = "inconclusive"
    else:
        verdict = "not-rotated"
    return {"app_id": app_id, "api_base": api_base, "verdict": verdict, "checks": checks}


# A real key body is long (RSA-2048 PEM ~1.6k base64 chars; ed25519 OpenSSH
# ~400). A header followed by less than this is a doc/test mention, not a key.
# Tuned for the RSA keys GitHub Apps use; a small EC key (P-256 PKCS#8 ~180
# chars) could fall under it -- lower it before reusing this as a general scanner.
MIN_BODY_B64 = 200
_BODY_RE = re.compile(KEY_HEADER_RE + r"((?:[A-Za-z0-9+/=\s]|\\n|\\r)*)")


def _has_key_body(text: str) -> bool:
    for m in _BODY_RE.finditer(text):
        body = re.sub(r"\\[nr]|\s", "", m.group(2))
        if len(body) >= MIN_BODY_B64:
            return True
    return False


def scan(repos: list, allow: list[str] | None = None) -> dict:
    """{"key_material": [...], "header_only": [...]} as repo:path, tracked content only."""
    allow = allow or []
    found: dict[str, list[str]] = {"key_material": [], "header_only": []}
    for repo in repos:
        repo = Path(repo)
        inside = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
        )
        if inside.returncode != 0 or Path(inside.stdout.strip()).resolve() != repo.resolve():
            raise InputError(f"not a git repository root: {repo}")
        # Tracked content in BOTH places: the index (what the next commit
        # carries) and the worktree copy of tracked files (unstaged edits).
        # A key in either is key material (final review I1).
        paths: set[str] = set()
        for extra in ([], ["--cached"]):
            r = subprocess.run(
                ["git", "-C", str(repo), "grep", *extra, "-I", "-l", "-E", "-e", KEY_HEADER_RE],
                capture_output=True,
                text=True,
                check=False,
            )
            if r.returncode not in (0, 1):
                raise InputError(f"git grep failed in {repo}")
            paths.update(r.stdout.splitlines())
        for path in sorted(paths):
            if any(fnmatch.fnmatch(path, glob) for glob in allow):
                continue
            blob = subprocess.run(
                ["git", "-C", str(repo), "show", f":{path}"], capture_output=True, text=True, check=False
            )
            texts = [blob.stdout] if blob.returncode == 0 else []
            with contextlib.suppress(OSError):  # deleted in the worktree: the index copy still counts
                texts.append((repo / path).read_text(errors="replace"))
            # Nothing readable at all is treated as key material: fail closed.
            kind = "key_material" if not texts or any(_has_key_body(t) for t in texts) else "header_only"
            found[kind].append(f"{repo.name}:{path}")
    return {k: sorted(v) for k, v in found.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fingerprint")
    f.add_argument("key")
    v = sub.add_parser("verify")
    v.add_argument("--app-id", type=int, required=True)
    v.add_argument("--api-base", default=DEFAULT_API)
    v.add_argument("--new-key", required=True)
    v.add_argument("--old-key")
    v.add_argument("--old-fingerprint")
    v.add_argument("--listed-fingerprints")
    v.add_argument("--timeout", type=float, default=15.0)
    v.add_argument("--json", action="store_true")
    s = sub.add_parser("scan")
    s.add_argument("repos", nargs="+")
    s.add_argument("--allow", action="append", default=[], metavar="GLOB")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "fingerprint":
            print(fingerprint(Path(args.key)))
            return 0
        if args.cmd == "scan":
            found = scan([Path(r) for r in args.repos], args.allow)
            for path in found["header_only"]:
                print(f"  note (header only, no key body): {path}")
            if found["key_material"]:
                print(f"FAIL: {len(found['key_material'])} tracked file(s) carry private-key material (paths only):")
                print("\n".join(f"  {h}" for h in found["key_material"]))
                return 1
            print(f"CLEAN: no tracked private-key material in {len(args.repos)} repo(s)")
            return 0
        result = verify(
            args.app_id,
            new_key=Path(args.new_key),
            api_base=args.api_base,
            old_key=Path(args.old_key) if args.old_key else None,
            old_fingerprint=args.old_fingerprint,
            listed_fingerprints=Path(args.listed_fingerprints) if args.listed_fingerprints else None,
            timeout=args.timeout,
        )
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"S0 row 0.2 GitHub App {result['app_id']}: {result['verdict'].upper()}")
        for name, value in result["checks"].items():
            print(f"  {name}: {value}")
    return 0 if result["verdict"] == "rotated" else 1


if __name__ == "__main__":
    sys.exit(main())
