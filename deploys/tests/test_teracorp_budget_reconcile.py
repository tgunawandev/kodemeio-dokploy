from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops/scripts/teracorp_budget_reconcile.py"
SPEC = importlib.util.spec_from_file_location("teracorp_budget_reconcile", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUDGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUDGET)


def monthly_budget() -> dict:
    return {
        "schema_version": 1,
        "period": "2026-09",
        "currency": "IDR",
        "entities": [
            {"entity_id": "terakidz", "budget_amount": "1000000"},
            {"entity_id": "terakod", "budget_amount": "500000"},
        ],
        "invoices": [
            {
                "invoice_ref": "invoice:synthetic-001",
                "invoice_date": "2026-09-05",
                "amount": "300000",
                "allocations": [
                    {"entity_id": "terakidz", "amount": "200000"},
                    {"entity_id": "terakod", "amount": "100000"},
                ],
            },
            {
                "invoice_ref": "invoice:synthetic-002",
                "invoice_date": "2026-09-15",
                "amount": "400000",
                "allocations": [{"entity_id": "terakidz", "amount": "400000"}],
            },
        ],
    }


def test_reconciles_invoice_allocations_against_each_entity_budget() -> None:
    result = BUDGET.reconcile_month(monthly_budget())
    assert result == {
        "schema_version": 1,
        "period": "2026-09",
        "currency": "IDR",
        "entities": [
            {
                "entity_id": "terakidz",
                "budget_amount": "1000000",
                "invoice_total": "600000",
                "variance_amount": "400000",
                "budget_position": "within_budget",
            },
            {
                "entity_id": "terakod",
                "budget_amount": "500000",
                "invoice_total": "100000",
                "variance_amount": "400000",
                "budget_position": "within_budget",
            },
        ],
        "invoice_count": 2,
        "unallocated_amount": "0",
        "total_budget": "1500000",
        "total_invoice_amount": "700000",
        "total_variance": "800000",
    }


def test_reports_over_budget_without_recommending_action() -> None:
    data = monthly_budget()
    data["invoices"][0]["amount"] = "1200000"
    data["invoices"][0]["allocations"] = [{"entity_id": "terakidz", "amount": "1200000"}]
    result = BUDGET.reconcile_month(data)
    assert result["entities"][0]["budget_position"] == "over_budget"
    assert result["entities"][0]["variance_amount"] == "-600000"
    assert "decision" not in result


def test_exactly_reconciles_29_digit_amount() -> None:
    amount = "12345678901234567890123456789"
    data = monthly_budget()
    data["entities"] = [{"entity_id": "terakidz", "budget_amount": amount}]
    data["invoices"] = [
        {
            "invoice_ref": "invoice:large-exact",
            "invoice_date": "2026-09-05",
            "amount": amount,
            "allocations": [{"entity_id": "terakidz", "amount": amount}],
        }
    ]

    result = BUDGET.reconcile_month(data)

    assert result["entities"][0]["invoice_total"] == amount
    assert result["entities"][0]["variance_amount"] == "0"
    assert result["total_invoice_amount"] == amount
    assert result["total_variance"] == "0"


