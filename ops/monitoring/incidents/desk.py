"""Operator ticket bridge to the existing Desk. No credential is forwarded to OMP.

Uses Odoo's transactional external-id import for stable tickets/internal notes.
Preview is the default. Production code deployment is never an available action.
"""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ODOO = ROOT.parents[3] / "kodemeio-odoo"
DESK_ORIGIN = "https://desk.idtpp.com"
COMPANY = 1
TEAM = 3  # verified live: IT Development; reuse the existing team and stages
PROFILE = "idtpp-tpp-odoo-helpdesk"
CONTEXT = {
    "allowed_company_ids": [COMPANY],
    "default_company_id": COMPANY,
    "mail_create_nosubscribe": True,
    "mail_auto_subscribe_no_notify": True,
    "mail_notify_force_send": False,
    "tracking_disable": True,
}


def selected_text(value, limit=16000):
    text = str(value)
    if len(text.encode()) > limit:
        raise ValueError("selected report exceeds the 16 KiB ticket budget")
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+[\w.+/=-]+", "[credential]", text)
    text = re.sub(
        r"(?i)(?:password|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*[^\s,;]+", "[credential]", text
    )
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+", "[credential]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"https?://[^\s<>]*?/webhooks/[^\s<>]+", "[ingest-url]", text)
    return text


class Desk:
    def __init__(self, apply=False):
        self.apply = apply

    def call(self, model, method, args, *, write=False):
        if write and not self.apply:
            raise ValueError("write requires an explicit --apply")
        command = [
            str(ODOO / "odoo.sh"),
            "helpdesk",
            "prod",
            "--json",
            "shell",
            "call",
            model,
            method,
            json.dumps(args),
            "--kwargs",
            json.dumps({"context": CONTEXT}),
        ]
        # shell call carries read and write methods and has no --yes flag;
        # this bridge's explicit --apply is its write gate.
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "KCTL_ODOO_PROFILE": PROFILE}
        result = subprocess.run(command, cwd=ODOO, env=env, check=False, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("Desk operation failed; inspect the operator CLI without exposing credentials")
        return json.loads(result.stdout)

    def ticket(self, key, subject, description):
        if not re.fullmatch(r"[a-z0-9_]{1,100}", key) or not subject or len(subject) > 180:
            raise ValueError("invalid ticket key or subject")
        description = selected_text(description)
        external = "__import__.veronica_" + key
        preview = {
            "action": "upsert_ticket",
            "origin": DESK_ORIGIN,
            "company_id": COMPANY,
            "team_id": TEAM,
            "external_id": external,
            "subject": subject,
            "description": description,
            "fix_environment": "staging",
            "production_deployment": "human_acceptance_required",
        }
        if not self.apply:
            return preview
        result = self.call(
            "helpdesk.ticket",
            "load",
            [
                ["id", "name", "description", "company_id/.id", "team_id/.id"],
                [[external, subject, "<pre>" + html.escape(description) + "</pre>", str(COMPANY), str(TEAM)]],
            ],
            write=True,
        )
        ids = result.get("ids") or []
        if len(ids) != 1 or type(ids[0]) is not int:
            raise RuntimeError("Desk rejected the ticket import; no success is claimed")
        ticket_id = ids[0]
        records = self.call("helpdesk.ticket", "read", [[ticket_id], ["id", "number", "name", "company_id", "team_id"]])
        if not records or records[0]["company_id"][0] != COMPANY or records[0]["team_id"][0] != TEAM:
            raise RuntimeError("Desk post-write scope verification failed")
        return {
            "ticket_id": ticket_id,
            "ticket_number": records[0].get("number"),
            "ticket_url": f"{DESK_ORIGIN}/web#id={ticket_id}&model=helpdesk.ticket&view_type=form",
            "company_id": COMPANY,
            "team_id": TEAM,
        }

    def note(self, ticket_id, kind, text):
        if (
            type(ticket_id) is not int
            or ticket_id < 1
            or kind not in {"triage", "staging-fix", "review", "acceptance", "status"}
        ):
            raise ValueError("invalid ticket action")
        text = selected_text(text)
        records = self.call("helpdesk.ticket", "read", [[ticket_id], ["id", "company_id", "team_id"]])
        if (
            not records
            or records[0]["company_id"][0] != COMPANY
            or (records[0]["team_id"] and records[0]["team_id"][0] not in {1, 2, 3})
        ):
            raise ValueError("ticket is outside the verified Desk scope")
        key = hashlib.sha256(f"{ticket_id}:{kind}:{text}".encode()).hexdigest()[:32]
        external = "__import__.veronica_note_" + key
        if not self.apply:
            return {
                "action": "internal_note",
                "ticket_id": ticket_id,
                "kind": kind,
                "text": text,
                "external_id": external,
                "production_deployment": "human_acceptance_required",
            }
        result = self.call(
            "mail.message",
            "load",
            [
                ["id", "model", "res_id", "body", "message_type", "subtype_id/id"],
                [
                    [
                        external,
                        "helpdesk.ticket",
                        str(ticket_id),
                        "<pre>VERONICA " + kind + " (operator submitted)\n" + html.escape(text) + "</pre>",
                        "comment",
                        "mail.mt_note",
                    ]
                ],
            ],
            write=True,
        )
        ids = result.get("ids") or []
        if len(ids) != 1 or type(ids[0]) is not int:
            raise RuntimeError("Desk rejected the internal note; no success is claimed")
        messages = self.call("mail.message", "read", [[ids[0]], ["id", "model", "res_id", "subtype_id"]])
        if (
            not messages
            or messages[0]["model"] != "helpdesk.ticket"
            or messages[0]["res_id"] != ticket_id
            or not messages[0]["subtype_id"]
        ):
            raise RuntimeError("Desk post-write note verification failed")
        subtype = self.call("mail.message.subtype", "read", [[messages[0]["subtype_id"][0]], ["internal"]])
        if not subtype or subtype[0].get("internal") is not True:
            raise RuntimeError("Desk note is not internal; inspect the operator ticket")
        return {"ticket_id": ticket_id, "note_id": ids[0], "kind": kind, "internal": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ticket", "note"))
    parser.add_argument("--key")
    parser.add_argument("--subject")
    parser.add_argument("--ticket", type=int)
    parser.add_argument("--kind", default="status")
    parser.add_argument(
        "--body", type=Path, required=True, help="selected operator-reviewed text; never raw logs or transcripts"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with args.body.open("rb") as stream:
        raw = stream.read(16001)
    if len(raw) > 16000:
        parser.error("body exceeds 16 KiB")
    body = raw.decode()
    desk = Desk(apply=args.apply)
    result = (
        desk.ticket(args.key or "", args.subject or "", body)
        if args.command == "ticket"
        else desk.note(args.ticket, args.kind, body)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
