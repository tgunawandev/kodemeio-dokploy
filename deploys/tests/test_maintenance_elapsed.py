"""The maintenance page's "N menit berjalan" counter.

Seen on prod 2026-09-16: erp-stg.idtpp.com rendered `427 menit berjalan` while
the Odoo app was answering 303 from Werkzeug and Dokploy reported the service
`done`. The number was not an outage, it was the age of the browser tab.

`sessionStorage['kodemeio-maint-since']` is written the first time a tab ever
renders this page and cleared on exactly ONE of the ways the page can be left:
the in-page HEAD probe. Recovery via the 90s `<meta refresh>`, a manual reload,
or a throttled background tab all leave it behind, and nothing bounds its age,
so the NEXT unrelated 5xx blip in that tab renders the age of the PREVIOUS one.

These tests drive the real shipped script under a stub DOM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "maintenance" / "docker-compose.yml"

MINUTE = 60_000
HOUR = 60 * MINUTE

# A fixed "now" so the assertions read as wall-clock arithmetic rather than
# as whatever the test host's clock happened to be.
NOW = 1_800_000_000_000


def _page_script() -> str:
    """The <script> body out of the maintenance_html config, verbatim."""
    doc = yaml.safe_load(COMPOSE.read_text())
    html = doc["configs"]["maintenance_html"]["content"]
    start = html.index("<script>") + len("<script>")
    return html[start : html.index("</script>", start)]


HARNESS = """
// Minimal DOM: every element is a bag of the properties the page writes.
function elem() {
  var e = {
    innerHTML: '', hidden: false, href: '',
    style: {}, classList: { _s: new Set(), add(c) { this._s.add(c); },
                            has(c) { return this._s.has(c); } },
    appendChild() {}, charAt() { return ''; },
  };
  // A real DOM coerces whatever you assign to a string; the stub must too, or
  // the test disagrees with the browser about `el.count.textContent = secs`.
  var text = '';
  Object.defineProperty(e, 'textContent', {
    get() { return text; },
    set(v) { text = String(v); },
    enumerable: true,
  });
  return e;
}
var NODES = {};
var document = {
  getElementById(id) { return (NODES[id] = NODES[id] || elem()); },
  createElement() { return elem(); },
  cookie: '',
};

var STORE = __STORE__;
var sessionStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(STORE, k) ? STORE[k] : null; },
  setItem(k, v) { STORE[k] = String(v); },
  removeItem(k) { delete STORE[k]; },
};

var window = { location: {
  hostname: 'erp-stg.idtpp.com',
  href: 'https://erp-stg.idtpp.com/odoo/customer-invoices/692804',
} };

// Freeze the clock. The page must never read anything but Date.now().
Date.now = function () { return __NOW__; };

// No timers: we assert on the synchronous first render, not on the poll loop.
function setTimeout() {}
// The state fetch is irrelevant to elapsed; let it reject so .catch(render) runs.
function fetch() { return Promise.reject(new Error('no state endpoint in test')); }

__SCRIPT__

console.log(JSON.stringify({
  count: NODES.count.textContent,
  unit: NODES.unit.textContent,
  note: NODES.note.textContent || NODES.note.innerHTML,
  over: NODES.bar.classList.has('over'),
  store: STORE,
}));
"""


def run_page(store: dict[str, str], now: int = NOW) -> dict:
    """Load the maintenance page with `store` as the tab's sessionStorage."""
    if shutil.which("node") is None:  # pragma: no cover
        pytest.skip("node is required to exercise the maintenance page script")
    js = (
        HARNESS.replace("__STORE__", json.dumps(store))
        .replace("__NOW__", str(now))
        .replace("__SCRIPT__", _page_script())
    )
    out = subprocess.run(
        ["node", "--input-type=commonjs", "-e", js],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert out.returncode == 0, f"page script threw:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


SINCE = "kodemeio-maint-since"
SEEN = "kodemeio-maint-seen"


def test_first_view_of_an_outage_starts_at_zero():
    r = run_page({})
    assert r["count"] == "0"
    assert r["unit"] == "detik berjalan"


def test_a_continuing_outage_keeps_counting():
    """The page reloads itself every 90s; the count must survive that."""
    r = run_page({SINCE: str(NOW - 3 * MINUTE), SEEN: str(NOW - 30_000)})
    assert r["count"] == "3"
    assert r["unit"] == "menit berjalan"


def test_a_stale_timestamp_from_an_earlier_outage_is_not_reused():
    """The 427-minute bug.

    A tab that saw a blip this morning, recovered by <meta refresh> without ever
    running the HEAD probe's cleanup, then hit a fresh blip this afternoon. The
    gap in the heartbeat is the evidence that these are two different outages.
    """
    r = run_page({SINCE: str(NOW - 7 * HOUR), SEEN: str(NOW - 7 * HOUR)})
    assert r["count"] == "0", "counter reported a previous outage's age"
    assert r["unit"] == "detik berjalan"


def test_a_timestamp_from_the_future_is_rejected():
    """An NTP step or a corrected system clock must not render a negative age."""
    r = run_page({SINCE: str(NOW + HOUR), SEEN: str(NOW + HOUR)})
    assert not r["count"].startswith("-"), f"rendered a negative age: {r['count']!r}"
    assert r["count"] == "0"


def test_the_heartbeat_is_refreshed_on_every_view():
    """Without this write, a continuing outage would look like a gap next load."""
    r = run_page({SINCE: str(NOW - 2 * MINUTE), SEEN: str(NOW - 30_000)})
    assert r["store"][SEEN] == str(NOW)
    assert r["store"][SINCE] == str(NOW - 2 * MINUTE)
