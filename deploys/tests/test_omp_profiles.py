"""The coding personas do not inherit business-agent or legacy broker authority."""

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "contracts/coding_agents"


def profile(name):
    return json.loads((DIRECTORY / f"{name}-omp.json").read_text())


def validator():
    schema = json.loads((DIRECTORY / "profile.v1.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("name", ["friday", "veronica"])
def test_profile_validates(name):
    validator().validate(profile(name))


def test_review_cannot_gain_developer_or_publish_role():
    record = profile("veronica")
    record["publication"] = "operator-only-draft-pr"
    with pytest.raises(jsonschema.ValidationError):
        validator().validate(record)
    record = profile("veronica")
    record["roles"].append("developer")
    with pytest.raises(jsonschema.ValidationError):
        validator().validate(record)


@pytest.mark.parametrize(
    "key,value",
    [
        ("production_access", True),
        ("automatic_paid_fallback", True),
        ("max_parallel_workers", 10),
        ("optional_worker_model", "deepseek/deepseek-v4-flash"),
    ],
)
def test_unsafe_or_obsolete_mapping_is_rejected(key, value):
    record = profile("friday")
    record[key] = value
    with pytest.raises(jsonschema.ValidationError):
        validator().validate(record)


def test_principals_are_distinct_from_legacy_friday():
    assert profile("friday")["principal"] != profile("veronica")["principal"]
    assert profile("friday")["principal"] != "agent:friday"
