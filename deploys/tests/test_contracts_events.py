from __future__ import annotations

import json

import pytest
from contracts_lib import CONTRACTS, iter_schemas, load, validator_for
from jsonschema import Draft202012Validator

EXAMPLES = CONTRACTS / "examples"


def _schema_for_example(example):
    # order.requested.v1.valid.json -> events/order.requested.v1.schema.json
    stem = example.name.split(".valid")[0].split(".invalid")[0]
    return CONTRACTS / example.parent.name / f"{stem}.schema.json"


def test_contracts_dir_has_schemas():
    assert iter_schemas(), "no schemas found under contracts/"


@pytest.mark.parametrize("path", iter_schemas(), ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path):
    schema = load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$id"].startswith("https://kodeme.io/contracts/")
    assert schema["$id"].endswith(path.relative_to(CONTRACTS).as_posix())


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.valid.json")), ids=lambda p: p.name)
def test_valid_examples_validate(path):
    validator_for(_schema_for_example(path)).validate(json.loads(path.read_text()))


# Each invalid example must fail for the reason its file name promises, not
# merely fail: `<stem>.invalid.<reason>.json` -> (validator keyword, instance
# path, offending value named in the message or None). A new invalid example
# with no entry here fails the test until its reason is declared.
EXPECTED_INVALID = {
    "extra-field": ("additionalProperties", (), "'note'"),
    "financial-class": ("enum", ("data_classification",), "'financial'"),
    "pii-in-payload": ("additionalProperties", ("payload",), "'email'"),
    "bad-work-order-id": ("pattern", ("work_order_id",), "'WO-bad-id'"),
    "bad-state": ("enum", ("state",), "'in_progress'"),
    "refs-extra-field": ("additionalProperties", ("refs",), "'secret'"),
}


def _reason(example):
    # order.requested.v1.invalid.extra-field.json -> extra-field
    return example.name.split(".invalid.", 1)[1].removesuffix(".json")


def _unexpected_failures(errors, expected):
    """Errors that do NOT match the declared reason (empty list = all match)."""
    keyword, path, token = expected
    return [
        error
        for error in errors
        if error.validator != keyword
        or tuple(error.absolute_path) != path
        or (token is not None and token not in error.message)
    ]


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.invalid.*.json")), ids=lambda p: p.name)
def test_invalid_examples_fail(path):
    reason = _reason(path)
    assert reason in EXPECTED_INVALID, f"{path.name}: declare its expected failure in EXPECTED_INVALID"
    errors = list(validator_for(_schema_for_example(path)).iter_errors(json.loads(path.read_text())))
    assert errors, f"{path.name} unexpectedly validated"
    wrong = _unexpected_failures(errors, EXPECTED_INVALID[reason])
    assert not wrong, f"{path.name} failed for another reason: {[e.message for e in wrong]}"


def test_invalid_example_check_rejects_the_wrong_reason():
    # An "extra-field" example that actually fails on data_classification must not pass.
    example = json.loads((EXAMPLES / "events/order.requested.v1.invalid.financial-class.json").read_text())
    errors = list(validator_for(CONTRACTS / "events/order.requested.v1.schema.json").iter_errors(example))
    assert _unexpected_failures(errors, EXPECTED_INVALID["extra-field"])
    assert not _unexpected_failures(errors, EXPECTED_INVALID["financial-class"])
