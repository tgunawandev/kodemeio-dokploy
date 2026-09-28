#!/usr/bin/env python3
"""Affiliate short-link redirect handler (portable reference for a Cloudflare Worker).

The handler is a pure request -> response function over an ``affiliate.v1`` document. It resolves
``/a/<link_id>`` only for a link that is active, unexpired, disclosed, allowlisted, attribution-
consistent and backed by an approved advertiser and approved commission terms *at request time*.
Every other request gets a generic 404 that never echoes the requested path.

An accepted GET logs one minimised click record through an injected sink. The record matches the
``click_event`` shape of ``affiliate.v1`` and never contains a raw IP address, network prefix,
user agent, query string or referrer: the visitor is reduced to a keyed, daily-rotated hash of a
truncated network prefix (/24 IPv4, /48 IPv6) plus a coarse agent class.

The local HTTP shim (``serve``) binds loopback only and exists for manual testing. No Cloudflare,
advertiser or network call is made anywhere in this module.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from affiliate import (  # noqa: E402
    InputError,
    _date,
    _destination_is_allowed,
    _load_schema,
    _placeholder,
    attribution_digest,
    load_document,
)
from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

PATH_PATTERN = re.compile(r"^/a/([a-z][a-z0-9_-]{0,63})$")
MIN_SALT_BYTES = 32
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
BOT_MARKERS = (
    "bot",
    "crawl",
    "spider",
    "slurp",
    "preview",
    "facebookexternalhit",
    "curl",
    "wget",
    "python-",
    "httpclient",
    "headless",
)
MOBILE_MARKERS = ("mobile", "iphone", "ipad", "android")
NOT_FOUND_BODY = b"Not Found\n"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    client_ip: str | None = None


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""
    reason: str = ""  # internal diagnostic code; never sent to the client


@dataclass(frozen=True)
class RedirectConfig:
    salt: bytes = field(repr=False)
    synthetic: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.salt, bytes) or len(self.salt) < MIN_SALT_BYTES:
            raise InputError("redirect_salt_too_short")
        if self.synthetic is not True:
            # affiliate.v1 accepts synthetic events only; live collection needs a reviewed v2 contract.
            raise InputError("live_click_collection_not_enabled")


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value if isinstance(value, str) else ""
    return ""


def ua_class(user_agent: str) -> str:
    agent = user_agent.strip().lower()[:512]
    if not agent:
        return "unknown"
    if any(marker in agent for marker in BOT_MARKERS):
        return "bot"
    if any(marker in agent for marker in MOBILE_MARKERS):
        return "mobile"
    return "desktop"


def network_prefix(client_ip: str | None) -> str:
    """Truncate an address to its /24 (IPv4) or /48 (IPv6) network; never return the raw address."""
    if not isinstance(client_ip, str):
        return "none"
    try:
        address = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return "none"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def visitor_hash(salt: bytes, day: date, client_ip: str | None, agent_class: str) -> str:
    message = f"{day.isoformat()}|{network_prefix(client_ip)}|{agent_class}".encode("ascii")
    return hmac.new(salt, message, hashlib.sha256).hexdigest()[:32]


def _default_id() -> str:
    return f"click-{secrets.token_hex(16)}"


class ShortLinkHandler:
    """Resolve disclosed, approved short links and log minimised clicks."""

    def __init__(
        self,
        document: Any,
        config: RedirectConfig,
        *,
        sink: Callable[[dict[str, Any]], None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = _default_id,
    ) -> None:
        validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
        if type(document) is not dict or any(True for _ in validator.iter_errors(document)):
            raise InputError("registry_document_invalid")
        self._document = document
        self._links = {link["link_id"]: link for link in document["links"]}
        if len(self._links) != len(document["links"]):
            raise InputError("registry_document_invalid")
        self._config = config
        self._sink = sink
        self._clock = clock
        self._id_factory = id_factory

    def _refusal(self, link: dict[str, Any], today: date) -> str | None:
        advertiser = self._document["advertiser"]
        approval = advertiser["approval"]
        terms = advertiser["commission_terms"]
        if approval["status"] != "approved" or _placeholder(approval["approved_by_role"]):
            return "advertiser_unapproved"
        start, end = _date(approval["approved_on"]), _date(approval["valid_until"])
        if not start or not end or not start <= today <= end:
            return "advertiser_unapproved"
        start, end = _date(terms["valid_from"]), _date(terms["valid_until"])
        if terms["status"] != "approved" or not start or not end or not start <= today <= end:
            return "terms_unapproved"
        if _placeholder(link["disclosure"]):
            return "undisclosed"
        if link["status"] != "active":
            return "not_active"
        expiry = _date(link["expires_on"])
        if not expiry or expiry < today:
            return "expired"
        if not _destination_is_allowed(link["destination_url"], self._document["destination_allowlist"]):
            return "destination_not_allowlisted"
        if attribution_digest(link["attribution"]) != link["attribution_sha256"]:
            return "attribution_mismatch"
        return None

    @staticmethod
    def _not_found(reason: str) -> Response:
        return Response(
            404,
            {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
            NOT_FOUND_BODY,
            reason,
        )

    def __call__(self, request: Request) -> Response:
        method = request.method.upper() if isinstance(request.method, str) else ""
        if method not in {"GET", "HEAD"}:
            return Response(405, {"Allow": "GET, HEAD", "Cache-Control": "no-store"}, b"", "method_not_allowed")
        raw_path = request.path if isinstance(request.path, str) else ""
        match = PATH_PATTERN.fullmatch(urlsplit(raw_path).path) if len(raw_path) <= 2048 else None
        if not match:
            return self._not_found("unknown")
        link = self._links.get(match.group(1))
        if link is None:
            return self._not_found("unknown")
        now = self._clock().astimezone(UTC).replace(microsecond=0)
        refusal = self._refusal(link, now.date())
        if refusal:
            return self._not_found(refusal)

        location = link["destination_url"]
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        }
        if method == "HEAD":
            return Response(302, {**headers, "Location": location}, b"", "redirected_unlogged_head")

        agent = ua_class(_header(request.headers, "User-Agent"))
        record = {
            "event_id": self._id_factory(),
            "event_type": "click",
            "synthetic": self._config.synthetic,
            "occurred_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "link_id": link["link_id"],
            "attribution": dict(link["attribution"]),
            "attribution_sha256": link["attribution_sha256"],
            "visitor_hash": visitor_hash(self._config.salt, now.date(), request.client_ip, agent),
            "ua_class": agent,
        }
        try:
            self._sink(record)
        except Exception:  # noqa: BLE001 - availability: the visitor still reaches the destination
            return Response(302, {**headers, "Location": location}, b"", "click_log_failed")
        if link.get("click_id_param"):
            location = f"{location}?{urlencode({link['click_id_param']: record['event_id']})}"
        return Response(302, {**headers, "Location": location}, b"", "redirected")


class JsonlClickSink:
    """Append one JSON line per click to a private (0600) regular file; refuses symlinks."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def __call__(self, record: dict[str, Any]) -> None:
        line = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self._path, flags, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)


