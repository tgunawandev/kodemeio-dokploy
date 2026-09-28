import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "weekly_rhythm.py"


def test_huge_json_integer_is_refused_without_traceback(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":' + ("9" * 5000) + "}", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(manifest)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stderr.startswith("error: JSON integer token exceeds the supported bound")
    assert "Traceback" not in result.stderr


def _run_tool(script: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT.parent / script), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_real_g2_g3_g4_tool_outputs_assemble_into_weekly_packet(tmp_path):
    """Cross-tool contract: the actual producer CLIs' output must be accepted by the OPS1 packet builder."""
    examples = SCRIPT.parents[1] / "metrics/examples"
    outputs = {
        "g2": _run_tool("ops_metrics.py", "validate-scorecards", str(examples / "product-scorecards.synthetic.json")),
        "g3": _run_tool("ops_metrics.py", "hours-trend", str(examples / "founder-hours.synthetic.json")),
        "g4": _run_tool("budget_reconcile.py", str(examples / "monthly-budget.synthetic.json")),
    }
    limits = {"g2": 14, "g3": 10, "g4": 45}
    components = {}
    for name, output in outputs.items():
        (tmp_path / f"{name}.json").write_text(output, encoding="utf-8")
        components[name] = {
            "path": f"{name}.json",
            "max_age_days": limits[name],
            "evidence_status": "unverified",
            "provenance_note": "synthetic_fixture",
        }
    g4_period = json.loads(outputs["g4"])["period"]
    year, month = (int(part) for part in g4_period.split("-"))
    # A review on the 20th of the month after the reconciled period: the latest closed month is fresh.
    as_of = f"{year + month // 12:04}-{month % 12 + 1:02}-20"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "as_of_date": as_of, "components": components}), encoding="utf-8"
    )

    result = subprocess.run([sys.executable, str(SCRIPT), str(manifest)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)
    assert set(packet["inputs"]) == {"g2", "g3", "g4"}
    for name, output in outputs.items():
        assert packet["inputs"][name]["summary"] == json.loads(output)
        assert packet["inputs"][name]["trust_status"] == "untrusted"
        assert packet["inputs"][name]["freshness_status"] != "missing"
    assert packet["inputs"]["g4"]["freshness_status"] == "fresh"
    assert packet["g1_four_week_execution_status"] == "not_established"
