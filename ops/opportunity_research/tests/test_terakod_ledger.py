"""The Terakod opportunity ledger (Track B, B8) and its selection record.

The ledger is synthetic example evidence: it must validate with the FC2 CLI as a
still-unverified candidate, every source must be a `.test` domain, no owner
review may pretend to be authenticated, and the selection record must name a
candidate that exists in the ledger. Nothing here promotes a candidate.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "examples" / "terakod-ledger.v1.json"
SELECTION = ROOT / "examples" / "terakod-selection.md"
SCRIPT = ROOT / "scripts" / "opportunity_research.py"

sys.path.insert(0, str(ROOT / "scripts"))
from opportunity_research import evaluate, load_document  # noqa: E402


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _front_matter() -> dict[str, str]:
    text = SELECTION.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "selection record needs YAML front matter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_ledger_validates_with_the_fc2_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "validate", str(LEDGER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "candidate-unverified"
    assert report["issues"] == []
    assert report["owner_review_authenticated"] is False
    assert report["source_assertions_verified"] is False


def test_ledger_has_at_least_three_synthetic_candidates() -> None:
    document = _ledger()
    assert len(document["opportunities"]) >= 3
    assert all(item["source_domain"].endswith(".test") for item in document["evidence"])
    assert all(item["source_assertion_status"] == "unverified" for item in document["opportunities"])


def test_no_candidate_claims_a_completed_owner_review() -> None:
    for item in _ledger()["opportunities"]:
        assert item["owner_review"]["status"] == "pending"
        assert item["owner_review"]["reviewed_on"] is None
        assert item["status"] == "unreviewed"


def test_every_evidence_record_is_used_by_a_candidate() -> None:
    document = _ledger()
    used = {ref for item in document["opportunities"] for ref in item["evidence_refs"]}
    assert used == {item["evidence_ref"] for item in document["evidence"]}


def test_a_tampered_claim_is_refused_by_name() -> None:
    document = _ledger()
    document["opportunities"][0]["claim"]["text"] += " changed"
    assert "opportunities[0].claim:hash_mismatch" in evaluate(document)["issues"]


def test_selection_names_a_ledger_candidate_and_is_not_authenticated() -> None:
    fields = _front_matter()
    refs = {item["opportunity_ref"] for item in _ledger()["opportunities"]}
    assert fields["selected_opportunity_ref"] in refs
    assert fields["owner_review_authenticated"] == "false"
    assert fields["decision"] == "TB-D2"
    assert fields["selected_product"] == "Terakon Studio"


def test_selection_record_is_marked_example_content() -> None:
    text = SELECTION.read_text(encoding="utf-8")
    assert "EXAMPLE CONTENT" in text
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", text)  # ISO dates are not phone numbers
    for pattern in (r"https?://", r"\b[^\s@]+@[^\s@]+\.[a-z]{2,}\b", r"\+?\d[\d ()-]{8,}\d"):
        assert not re.search(pattern, text), pattern


@pytest.mark.parametrize("path", [LEDGER])
def test_ledger_loads_through_the_hardened_reader(path: Path) -> None:
    assert load_document(path)["schema_version"] == 1
