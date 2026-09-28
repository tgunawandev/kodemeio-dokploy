from __future__ import annotations

import copy
import http.client
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import redirect  # noqa: E402
from affiliate import InputError, attribution_digest, evaluate  # noqa: E402
from redirect import JsonlClickSink, RedirectConfig, Request, ShortLinkHandler  # noqa: E402

SAMPLE = ROOT / "examples" / "affiliate.synthetic.v1.json"
NOW = datetime(2026, 9, 28, 9, 30, 15, 123456, tzinfo=UTC)
SALT = b"s" * 32
RAW_IP = "203.0.113.77"
RAW_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1"


def document() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class ListSink:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def __call__(self, record: dict) -> None:
        self.records.append(record)


def make(doc: dict | None = None, *, now: datetime = NOW, sink=None, ids=None) -> tuple[ShortLinkHandler, ListSink]:
    sink = sink if sink is not None else ListSink()
    counter = iter(ids or [f"click-{n:04d}" for n in range(1000)])
    handler = ShortLinkHandler(
        doc if doc is not None else document(),
        RedirectConfig(salt=SALT),
        sink=sink,
        clock=lambda: now,
        id_factory=lambda: next(counter),
    )
    return handler, sink


def get(path: str = "/a/synthetic-link-001", *, ip: str | None = RAW_IP, ua: str = RAW_UA, method: str = "GET"):
    return Request(method=method, path=path, headers={"User-Agent": ua}, client_ip=ip)


def test_disclosed_approved_link_redirects_and_logs_a_minimised_click() -> None:
    handler, sink = make()
    response = handler(get())
    assert response.status == 302
    assert response.headers["Location"] == "https://shop.example.test/item/1"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.reason == "redirected"
    assert len(sink.records) == 1
    click = sink.records[0]
    assert click["event_type"] == "click"
    assert click["event_id"] == "click-0000"
    assert click["link_id"] == "synthetic-link-001"
    assert click["occurred_at"] == "2026-09-28T09:30:15Z"
    assert click["ua_class"] == "mobile"
    assert len(click["visitor_hash"]) == 32
    assert click["attribution_sha256"] == attribution_digest(click["attribution"])


def test_click_record_never_contains_raw_ip_user_agent_or_network_prefix() -> None:
    handler, sink = make()
    handler(get())
    serialized = json.dumps(sink.records)
    assert RAW_IP not in serialized
    assert "203.0.113" not in serialized
    assert "iPhone" not in serialized and "Mozilla" not in serialized
    assert set(sink.records[0]) == {
        "event_id",
        "event_type",
        "synthetic",
        "occurred_at",
        "link_id",
        "attribution",
        "attribution_sha256",
        "visitor_hash",
        "ua_class",
    }


def test_visitor_hash_truncates_the_network_and_rotates_daily() -> None:
    handler, sink = make()
    handler(get(ip="203.0.113.77"))
    handler(get(ip="203.0.113.200"))  # same /24
    handler(get(ip="198.51.100.7"))
    assert sink.records[0]["visitor_hash"] == sink.records[1]["visitor_hash"]
    assert sink.records[0]["visitor_hash"] != sink.records[2]["visitor_hash"]
    next_day, next_sink = make(now=datetime(2026, 9, 29, 9, 0, tzinfo=UTC))
    next_day(get(ip="203.0.113.77"))
    assert next_sink.records[0]["visitor_hash"] != sink.records[0]["visitor_hash"]
    v6, v6_sink = make()
    v6(get(ip="2001:db8:abcd:0012::1"))
    v6(get(ip="2001:db8:abcd:ffff::2"))  # same /48
    assert v6_sink.records[0]["visitor_hash"] == v6_sink.records[1]["visitor_hash"]


def test_logged_click_is_a_valid_event_for_the_affiliate_contract() -> None:
    doc = document()
    handler, sink = make(doc)
    handler(get())
    doc["events"] = [*sink.records]
    report = evaluate(doc)
    assert report["status"] == "candidate-ready-unverified", report["issues"]


def test_click_id_param_carries_only_the_opaque_click_id() -> None:
    doc = document()
    doc["links"][0]["click_id_param"] = "subid"
    handler, sink = make(doc)
    response = handler(get())
    assert response.headers["Location"] == "https://shop.example.test/item/1?subid=click-0000"
    assert sink.records[0]["event_id"] == "click-0000"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda d: d["links"][0].update(disclosure="tbd: pending review"), "undisclosed"),
        (lambda d: d["links"][0].update(disclosure="TODO disclosure"), "undisclosed"),
        (lambda d: d["links"][0].update(status="paused"), "not_active"),
        (lambda d: d["links"][0].update(expires_on="2026-09-27"), "expired"),
        (lambda d: d["advertiser"]["approval"].update(status="pending"), "advertiser_unapproved"),
        (lambda d: d["advertiser"]["approval"].update(valid_until="2026-09-27"), "advertiser_unapproved"),
        (lambda d: d["advertiser"]["commission_terms"].update(status="pending"), "terms_unapproved"),
        (lambda d: d.update(destination_allowlist=["other.example.test"]), "destination_not_allowlisted"),
        (lambda d: d["links"][0]["attribution"].update(campaign="changed"), "attribution_mismatch"),
    ],
)
def test_undisclosed_or_unapproved_links_are_refused_without_logging(mutation, reason) -> None:
    doc = document()
    mutation(doc)
    handler, sink = make(doc)
    response = handler(get())
    assert response.status == 404
    assert "Location" not in response.headers
    assert response.reason == reason
    assert sink.records == []