def make_server(handler: ShortLinkHandler, *, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Local loopback-only HTTP shim around the handler (the Worker's fetch() equivalent)."""
    if host not in LOOPBACK_HOSTS:
        raise InputError("shim_loopback_only")

    class ShimRequestHandler(BaseHTTPRequestHandler):
        server_version = "affiliate-shim"
        sys_version = ""

        def _dispatch(self) -> None:
            response = handler(Request(self.command, self.path, dict(self.headers.items()), self.client_address[0]))
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # never write client addresses or paths to stderr

    return ThreadingHTTPServer((host, port), ShimRequestHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the loopback-only local shim")
    serve.add_argument("document", type=Path)
    serve.add_argument("--clicks", type=Path, required=True, help="JSONL click log to append to")
    serve.add_argument("--salt-file", type=Path, required=True, help="file holding >= 32 secret bytes")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    try:
        salt = args.salt_file.read_bytes()[:4096]
        handler = ShortLinkHandler(
            load_document(args.document), RedirectConfig(salt=salt), sink=JsonlClickSink(args.clicks)
        )
        server = make_server(handler, host=args.host, port=args.port)
    except (InputError, OSError) as exc:
        print(json.dumps({"status": "blocked", "issues": [str(exc) if isinstance(exc, InputError) else "io_error"]}))
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
