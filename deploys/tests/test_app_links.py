"""Terakidz physical task boxes with QR -> app activities (Track B slice TB2; spec section 4 "B2").

TB-D4 (a): a QR on the box insert is an OPEN link to a companion activity, never a single-use
activation code -- the physical box is the product. template.v1 has no QR element, so each QR
is a committed, licensed own-work IMAGE asset that the insert places by `asset:` key.

1. **Contract** -- `contracts/app_links/app_links.v1.schema.json` and the Terakidz instance
   `terakidz.v1.json`: one `base` host (FOUNDER: confirm; a placeholder must be a `.test` host,
   so a placeholder can never print a live-looking link) and the activity ids the box opens.
2. **Catalog** -- every id exists in the SPA catalog snapshot `terakidz.catalog-ids.json`, which
   kodemeio-react regenerates in its own vitest and fails on drift, and every id is in a FREE
   collection: a printed QR never lands on a paywall.
3. **Assets** -- `templates/terakidz-box-insert/assets/own-work.json` is the licence evidence:
   one own-work image per id, its link exactly `<base>/a/<id>`, its sha256 the PNG's bytes. The
   byte-exact regeneration and the decode check live beside the generator
   (`ops/app_links/tests`, run with the generator's pinned dependencies).
4. **Insert** -- the box insert validates as template.v1, places exactly those QR assets, holds
   the Terakidz voice and places the kit disclaimer.
5. **Line** -- `product_lines/terakidz-box.yaml` ships one physical box and its insert, both
   `publish: none`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import struct
import zlib

import pytest
import yaml
from contracts_lib import CONTRACTS, validator_for
from test_contracts_templates import FORBIDDEN_LAYOUT_MARKUP, TEMPLATES_SCHEMA
from test_contracts_templates import resolve_problems as template_resolve_problems
from test_terakidz_line import (
    EMAIL_RE,
    NIK_RE,
    PHONE_RE,
    URL_RE,
    template_texts,
    var_in_style_or_script,
    voice_problems,
)

REPO = CONTRACTS.parent
APP_LINKS = CONTRACTS / "app_links"
SCHEMA = APP_LINKS / "app_links.v1.schema.json"
LINKS_PATH = APP_LINKS / "terakidz.v1.json"
CATALOG_PATH = APP_LINKS / "terakidz.catalog-ids.json"
INSERT_ID = "terakidz-box-insert"
INSERT_DIR = REPO / "templates" / INSERT_ID
ASSETS_DIR = INSERT_DIR / "assets"
MANIFEST_PATH = ASSETS_DIR / "own-work.json"
GENERATOR = REPO / "ops" / "app_links" / "gen_app_qr.py"
LINE_PATH = REPO / "product_lines" / "terakidz-box.yaml"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def links() -> dict:
    return json.loads(LINKS_PATH.read_text())


def catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text())


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def insert() -> dict:
    return yaml.safe_load((INSERT_DIR / "template.yaml").read_text())


def insert_layout() -> str:
    return (INSERT_DIR / insert()["layout"]).read_text()


def line() -> dict:
    return yaml.safe_load(LINE_PATH.read_text())


def errors(payload: dict) -> list[str]:
    return [error.message for error in validator_for(SCHEMA).iter_errors(payload)]


def link_for(payload: dict, activity_id: str) -> str:
    return f"{payload['base']}/a/{activity_id}"


def png_header(data: bytes) -> dict:
    """The IHDR fields of a PNG, after checking its signature and the IHDR CRC."""
    assert data[:8] == PNG_SIGNATURE, "not a PNG"
    length, kind = struct.unpack(">I4s", data[8:16])
    assert kind == b"IHDR" and length == 13
    body = data[16:29]
    (crc,) = struct.unpack(">I", data[29:33])
    assert zlib.crc32(kind + body) == crc, "IHDR CRC mismatch"
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
    return {"width": width, "height": height, "depth": depth, "colour": colour, "interlace": interlace}


# --- 1. contract -----------------------------------------------------------------


def test_the_schema_is_a_self_contained_2020_12_contract():
    schema = json.loads(SCHEMA.read_text())
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["$id"] == "https://kodeme.io/contracts/app_links/app_links.v1.schema.json"
    assert schema["additionalProperties"] is False
    assert not _keys_named(schema, "$ref")


def _keys_named(node, name: str) -> list:
    if isinstance(node, dict):
        return [key for key in node if key == name] + [
            hit for value in node.values() for hit in _keys_named(value, name)
        ]
    if isinstance(node, list):
        return [hit for value in node for hit in _keys_named(value, name)]
    return []


def test_the_terakidz_links_validate():
    assert errors(links()) == []


def test_the_links_name_the_app_and_a_founder_confirmed_or_placeholder_host():
    payload = links()
    assert payload["app"] == "terakidz"
    assert payload["base_status"] in {"placeholder", "confirmed"}
    if payload["base_status"] == "placeholder":
        assert payload["base"].endswith(".test")
        assert payload["base_note"].startswith("FOUNDER: confirm")


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("plain http", lambda p: p.update(base="http://app.terakidz.test")),
        ("a path on the base", lambda p: p.update(base="https://app.terakidz.test/a")),
        ("a trailing slash", lambda p: p.update(base="https://app.terakidz.test/")),
        ("a query on the base", lambda p: p.update(base="https://app.terakidz.test?x=1")),
        ("a placeholder on a live-looking host", lambda p: p.update(base="https://app.terakidz.id")),
        ("a confirmed .test host", lambda p: p.update(base_status="confirmed")),
        ("a placeholder without the founder note", lambda p: p.pop("base_note")),
        ("an unknown field", lambda p: p.update(activation_code="X1")),
        ("a duplicate activity", lambda p: p["activities"].append(p["activities"][0])),
        ("an upper-case id", lambda p: p["activities"].append("Hewan-Learn")),
        ("a path in an id", lambda p: p["activities"].append("../parent")),
        ("no activities", lambda p: p.update(activities=[])),
        ("an unknown app slug shape", lambda p: p.update(app="Terakidz App")),
    ],
)
def test_the_schema_refuses(label, mutate):
    payload = copy.deepcopy(links())
    mutate(payload)
    assert errors(payload), f"schema accepted {label}"


def test_a_confirmed_live_host_is_accepted():
    payload = copy.deepcopy(links())
    payload.update(base="https://app.example.id", base_status="confirmed")
    payload.pop("base_note", None)
    assert errors(payload) == []


# --- 2. catalog ------------------------------------------------------------------


def test_the_catalog_snapshot_is_the_spa_generated_shape():
    snapshot = catalog()
    assert set(snapshot) == {"generated_by", "activities"}
    assert "activity-link" in snapshot["generated_by"]
    ids = list(snapshot["activities"])
    assert ids == sorted(ids) and len(ids) > 100
    assert set(snapshot["activities"].values()) <= {"free", "premium"}
    assert CATALOG_PATH.read_text().endswith("\n")


def test_every_link_opens_an_activity_in_the_catalog():
    missing = [activity for activity in links()["activities"] if activity not in catalog()["activities"]]
    assert missing == []


def test_every_link_opens_a_free_activity():
    # TB-D4 (a): the box is the product; a printed QR must never land on the paywall.
    tiers = catalog()["activities"]
    assert {activity: tiers[activity] for activity in links()["activities"] if tiers[activity] != "free"} == {}


def test_the_catalog_check_is_not_vacuous():
    tiers = catalog()["activities"]
    assert "no-such-activity" not in tiers
    assert "premium" in set(tiers.values())


# --- 3. own-work assets ------------------------------------------------------------


def test_the_generator_is_committed_beside_its_tests():
    assert GENERATOR.is_file()
    assert (REPO / "ops" / "app_links" / "tests").is_dir()


def test_the_manifest_is_own_work_evidence_for_every_link():
    payload, evidence = links(), manifest()
    assert evidence["licence"] == "own-work"
    assert evidence["licence_type"] == "owned" and evidence["commercial_use"] is True
    assert evidence["author_note"].strip()
    assert evidence["generator"] == "ops/app_links/gen_app_qr.py"
    assert evidence["source"] == "contracts/app_links/terakidz.v1.json"
    assert [asset["activity"] for asset in evidence["assets"]] == payload["activities"]
    for asset in evidence["assets"]:
        assert asset["key"] == f"image-qr-{payload['app']}-{asset['activity']}"
        assert asset["kind"] == "image"
        assert asset["file"] == f"qr-{asset['activity']}.png"
        assert asset["link"] == link_for(payload, asset["activity"])


def test_every_png_is_its_manifest_entry_and_nothing_else_is_shipped():
    evidence = manifest()
    on_disk = {path.name for path in ASSETS_DIR.iterdir()}
    assert on_disk == {"own-work.json", *(asset["file"] for asset in evidence["assets"])}
    for asset in evidence["assets"]:
        data = (ASSETS_DIR / asset["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == asset["sha256"], asset["file"]


def test_every_png_is_a_square_one_bit_greyscale_image():
    for asset in manifest()["assets"]:
        header = png_header((ASSETS_DIR / asset["file"]).read_bytes())
        assert header["width"] == header["height"] >= 200
        assert (header["depth"], header["colour"], header["interlace"]) == (1, 0, 0)


def test_the_asset_keys_use_a_prefix_the_asset_library_knows():
    # The product-line harness registers `asset:<key>` by its prefix (font-, image-, footage-).
    for asset in manifest()["assets"]:
        assert re.fullmatch(r"image-[a-z0-9-]+", asset["key"]), asset["key"]


# --- 4. the box insert -------------------------------------------------------------


def test_the_insert_validates_and_resolves():
    validator_for(TEMPLATES_SCHEMA).validate(insert())
    assert template_resolve_problems(insert(), insert_layout()) == []


def test_the_insert_is_a_first_version_pdf_for_the_terakidz_kit_only():
    payload = insert()
    assert payload["id"] == INSERT_ID and payload["version"] == 1
    assert payload["kind"] == "pdf" and payload["renderer"] == "wkhtml" and payload["outputs"] == ["pdf"]
    assert payload["brand"]["kits"] == ["terakidz"]


def test_the_insert_places_exactly_the_qr_assets_in_link_order():
    payload = insert()
    placed = [block["asset"] for block in payload["blocks"] if block["type"] == "image"]
    expected = [f"asset:{asset['key']}" for asset in manifest()["assets"]]
    assert placed == expected
    assert sorted(payload["assets"]["images"].values()) == sorted(expected)
    assert all(block.get("alt") for block in payload["blocks"] if block["type"] == "image")


def test_the_insert_uses_only_the_kit_fonts():
    payload = insert()
    kit_fonts = set(yaml.safe_load((REPO / "brands" / "terakidz.yaml").read_text())["fonts"].values())
    assert set(payload["assets"]["fonts"].values()) <= kit_fonts


def test_the_insert_layout_is_a_skeleton():
    text = insert_layout()
    assert text.count("{{blocks}}") == 1
    lowered = text.lower()
    assert not [marker for marker in FORBIDDEN_LAYOUT_MARKUP if marker in lowered]
    for needle in ("<img", "src=", "href=", "@import", "url("):
        assert needle not in lowered, needle
    assert not var_in_style_or_script(text)
    assert "EXAMPLE CONTENT" in text


def test_the_insert_header_marks_example_content():
    assert "EXAMPLE CONTENT — not approved product copy" in (INSERT_DIR / "template.yaml").read_text()


def test_the_insert_honours_the_kit_voice_and_carries_no_pii_or_url():
    texts = template_texts(insert(), insert_layout())
    assert voice_problems(texts) == []
    for text in texts[:-1]:
        for pattern in (EMAIL_RE, PHONE_RE, NIK_RE, URL_RE):
            assert not pattern.search(text), text


def test_the_insert_places_the_kit_disclaimers():
    disclaimers = [block for block in insert()["blocks"] if block["type"] == "disclaimer"]
    assert disclaimers and all(block["text"] == "{{kit.disclaimers}}" for block in disclaimers)


def test_the_insert_names_no_activation_code():
    # TB-D4 (a): open links only -- no code, serial or voucher is printed or asked for.
    copy_text = " ".join(template_texts(insert(), "")).lower()
    for word in ("kode aktivasi", "aktivasi", "voucher", "serial", "kode unik"):
        assert word not in copy_text, word


# --- 5. the line -------------------------------------------------------------------


def test_the_line_is_the_box_and_its_insert():
    payload = line()
    assert payload["id"] == "terakidz-box" and payload["brand_kit"] == "terakidz"
    kinds = {(item["kind"], item["ref"]) for item in payload["items"]}
    assert kinds == {("physical", "terakidz-task-box"), ("template", INSERT_ID)}
    assert all(item["publish"] == "none" for item in payload["items"])
    assert payload["launch"] == []


def test_the_box_has_a_price_and_the_insert_ships_inside_it():
    items = {item["kind"]: item for item in line()["items"]}
    assert items["physical"]["price_idr"] > 0
    assert items["template"]["price_idr"] == 0
    assert "digital_kind" not in items["physical"] and "digital_kind" not in items["template"]


def test_the_box_ref_is_an_order_intake_product_ref_when_upper_cased():
    # order.requested.v1 `lines[].product_ref`: ^[A-Z0-9-]{3,32}$ -- the harness sells the box by
    # that default code.
    schema = json.loads((CONTRACTS / "events" / "order.requested.v1.schema.json").read_text())
    pattern = schema["properties"]["payload"]["properties"]["lines"]["items"]["properties"]["product_ref"]["pattern"]
    ref = {item["kind"]: item for item in line()["items"]}["physical"]["ref"]
    assert re.fullmatch(pattern, ref.upper())


def test_the_line_header_records_the_open_link_decision():
    text = LINE_PATH.read_text()
    assert "TB-D4" in text and "EXAMPLE CONTENT" in text
