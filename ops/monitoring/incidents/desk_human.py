"""Export a selected existing human bug ticket; preserve its original Desk id."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from desk import COMPANY, DESK_ORIGIN, ROOT, Desk, selected_text


class ReportText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def human_bundle(desk, ticket_id, service, services):
    if type(ticket_id) is not int or ticket_id < 1 or service not in services:
        raise ValueError("choose an existing ticket and an allowlisted service")
    rows = desk.call(
        "helpdesk.ticket", "read", [[ticket_id], ["id", "name", "description", "company_id", "write_date"]]
    )
    if not rows or rows[0]["company_id"][0] != COMPANY:
        raise ValueError("human report is outside the verified Desk company")
    row = rows[0]
    description = str(row.get("description", ""))
    if len(description.encode()) > 64 * 1024:
        raise ValueError("select a bounded human report before investigation")
    parser = ReportText()
    parser.feed(description)
    selected = selected_text(" ".join(parser.text)[:1000], limit=16000)
    stamp = str(row.get("write_date") or "")
    generation = int(datetime.fromisoformat(stamp).replace(tzinfo=UTC).timestamp())
    mapping = services[service]
    return {
        "schema_version": "incident.v1",
        "incident_id": "INC-" + hashlib.sha256(f"desk:{COMPANY}:{ticket_id}".encode()).hexdigest()[:24],
        "generation": generation,
        "service": service,
        "repo": mapping["repo"],
        "environment": mapping["environment"],
        "source": "desk",
        "state": "open",
        "release": "",
        "observed_at": datetime.now(UTC).isoformat(),
        "ticket": {"origin": DESK_ORIGIN, "id": ticket_id},
        "evidence": [{"kind": "report", "title": selected_text(row["name"][:1000]), "message": selected}],
        "limitations": [
            "Operator-selected Desk report; text truncated to 1000 characters and pattern-redacted; "
            "inspect before model use.",
            "No attachments, requester identity, raw logs or production credentials are exported.",
            "Bug fixes and verification target staging; production deployment requires explicit human acceptance.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", type=int, required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--services", type=Path, default=ROOT / "services.local.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bundle = human_bundle(Desk(), args.ticket, args.service, json.loads(args.services.read_text()))
    if args.apply:
        if not args.output:
            parser.error("export requires --output to a new private evidence file")
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(json.dumps(bundle, indent=2) + "\n")
    print(
        json.dumps(
            {
                "action": "export_selected_human_report",
                "incident_id": bundle["incident_id"],
                "ticket": bundle["ticket"],
                "repo": bundle["repo"],
                "writes_file": args.apply,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
