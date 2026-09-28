"""The QR generator for app activity links (Track B TB2, spec section 4 "B2", TB-D4 (a)).

Run with the generator's pinned dependencies (the dokploy project does not carry them):

    uv run --with qrcode==8.2 --with pillow --with zxing-cpp pytest ops/app_links/tests -q

Three layers, each skipped BY NAME when its dependency is missing, never silently passed:

1. pure (stdlib only): the PNG writer, the stored-deflate stream, the naming and the manifest;
2. `qrcode`: the committed PNGs and `own-work.json` are byte-identical to a fresh regeneration,
   and `--check` catches a drifted file;
3. `zxingcpp` + `PIL`: every committed PNG decodes to exactly its link.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LINKS = REPO / "contracts" / "app_links" / "terakidz.v1.json"
ASSETS = REPO / "templates" / "terakidz-box-insert" / "assets"

_spec = importlib.util.spec_from_file_location("gen_app_qr", HERE.parent / "gen_app_qr.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_app_qr"] = gen
_spec.loader.exec_module(gen)


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == gen.PNG_SIGNATURE
    out, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert zlib.crc32(kind + body) == crc, kind
        out.append((kind, body))
        pos += 12 + length
    return out


def _pixels(data: bytes) -> list[list[int]]:
    """Decode a 1-bit greyscale, filter-0 PNG back to rows of 0/1 (1 = white)."""
    chunks = _chunks(data)
    width, height = struct.unpack(">II", chunks[0][1][:8])
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    stride = (width + 7) // 8
    rows = []
    for y in range(height):
        line = raw[y * (stride + 1) : (y + 1) * (stride + 1)]
        assert line[0] == 0, "filter type must be None"
        bits = [(line[1 + x // 8] >> (7 - x % 8)) & 1 for x in range(width)]
        rows.append(bits)
    return rows


# --- 1. pure ---------------------------------------------------------------------


def test_stored_deflate_round_trips_and_spans_blocks():
    payload = bytes(range(256)) * 700  # > 65535: several stored blocks
    stream = gen.stored_zlib(payload)
    assert stream[:2] == b"\x78\x01"
    assert zlib.decompress(stream) == payload
    assert gen.stored_zlib(payload) == stream


def test_the_png_writer_draws_the_matrix_at_scale_black_on_white():
    matrix = [[True, False], [False, True]]
    data = gen.png_bytes(matrix, scale=3)
    kinds = [kind for kind, _ in _chunks(data)]
    assert kinds == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, colour, _c, _f, interlace = struct.unpack(">IIBBBBB", _chunks(data)[0][1])
    assert (width, height, depth, colour, interlace) == (6, 6, 1, 0, 0)
    rows = _pixels(data)
    assert rows[0] == [0, 0, 0, 1, 1, 1]  # dark module = black (0)
    assert rows[5] == [1, 1, 1, 0, 0, 0]
    assert gen.png_bytes(matrix, scale=3) == data


def test_the_png_writer_refuses_a_ragged_or_empty_matrix():
    with pytest.raises(ValueError, match="square"):
        gen.png_bytes([[True, False], [True]], scale=2)
    with pytest.raises(ValueError, match="square"):
        gen.png_bytes([], scale=2)


def test_names_and_links_follow_the_contract():
    payload = {"app": "terakidz", "base": "https://app.terakidz.test", "activities": ["jam"]}
    assert gen.link_for(payload, "jam") == "https://app.terakidz.test/a/jam"
    assert gen.asset_key(payload, "jam") == "image-qr-terakidz-jam"
    assert gen.file_name("jam") == "qr-jam.png"


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("http base", {"app": "a1", "base": "http://x.test", "base_status": "placeholder", "activities": ["x"]}),
        (
            "a path on the base",
            {"app": "a1", "base": "https://x.test/p", "base_status": "placeholder", "activities": ["x"]},
        ),
        (
            "duplicate ids",
            {"app": "a1", "base": "https://x.test", "base_status": "placeholder", "activities": ["x", "x"]},
        ),
        ("a bad id", {"app": "a1", "base": "https://x.test", "base_status": "placeholder", "activities": ["../x"]}),
        (
            "a placeholder on a live host",
            {"app": "a1", "base": "https://x.id", "base_status": "placeholder", "activities": ["x"]},
        ),
        ("no activities", {"app": "a1", "base": "https://x.test", "base_status": "placeholder", "activities": []}),
    ],
)
def test_the_generator_refuses_links_the_contract_refuses(label, payload):
    with pytest.raises(gen.LinksError):
        gen.check_links(payload)


def test_the_manifest_is_deterministic_json_with_every_asset():
    payload = json.loads(LINKS.read_text())
    files = {gen.file_name(activity): f"png-{activity}".encode() for activity in payload["activities"]}
    text = gen.manifest_text(payload, "contracts/app_links/terakidz.v1.json", files)
    assert text == gen.manifest_text(payload, "contracts/app_links/terakidz.v1.json", files)
    assert text.endswith("\n")
    manifest = json.loads(text)
    assert manifest["licence"] == "own-work" and manifest["licence_type"] == "owned"
    assert [asset["activity"] for asset in manifest["assets"]] == payload["activities"]
    first = manifest["assets"][0]
    assert first["sha256"] == hashlib.sha256(files[first["file"]]).hexdigest()
    assert first["link"] == gen.link_for(payload, first["activity"])


# --- 2. regeneration (qrcode) -------------------------------------------------------


def _qrcode():
    return pytest.importorskip("qrcode", reason="qrcode is not installed: run with `uv run --with qrcode==8.2`")


def test_the_generator_pins_the_qrcode_version_it_runs_with():
    qrcode = _qrcode()
    from importlib.metadata import version

    assert version("qrcode") == gen.QRCODE_VERSION, "regenerate only with the pinned qrcode"
    assert qrcode is not None


def test_regenerating_gives_the_committed_bytes_exactly():
    _qrcode()
    payload = json.loads(LINKS.read_text())
    built = gen.build(payload, "contracts/app_links/terakidz.v1.json")
    assert built == gen.build(payload, "contracts/app_links/terakidz.v1.json")
    on_disk = {path.name: path.read_bytes() for path in ASSETS.iterdir()}
    assert set(built) == set(on_disk)
    for name, data in built.items():
        assert data == on_disk[name], f"{name} drifted from a fresh regeneration"


def test_check_mode_passes_on_the_committed_tree_and_names_a_drifted_file(tmp_path, capsys):
    _qrcode()
    assert gen.main(["--links", str(LINKS), "--out", str(ASSETS), "--check"]) == 0
    copy = tmp_path / "assets"
    shutil.copytree(ASSETS, copy)
    victim = copy / gen.file_name(json.loads(LINKS.read_text())["activities"][0])
    victim.write_bytes(victim.read_bytes()[:-1] + b"\x00")
    assert gen.main(["--links", str(LINKS), "--out", str(copy), "--check"]) == 1
    assert victim.name in capsys.readouterr().err


def test_check_mode_names_a_stray_file(tmp_path, capsys):
    _qrcode()
    copy = tmp_path / "assets"
    shutil.copytree(ASSETS, copy)
    (copy / "qr-stray.png").write_bytes(b"x")
    assert gen.main(["--links", str(LINKS), "--out", str(copy), "--check"]) == 1
    assert "qr-stray.png" in capsys.readouterr().err


def test_write_mode_produces_the_committed_tree(tmp_path):
    _qrcode()
    out = tmp_path / "assets"
    assert gen.main(["--links", str(LINKS), "--out", str(out)]) == 0
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {p.name: p.read_bytes() for p in ASSETS.iterdir()}


# --- 3. decode (zxing-cpp) -----------------------------------------------------------


def test_every_committed_png_decodes_to_exactly_its_link():
    zxingcpp = pytest.importorskip("zxingcpp", reason="zxing-cpp is not installed: run with `uv run --with zxing-cpp`")
    image = pytest.importorskip("PIL.Image", reason="Pillow is not installed: run with `uv run --with pillow`")
    manifest = json.loads((ASSETS / "own-work.json").read_text())
    for asset in manifest["assets"]:
        with image.open(ASSETS / asset["file"]) as picture:
            found = [result.text for result in zxingcpp.read_barcodes(picture.convert("L"))]
        assert found == [asset["link"]], asset["file"]
