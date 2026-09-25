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


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.invalid.*.json")), ids=lambda p: p.name)
def test_invalid_examples_fail(path):
    errors = list(validator_for(_schema_for_example(path)).iter_errors(json.loads(path.read_text())))
    assert errors, f"{path.name} unexpectedly validated"
