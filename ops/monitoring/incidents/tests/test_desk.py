import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desk import Desk, selected_text  # noqa: E402
from desk_human import human_bundle  # noqa: E402


def test_preview_never_creates_a_ticket_and_rejects_oversize_or_credentials():
    desk = Desk()
    desk.call = Mock(side_effect=AssertionError("preview must not write"))
    preview = desk.ticket("incident_123", "Synthetic test", "email=a@example.invalid token=secret-value")
    assert preview["origin"] == "https://desk.idtpp.com"
    assert preview["production_deployment"] == "human_acceptance_required"
    assert "secret-value" not in preview["description"]
    assert "a@example.invalid" not in preview["description"]
    with pytest.raises(ValueError):
        selected_text("a" * 16001)
    with pytest.raises(ValueError):
        desk.ticket("../../foreign", "test", "test")


def test_ticket_post_write_scope_and_stable_external_id():
    desk = Desk(apply=True)
    desk.call = Mock(
        side_effect=[
            {"ids": [530]},
            [{"id": 530, "number": "HT00530", "company_id": [1, "Desk"], "team_id": [3, "Development"]}],
        ]
    )
    result = desk.ticket("incident_123", "Test", "<script>untrusted</script>")
    assert result["ticket_number"] == "HT00530"
    args = desk.call.call_args_list[0].args[2]
    assert args[1][0][0] == "__import__.veronica_incident_123"
    assert "<script>" not in args[1][0][2]
    assert desk.call.call_args_list[0].kwargs["write"]
    desk.call = Mock(side_effect=[{"ids": [530]}, [{"company_id": [2, "Other"], "team_id": [3, "Development"]}]])
    with pytest.raises(RuntimeError, match="scope"):
        desk.ticket("incident_123", "Test", "test")


def test_notes_preserve_human_team_and_repeated_note_key():
    desk = Desk()
    desk.call = Mock(return_value=[{"company_id": [1, "Desk"], "team_id": [1, "IT Operations"]}])
    first = desk.note(530, "review", "Staging review only")
    second = desk.note(530, "review", "Staging review only")
    assert first["external_id"] == second["external_id"]
    assert len(desk.call.call_args_list) == 2
    desk.call = Mock(return_value=[{"company_id": [2, "Other"], "team_id": [1, "IT Operations"]}])
    with pytest.raises(ValueError, match="scope"):
        desk.note(530, "review", "test")
    with pytest.raises(ValueError):
        desk.note(530, "deploy-prod", "test")


def test_selected_human_report_retains_ticket_and_drops_markup_and_requester():
    desk = Desk()
    desk.call = Mock(
        return_value=[
            {
                "id": 530,
                "name": "Human reported bug",
                "description": "<p>Broken widget token=private-token</p><script>embedded()</script>",
                "company_id": [1, "Desk"],
                "write_date": "2026-10-05 04:00:00",
            }
        ]
    )
    services = {"web": {"repo": "kodemeio-next", "environment": "staging"}}
    bundle = human_bundle(desk, 530, "web", services)
    assert bundle["source"] == "desk" and bundle["ticket"]["id"] == 530
    assert bundle["environment"] == "staging"
    assert "embedded" not in json.dumps(bundle) and "private-token" not in json.dumps(bundle)
    assert "requester" not in desk.call.call_args.args[2][1]
    with pytest.raises(ValueError):
        human_bundle(desk, 530, "foreign", services)


def test_import_response_alone_cannot_claim_a_note_reached_the_selected_ticket():
    desk = Desk(apply=True)
    desk.call = Mock(
        side_effect=[
            [{"company_id": [1, "Desk"], "team_id": [3, "Development"]}],
            {"ids": [6208]},
            [{"model": "helpdesk.ticket", "res_id": 999, "subtype_id": [2, "Note"]}],
        ]
    )
    with pytest.raises(RuntimeError, match="verification"):
        desk.note(530, "triage", "Selected synthetic report")