def test_refusals_do_not_echo_the_requested_path() -> None:
    handler, sink = make()
    for path in ("/a/unknown-link", "/a/../../etc", "/a/synthetic-link-001/extra", "/", "/a/UPPER"):
        response = handler(get(path))
        assert response.status == 404
        assert path.encode() not in response.body
    assert sink.records == []


def test_query_string_is_ignored_and_never_forwarded() -> None:
    handler, sink = make()
    response = handler(get("/a/synthetic-link-001?email=someone@example.test"))
    assert response.status == 302
    assert response.headers["Location"] == "https://shop.example.test/item/1"
    assert "someone" not in json.dumps(sink.records)


def test_head_redirects_without_logging_and_other_methods_are_refused() -> None:
    handler, sink = make()
    assert handler(get(method="HEAD")).status == 302
    assert sink.records == []
    response = handler(get(method="POST"))
    assert response.status == 405
    assert response.headers["Allow"] == "GET, HEAD"


@pytest.mark.parametrize(
    "ua,expected",
    [
        ("Googlebot/2.1 (+http://www.google.com/bot.html)", "bot"),
        ("facebookexternalhit/1.1", "bot"),
        ("curl/8.5.0", "bot"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0", "desktop"),
        ("", "unknown"),
    ],
)
def test_user_agent_is_reduced_to_a_coarse_class(ua: str, expected: str) -> None:
    handler, sink = make()
    handler(get(ua=ua))
    assert sink.records[0]["ua_class"] == expected


def test_missing_or_invalid_client_ip_still_logs_without_failing() -> None:
    handler, sink = make()
    assert handler(get(ip=None)).status == 302
    assert handler(get(ip="not-an-ip")).status == 302
    assert len(sink.records) == 2


def test_sink_failure_still_redirects_but_without_an_unlogged_click_id() -> None:
    doc = document()
    doc["links"][0]["click_id_param"] = "subid"

    def broken(record: dict) -> None:
        raise OSError("disk full")

    handler, _ = make(doc, sink=broken)
    response = handler(get())
    assert response.status == 302
    assert response.headers["Location"] == "https://shop.example.test/item/1"
    assert response.reason == "click_log_failed"


def test_invalid_document_or_short_salt_refuses_to_build_a_handler() -> None:
    doc = document()
    doc["links"][0]["destination_url"] = 42
    with pytest.raises(InputError, match="^registry_document_invalid$"):
        make(doc)
    with pytest.raises(InputError, match="^redirect_salt_too_short$"):
        RedirectConfig(salt=b"short")


def test_jsonl_sink_appends_private_records(tmp_path: Path) -> None:
    path = tmp_path / "clicks.jsonl"
    sink = JsonlClickSink(path)
    handler, _ = make(sink=sink)
    handler(get())
    handler(get())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in lines] == ["click-0000", "click-0001"]
    assert path.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)
    with pytest.raises(OSError):
        JsonlClickSink(link)({"event_id": "x"})


def test_local_http_shim_serves_the_same_handler_on_loopback_only(tmp_path: Path) -> None:
    handler, sink = make(copy.deepcopy(document()))
    with pytest.raises(InputError, match="^shim_loopback_only$"):
        redirect.make_server(handler, host="0.0.0.0", port=0)
    server = redirect.make_server(handler, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/a/synthetic-link-001", headers={"User-Agent": RAW_UA})
        response = connection.getresponse()
        assert response.status == 302
        assert response.getheader("Location") == "https://shop.example.test/item/1"
        response.read()
        connection.request("GET", "/a/missing")
        missing = connection.getresponse()
        missing.read()
        assert missing.status == 404
    finally:
        server.shutdown()
        server.server_close()
    assert len(sink.records) == 1
    assert "127.0.0.1" not in json.dumps(sink.records)


@pytest.mark.parametrize(
    "path", ["//evil.example/a/synthetic-link-001", "http://evil.example/a/synthetic-link-001", "a/synthetic-link-001"]
)
def test_absolute_form_or_authority_paths_are_refused(path: str) -> None:
    handler, sink = make()
    assert handler(get(path)).status == 404
    assert sink.records == []


def test_jsonl_sink_refuses_non_regular_or_shared_files(tmp_path: Path) -> None:
    import os

    shared = tmp_path / "shared.jsonl"
    shared.write_text("", encoding="utf-8")
    shared.chmod(0o644)
    with pytest.raises(OSError):
        JsonlClickSink(shared)({"event_id": "x"})
    fifo = tmp_path / "fifo.jsonl"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        JsonlClickSink(fifo)({"event_id": "x"})
