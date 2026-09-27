from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "teracorp_contractor_readiness.py"
SPEC = importlib.util.spec_from_file_location("teracorp_contractor_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
G5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G5)


def test_cli_rejects_json_nesting_above_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = tmp_path / "deep.json"
    payload.write_text("{" * (G5._MAX_JSON_NESTING + 1) + "0" + "}" * (G5._MAX_JSON_NESTING + 1), encoding="utf-8")

    assert G5.main([str(payload)]) == 2
    assert f"nesting must not exceed {G5._MAX_JSON_NESTING} levels" in capsys.readouterr().err


def test_brackets_inside_json_string_do_not_count_as_nesting() -> None:
    raw = json.dumps({"text": "[]{}" * (G5._MAX_JSON_NESTING * 10)})

    G5._check_json_nesting(raw)


def test_cli_maps_parser_recursion_error_to_sanitized_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "parser-failure.json"
    payload.write_text("{}", encoding="utf-8")

    def raise_recursion_error(*args: object, **kwargs: object) -> object:
        raise RecursionError("internal parser details")

    monkeypatch.setattr(G5.json, "loads", raise_recursion_error)
    assert G5.main([str(payload)]) == 2
    error = capsys.readouterr().err
    assert "input JSON exceeds parser safety limits" in error
    assert "internal parser details" not in error
