"""Roadmap row 0.2 -- GitHub App credential rotation verifier.

Never contacts GitHub. A local fake answers `GET /app` the way GitHub's REST
docs describe (200 + the App object for a JWT signed by a current key, 401 for
anything else); it really verifies the RS256 signature with openssl against the
"current" public key. The status codes are from the docs, not a recorded live
response -- the runbook makes the founder's first live run the recording.
Throwaway RSA keys are generated in tmp dirs; none is ever tracked.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "ops/scripts/teracorp_s0_github_app.py"
SPEC = importlib.util.spec_from_file_location("teracorp_s0_github_app", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GH)

APP_ID = 424242

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


def b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@pytest.fixture(scope="module")
def keys():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("old", "new"):
            subprocess.run(
                ["openssl", "genrsa", "-out", str(d / f"{name}.pem"), "2048"], check=True, capture_output=True
            )
            subprocess.run(
                ["openssl", "rsa", "-in", str(d / f"{name}.pem"), "-pubout", "-out", str(d / f"{name}.pub")],
                check=True,
                capture_output=True,
            )
        yield d


class FakeGitHub:
    """`GET /app` only. mode: normal | status:<code> | wrong-id | slow."""

    def __init__(self, current_pub: Path):
        self.current_pub = current_pub
        self.mode = "normal"
        self.seen_auth: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                fake.seen_auth.append(auth)
                if self.path != "/app":
                    return self._send(404, {"message": "Not Found"})
                if fake.mode.startswith("status:"):
                    return self._send(int(fake.mode.split(":")[1]), {"message": "synthetic"})
                if fake.mode == "slow":
                    time.sleep(3)
                if not auth.startswith("Bearer ") or not fake.valid(auth[7:]):
                    return self._send(
                        401,
                        {
                            "message": "A JSON web token could not be decoded",
                            "documentation_url": "https://docs.github.com/rest",
                        },
                    )
                app_id = APP_ID + 1 if fake.mode == "wrong-id" else APP_ID
                return self._send(200, {"id": app_id, "slug": "synthetic-app", "name": "Synthetic"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def valid(self, token: str) -> bool:
        try:
            header, payload, sig = token.split(".")
            claims = json.loads(b64url_decode(payload))
        except ValueError:
            return False
        now = time.time()
        if str(claims.get("iss")) != str(APP_ID) or not (claims["iat"] <= now < claims["exp"]):
            return False
        if claims["exp"] - claims["iat"] > 600:
            return False
        with tempfile.TemporaryDirectory() as tmp:
            sig_path = Path(tmp) / "sig"
            sig_path.write_bytes(b64url_decode(sig))
            r = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", str(self.current_pub), "-signature", str(sig_path)],
                input=f"{header}.{payload}".encode(),
                capture_output=True,
                check=False,
            )
        return r.returncode == 0

    def close(self):
        self.server.shutdown()


@pytest.fixture
def github(keys):
    fake = FakeGitHub(keys / "new.pub")
    yield fake
    fake.close()


# --- fingerprint ------------------------------------------------------------------


def test_fingerprint_matches_githubs_documented_recipe(keys):
    der = subprocess.run(
        ["openssl", "rsa", "-in", str(keys / "old.pem"), "-pubout", "-outform", "DER"],
        capture_output=True,
        check=True,
    ).stdout
    digest = subprocess.run(["openssl", "sha256", "-binary"], input=der, capture_output=True, check=True).stdout
    expected = "SHA256:" + base64.b64encode(digest).decode()
    assert GH.fingerprint(keys / "old.pem") == expected
    assert GH.fingerprint(keys / "old.pem") != GH.fingerprint(keys / "new.pem")


def test_fingerprint_of_non_key_is_input_error(tmp_path):
    bad = tmp_path / "x.pem"
    bad.write_text("not a key")
    with pytest.raises(GH.InputError):
        GH.fingerprint(bad)


# --- JWT --------------------------------------------------------------------------


def test_jwt_claims_follow_github_rules(keys):
    token = GH.make_jwt(APP_ID, keys / "new.pem", now=1_000_000)
    header, payload, _ = token.split(".")
    assert json.loads(b64url_decode(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(b64url_decode(payload))
    assert claims["iss"] == str(APP_ID)
    assert claims["iat"] == 1_000_000 - 60  # clock-drift backdate per GitHub docs
    assert claims["exp"] - claims["iat"] <= 600


# --- verify: key mode ---------------------------------------------------------------


def test_rotation_verified_old_rejected_new_accepted(keys, github):
    result = GH.verify(APP_ID, api_base=github.base, old_key=keys / "old.pem", new_key=keys / "new.pem")
    assert result["verdict"] == "rotated", result
    assert result["checks"]["old_key"] == {"status": 401, "outcome": "rejected"}
    assert result["checks"]["new_key"] == {"status": 200, "outcome": "accepted"}


def test_not_rotated_when_old_key_still_works(keys):
    fake = FakeGitHub(keys / "old.pub")  # GitHub still trusts the old key
    try:
        result = GH.verify(APP_ID, api_base=fake.base, old_key=keys / "old.pem", new_key=keys / "new.pem")
    finally:
        fake.close()
    assert result["verdict"] == "not-rotated"
    assert result["checks"]["old_key"]["outcome"] == "accepted"
    assert result["checks"]["new_key"]["outcome"] == "rejected"


@pytest.mark.parametrize("mode", ["status:500", "status:403", "status:404", "status:429"])
def test_anything_but_401_for_old_key_is_inconclusive(keys, github, mode):
    github.mode = mode
    result = GH.verify(APP_ID, api_base=github.base, old_key=keys / "old.pem", new_key=keys / "new.pem")
    assert result["verdict"] == "inconclusive"
    assert result["checks"]["old_key"]["outcome"] == "inconclusive"


def test_new_key_answering_for_another_app_is_inconclusive(keys, github):
    github.mode = "wrong-id"
    result = GH.verify(APP_ID, api_base=github.base, old_key=keys / "old.pem", new_key=keys / "new.pem")
    assert result["verdict"] == "inconclusive"
    assert result["checks"]["new_key"]["outcome"] == "inconclusive"


def test_network_error_is_inconclusive_not_rejected(keys):
    result = GH.verify(APP_ID, api_base="http://127.0.0.1:9", old_key=keys / "old.pem", new_key=keys / "new.pem")
    assert result["verdict"] == "inconclusive"
    assert result["checks"]["old_key"] == {"status": None, "outcome": "inconclusive"}


def test_timeout_is_inconclusive(keys, github):
    github.mode = "slow"
    result = GH.verify(APP_ID, api_base=github.base, old_key=keys / "old.pem", new_key=keys / "new.pem", timeout=0.5)
    assert result["verdict"] == "inconclusive"


def test_plain_http_to_a_remote_host_is_refused(keys):
    with pytest.raises(GH.InputError, match="https"):
        GH.verify(APP_ID, api_base="http://api.github.com", old_key=keys / "old.pem", new_key=keys / "new.pem")


# --- verify: fingerprint mode (the old .pem is not held) -------------------------------


def listing(tmp_path: Path, *fps: str) -> Path:
    p = tmp_path / "listed.txt"
    p.write_text("Private keys\n" + "\n".join(f"Added on Sep 28\n{fp}\nDelete" for fp in fps))
    return p


def test_fingerprint_mode_rotated(keys, github, tmp_path):
    old_fp = GH.fingerprint(keys / "old.pem")
    listed = listing(tmp_path, GH.fingerprint(keys / "new.pem"))
    result = GH.verify(
        APP_ID, api_base=github.base, new_key=keys / "new.pem", old_fingerprint=old_fp, listed_fingerprints=listed
    )
    assert result["verdict"] == "rotated", result
    assert result["checks"]["old_fingerprint_listed"] is False
    assert result["checks"]["new_fingerprint_listed"] is True


def test_fingerprint_mode_old_still_listed(keys, github, tmp_path):
    old_fp = GH.fingerprint(keys / "old.pem")
    listed = listing(tmp_path, old_fp, GH.fingerprint(keys / "new.pem"))
    result = GH.verify(
        APP_ID, api_base=github.base, new_key=keys / "new.pem", old_fingerprint=old_fp, listed_fingerprints=listed
    )
    assert result["verdict"] == "not-rotated"


def test_fingerprint_mode_empty_listing_is_input_error(keys, github, tmp_path):
    empty = tmp_path / "listed.txt"
    empty.write_text("nothing copied")
    with pytest.raises(GH.InputError, match="no SHA256"):
        GH.verify(
            APP_ID,
            api_base=github.base,
            new_key=keys / "new.pem",
            old_fingerprint="SHA256:" + "A" * 43 + "=",
            listed_fingerprints=empty,
        )


def test_verify_requires_some_evidence_about_the_old_key(keys, github):
    with pytest.raises(GH.InputError, match="old"):
        GH.verify(APP_ID, api_base=github.base, new_key=keys / "new.pem")


# --- scan -------------------------------------------------------------------------------


def git_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for rel, body in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        subprocess.run(["git", "-C", str(path), "add", rel], check=True)
    return path


def test_scan_finds_tracked_private_key_and_ignores_untracked(tmp_path, keys):
    pem = (keys / "old.pem").read_text()
    repo = git_repo(tmp_path / "r1", {"ok.txt": "hello", "deploy/app.pem": pem})
    (repo / "untracked.pem").write_text(pem)
    assert GH.scan([repo]) == {"key_material": [f"{repo.name}:deploy/app.pem"], "header_only": []}


def test_scan_finds_key_embedded_in_a_json_string(tmp_path, keys):
    pem = (keys / "old.pem").read_text().replace("\n", "\\n")
    repo = git_repo(
        tmp_path / "r1b", {"cfg.json": json.dumps({"private_key": "PLACEHOLDER"}).replace("PLACEHOLDER", pem)}
    )
    assert GH.scan([repo])["key_material"] == [f"{repo.name}:cfg.json"]


@pytest.mark.parametrize(
    "header",
    ["-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----", "-----BEGIN OPENSSH PRIVATE KEY-----"],
)
def test_scan_matches_the_private_key_header_family(tmp_path, header):
    body = "\n".join(["A" * 64] * 5)
    repo = git_repo(tmp_path / "r2", {"k": f"{header}\n{body}\n"})
    assert GH.scan([repo])["key_material"] == [f"{repo.name}:k"]


def test_scan_header_only_mentions_are_noted_not_failed(tmp_path):
    repo = git_repo(
        tmp_path / "r2b",
        {
            "t.bats": "printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\\n' > key.md\n",
            "doc.md": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
        },
    )
    assert GH.scan([repo]) == {"key_material": [], "header_only": [f"{repo.name}:doc.md", f"{repo.name}:t.bats"]}


def test_scan_reads_the_tracked_version_not_the_worktree(tmp_path, keys):
    repo = git_repo(tmp_path / "r2c", {"a.pem": (keys / "old.pem").read_text()})
    (repo / "a.pem").write_text("-----BEGIN PRIVATE KEY-----\n")  # unstaged edit hides nothing
    assert GH.scan([repo])["key_material"] == [f"{repo.name}:a.pem"]


def test_scan_clean_and_allow_glob(tmp_path, keys):
    repo = git_repo(tmp_path / "r3", {"tests/fixtures/dummy.pem": (keys / "old.pem").read_text()})
    assert GH.scan([repo], allow=["tests/fixtures/*"]) == {"key_material": [], "header_only": []}
    clean = git_repo(tmp_path / "r4", {"README": "public key only: -----BEGIN PUBLIC KEY-----"})
    assert GH.scan([clean]) == {"key_material": [], "header_only": []}


def test_scan_non_repo_is_input_error(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(GH.InputError, match="not a git"):
        GH.scan([tmp_path / "plain"])


# --- CLI: exit codes and no secret ever printed -------------------------------------------


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def test_cli_verify_exit_codes_and_no_key_or_jwt_leak(keys, github):
    ok = cli("verify", "--app-id", str(APP_ID), "--api-base", github.base, "--old-key", str(keys / "old.pem"),
             "--new-key", str(keys / "new.pem"))  # fmt: skip
    assert ok.returncode == 0, ok.stderr
    assert "ROTATED" in ok.stdout
    printed = ok.stdout + ok.stderr
    assert "PRIVATE KEY" not in printed
    for auth in github.seen_auth:
        assert auth[7:] not in printed  # the JWT itself is never printed
    github.mode = "status:500"
    bad = cli("verify", "--app-id", str(APP_ID), "--api-base", github.base, "--old-key", str(keys / "old.pem"),
              "--new-key", str(keys / "new.pem"))  # fmt: skip
    assert bad.returncode == 1
    assert "INCONCLUSIVE" in bad.stdout


def test_cli_fingerprint_and_scan(tmp_path, keys):
    out = cli("fingerprint", str(keys / "new.pem"))
    assert out.returncode == 0
    assert out.stdout.strip() == GH.fingerprint(keys / "new.pem")
    repo = git_repo(tmp_path / "r5", {"a.pem": (keys / "old.pem").read_text()})
    hit = cli("scan", str(repo))
    assert hit.returncode == 1
    assert "r5:a.pem" in hit.stdout
    assert "PRIVATE KEY" not in hit.stdout.replace("private key", "")
    clean = git_repo(tmp_path / "r6", {"a.txt": "x"})
    assert cli("scan", str(clean)).returncode == 0
    assert cli("scan", str(tmp_path)).returncode == 2
