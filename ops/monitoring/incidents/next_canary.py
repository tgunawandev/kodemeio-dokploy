"""Local real Next.js SDK delivery test; no application/production error route."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

from pilot import ROOT, request, seed, values, wait_for


def main():
    from playwright.sync_api import sync_playwright

    os.umask(0o077)
    env = values()
    seeded = seed()
    next_repo = ROOT.parents[3] / "kodemeio-next"
    fixture = next_repo / "packages/sentry-config/acceptance/next-app"
    modules = next_repo / "apps/trigunawan-web/node_modules"
    if not modules.is_dir():
        raise ValueError("install the Next repository's locked dependencies first")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=next_repo, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=next_repo, text=True).strip())
    source_hash = hashlib.sha256()
    inputs = [next_repo / "pnpm-lock.yaml", next_repo / "packages/sentry-config/src/index.ts", *fixture.rglob("*")]
    for path in sorted(path for path in inputs if path.is_file()):
        source_hash.update(str(path.relative_to(next_repo)).encode() + b"\0" + path.read_bytes())
    # Every invocation has a new fingerprint: an earlier successful run cannot satisfy acceptance.
    run_id = uuid.uuid4().hex[:12]
    release = f"next-sdk-canary@{head[:12]}-{source_hash.hexdigest()[:12]}-{run_id}"
    with tempfile.TemporaryDirectory(prefix="kod-next-sdk-canary-") as temporary:
        project = Path(temporary) / "app"
        shutil.copytree(fixture, project)
        (project / "node_modules").symlink_to(modules, target_is_directory=True)
        launch_env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "NEXT_TELEMETRY_DISABLED": "1",
            "NEXT_PUBLIC_SENTRY_ENVIRONMENT": "local",
            "NEXT_PUBLIC_SENTRY_RELEASE": release,
            "NEXT_PUBLIC_SENTRY_DSN": f"http://{seeded['canary_public_key']}@127.0.0.1:8789/{seeded['canary_project_id']}",
        }
        output = ROOT / "evidence"
        output.mkdir(mode=0o700, exist_ok=True)
        with (output / "next-runtime.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(next_repo / "next.sh"),
                    "--filter",
                    "@kodemeio/trigunawan-web",
                    "exec",
                    "next",
                    "dev",
                    str(project),
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "8790",
                    "--webpack",
                ],
                cwd=next_repo,
                env=launch_env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:

                def ready():
                    if process.poll() is not None:
                        raise RuntimeError("Next canary exited before readiness")
                    try:
                        import urllib.request

                        urllib.request.urlopen("http://127.0.0.1:8790", timeout=2).close()
                        return True
                    except OSError:
                        return False

                wait_for(ready, "Next.js runtime", timeout=180)
                for runtime in ("server", "edge"):
                    result = request(f"http://127.0.0.1:8790/api/{runtime}", b"", timeout=120)
                    if not result["flushed"]:
                        raise RuntimeError(f"{runtime} SDK did not flush")
                with sync_playwright() as playwright:
                    browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                    browser = playwright.chromium.launch(
                        headless=True, **({"executable_path": browser_path} if browser_path else {})
                    )
                    try:
                        page = browser.new_page()
                        page.goto("http://127.0.0.1:8790")
                        page.get_by_role("button", name="Send synthetic browser error").click()
                        page.wait_for_function("document.body.dataset.canary === 'sent'", timeout=20000)
                    finally:
                        browser.close()
                url = "http://127.0.0.1:8788/incidents"

                def bundles():
                    selected = []
                    for row in request(url, token=env["INCIDENT_READ_TOKEN"]):
                        if row["service"] != "next-sdk-local" or row["generation"] != row["collected_generation"]:
                            continue
                        bundle = request(url + "/" + row["id"], token=env["INCIDENT_READ_TOKEN"])
                        if bundle["release"] == release:
                            selected.append(bundle)
                    types = {
                        item["type"]
                        for bundle in selected
                        for item in bundle["evidence"]
                        if item["kind"] == "exception"
                    }
                    return (
                        selected
                        if {"NextCanaryServerError", "NextCanaryEdgeError", "NextCanaryBrowserError"} <= types
                        else None
                    )

                selected = wait_for(bundles, "Next browser/server/edge evidence", timeout=240)
                output = ROOT / "evidence"
                output.mkdir(mode=0o700, exist_ok=True)
                for bundle in selected:
                    (output / f"{bundle['incident_id']}.json").write_text(json.dumps(bundle, indent=2) + "\n")
                evidence = {
                    "synthetic": True,
                    "runtime": "Next.js with repository-pinned dependencies",
                    "release": release,
                    "inspected_head": head,
                    "source_dirty": dirty,
                    "fixture_and_dependency_sha256": source_hash.hexdigest(),
                    "run_id": run_id,
                    "environment": "local",
                    "browser": True,
                    "server": True,
                    "edge": True,
                    "incident_ids": [b["incident_id"] for b in selected],
                    "production_touched": False,
                    "source_map_upload_verified": False,
                    "business_application_deployment_verified": False,
                }
                (output / "next-sdk-acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n")
                print(json.dumps(evidence, indent=2))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)


if __name__ == "__main__":
    main()
