import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "teracorp_weekly_rhythm.py"


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