def test_aggregate_boundaries_preserve_max_integer_and_fractional_precision() -> None:
    largest_integer = "9" * 63
    smallest_fraction = "0." + "0" * 61 + "1"
    invoices = [
        {
            "invoice_ref": f"invoice:boundary-{index:04d}",
            "invoice_date": "2026-09-05",
            "amount": largest_integer if index % 2 == 0 else smallest_fraction,
            "allocations": [
                {"entity_id": "terakidz", "amount": largest_integer if index % 2 == 0 else smallest_fraction}
            ],
        }
        for index in range(BUDGET._MAX_INVOICES)
    ]
    data = {
        "schema_version": 1,
        "period": "2026-09",
        "currency": "IDR",
        "entities": [{"entity_id": "terakidz", "budget_amount": "0"}],
        "invoices": invoices,
    }
    fractional_total = str(BUDGET._MAX_INVOICES // 2).zfill(BUDGET._MAX_FRACTIONAL_DIGITS)
    expected_total = f"{int(largest_integer) * (BUDGET._MAX_INVOICES // 2)}.{fractional_total}"

    result = BUDGET.reconcile_month(data)

    assert result["invoice_count"] == BUDGET._MAX_INVOICES
    assert result["total_invoice_amount"] == expected_total
    assert result["entities"][0]["invoice_total"] == expected_total
    assert result["unallocated_amount"] == "0." + "0" * BUDGET._MAX_FRACTIONAL_DIGITS
    assert result["total_variance"] == f"-{expected_total}"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(extra="no"),
        lambda data: data.update(period="2026-13"),
        lambda data: data["entities"][0].update(budget_amount="NaN"),
        lambda data: data["entities"][0].update(budget_amount="1" * 65),
        lambda data: data["invoices"][0].update(invoice_date="2026-10-01"),
        lambda data: data["invoices"][0].update(amount="-1"),
        lambda data: data["invoices"][0]["allocations"][0].update(entity_id="unknown"),
        lambda data: data["invoices"][0]["allocations"][0].update(amount="199999"),
        lambda data: data["invoices"][0].update(currency="USD"),
        lambda data: data["invoices"].append(data["invoices"][0].copy()),
        lambda data: data["entities"].append(data["entities"][0].copy()),
        lambda data: data["invoices"][0].update(description="private details"),
        lambda data: data["invoices"][0].update(invoice_ref="https://example.invalid/invoice"),
    ],
    ids=[
        "unknown-root-key",
        "invalid-period",
        "invalid-budget-decimal",
        "decimal-too-long",
        "invoice-outside-period",
        "negative-invoice-amount",
        "unknown-allocation-entity",
        "allocation-does-not-match-invoice",
        "invoice-currency-not-allowed",
        "duplicate-invoice",
        "duplicate-entity",
        "pii-field-refused",
        "url-not-opaque-reference",
    ],
)
def test_invalid_monthly_budget_refuses(mutate) -> None:
    data = monthly_budget()
    mutate(data)
    with pytest.raises(BUDGET.InputError):
        BUDGET.reconcile_month(data)


def test_empty_invoice_list_is_zero_actual_not_missing_budget() -> None:
    data = monthly_budget()
    data["invoices"] = []
    result = BUDGET.reconcile_month(data)
    assert [row["invoice_total"] for row in result["entities"]] == ["0", "0"]
    assert result["total_invoice_amount"] == "0"


def test_cli_rejects_duplicate_json_keys(tmp_path, capsys) -> None:
    path = tmp_path / "budget.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert BUDGET.main([str(path)]) == 2
    assert "duplicate JSON key" in capsys.readouterr().err


def test_cli_reads_input_without_mutating_it(tmp_path, capsys) -> None:
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(monthly_budget()), encoding="utf-8")
    before = path.read_bytes()
    assert BUDGET.main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["invoice_count"] == 2
    assert path.read_bytes() == before


def test_cli_rejects_oversized_input_before_parsing(tmp_path, capsys) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (BUDGET._MAX_INPUT_BYTES + 1))
    assert BUDGET.main([str(path)]) == 2
    assert "must not exceed" in capsys.readouterr().err


def test_cli_rejects_invalid_utf8_without_traceback(tmp_path, capsys) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff")
    assert BUDGET.main([str(path)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_rejects_oversized_json_integer_without_traceback(tmp_path, capsys) -> None:
    path = tmp_path / "large-integer.json"
    path.write_text('{"schema_version":' + "9" * 5_000 + "}", encoding="utf-8")

    assert BUDGET.main([str(path)]) == 2

    error = capsys.readouterr().err
    assert "JSON integer must not exceed 64 digits" in error
    assert "Traceback" not in error


def test_script_has_no_network_or_database_dependencies() -> None:
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "subprocess", "sqlite3", "psycopg2"})


def test_checked_in_budget_example_is_synthetic_and_valid() -> None:
    example = SCRIPT.parents[1] / "metrics/examples/monthly-budget.synthetic.json"
    payload = json.loads(example.read_text(encoding="utf-8"))
    result = BUDGET.reconcile_month(payload)
    assert result["period"] == "2026-09"
    assert result["invoice_count"] == 2
