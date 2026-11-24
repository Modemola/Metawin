"""Connector tests against a local fake HTTP server (no real network)."""

from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from radar import ingest
from radar.connectors import ConnectorError, _http, fetch_main, intercom, trustpilot
from radar.connectors.privacy import redact

Response = tuple[int, dict[str, str], Any]


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any


@dataclass
class FakeAPI:
    """Routes every request to `handler(seen) -> (status, headers, json)` and records it."""

    handler: Callable[[Seen], Response]
    requests: list[Seen] = field(default_factory=list)
    url: str = ""


@pytest.fixture
def fake_api(monkeypatch):
    monkeypatch.setattr(_http, "sleep", lambda _s: None)  # retries are instant
    api = FakeAPI(handler=lambda s: (404, {}, {}))

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            seen = Seen(self.command, parsed.path, dict(urllib.parse.parse_qsl(parsed.query)),
                        dict(self.headers), json.loads(raw) if raw else None)
            api.requests.append(seen)
            status, headers, payload = api.handler(seen)
            body = json.dumps(payload).encode()
            self.send_response(status)
            for k, v in {"Content-Type": "application/json", **headers}.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _serve

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    api.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield api
    server.shutdown()
    server.server_close()


# ----------------------------------------------------------------------- Intercom

def _conversation(n: int) -> dict:
    """A full GET /conversations/{id} response, including identity fields that must be dropped."""
    return {
        "type": "conversation", "id": str(n), "created_at": 1793000000 + n,
        "contacts": {"contacts": [{"id": "c1", "email": "player@example.com"}]},
        "source": {"subject": "", "author": {"type": "user", "name": "Jane Player", "email": "jane@example.com"},
                   "body": f"<p>Lossback {n} not credited, mail me at jane.doe+x@example.co.uk or +44 7700 900123</p>"},
        "conversation_parts": {"type": "conversation_part.list", "total_count": 3, "conversation_parts": [
            {"part_type": "comment", "author": {"type": "admin", "name": "Agent Bob"}, "body": "<p>Checking</p>"},
            {"part_type": "assignment", "author": {"type": "bot"}, "body": None},
            {"part_type": "comment", "author": {"type": "user", "name": "Jane Player"},
             "body": "<p>still nothing, call (555) 123-4567</p>"},
        ]},
    }


def _intercom_handler(pages: list[list[int]]) -> Callable[[Seen], Response]:
    def handle(s: Seen) -> Response:
        if s.method == "POST" and s.path == "/conversations/search":
            cursor = (s.body.get("pagination") or {}).get("starting_after")
            idx = int(cursor[4:]) if cursor else 0
            page = {"type": "conversation.list", "conversations": [{"id": str(i)} for i in pages[idx]],
                    "pages": {"type": "pages", "per_page": 150}}
            if idx + 1 < len(pages):
                page["pages"]["next"] = {"page": idx + 2, "starting_after": f"cur-{idx + 1}"}
            return 200, {}, page
        if s.method == "GET" and s.path.startswith("/conversations/"):
            return 200, {}, _conversation(int(s.path.rsplit("/", 1)[1]))
        return 404, {}, {"errors": [{"code": "not_found"}]}
    return handle


def test_intercom_paginates_and_output_parses(fake_api):
    fake_api.handler = _intercom_handler([[1, 2], [3], [4, 5]])
    convs = intercom.fetch_conversations("tok", date(2026, 8, 1), date(2026, 8, 31), base_url=fake_api.url)

    assert [c["id"] for c in convs] == ["1", "2", "3", "4", "5"]
    searches = [r for r in fake_api.requests if r.method == "POST"]
    assert [(r.body["pagination"].get("starting_after")) for r in searches] == [None, "cur-1", "cur-2"]
    assert searches[0].body["pagination"]["per_page"] == 150
    clauses = searches[0].body["query"]["value"]
    assert clauses[0] == {"field": "created_at", "operator": ">", "value": 1785542400 - 1}  # 2026-08-01 UTC
    assert clauses[1] == {"field": "created_at", "operator": "<", "value": 1788220800}  # 2026-09-01 UTC
    h = searches[0].headers
    assert (h["Authorization"], h["Intercom-Version"], h["Accept"]) == ("Bearer tok", "2.11", "application/json")

    parsed = ingest.parse_intercom_json({"conversations": convs})
    assert len(parsed) == 5
    assert "still nothing" in parsed[0].text and "Checking" not in parsed[0].text


def test_intercom_max_conversations_stops_early(fake_api):
    fake_api.handler = _intercom_handler([[1, 2], [3, 4], [5, 6]])
    convs = intercom.fetch_conversations("tok", date(2026, 8, 1), max_conversations=3, base_url=fake_api.url)
    assert [c["id"] for c in convs] == ["1", "2", "3"]
    assert len([r for r in fake_api.requests if r.method == "POST"]) == 2


def test_intercom_strips_identities_and_contact_details(fake_api):
    fake_api.handler = _intercom_handler([[7]])
    (conv,) = intercom.fetch_conversations("tok", date(2026, 8, 1), base_url=fake_api.url)
    dumped = json.dumps(conv)
    for leaked in ("jane", "Jane", "Bob", "example.com", "contacts", "7700", "123-4567"):
        assert leaked not in dumped
    assert "[email]" in conv["source"]["body"] and "[phone]" in conv["source"]["body"]
    parts = conv["conversation_parts"]["conversation_parts"]
    assert [p["author"] for p in parts] == [{"type": "admin"}, {"type": "user"}]  # empty bodies dropped


def test_429_then_success_respects_retry_after(fake_api, monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(_http, "sleep", waits.append)
    calls = {"n": 0}

    def handle(s: Seen) -> Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return 429, {"Retry-After": "3"}, {"errors": [{"code": "rate_limit_exceeded"}]}
        if calls["n"] == 3:
            return 503, {}, {}
        return 200, {}, {"id": "bu-1"}

    reviews_handler = _trustpilot_handler(total=2)

    def chained(s: Seen) -> Response:
        return handle(s) if s.path.endswith("/find") else reviews_handler(s)

    fake_api.handler = chained
    reviews = trustpilot.fetch_reviews("key", domain="metawin.com", base_url=fake_api.url)
    assert len(reviews) == 2
    assert waits == [3.0, 3.0, 4.0]  # Retry-After twice, then backoff 2**2 on the 5xx


def test_rate_limit_reset_header_used_when_no_retry_after():
    from email.message import Message
    m = Message()
    m["X-RateLimit-Reset"] = "1010"
    assert _http._retry_wait(m, 0, now=lambda: 1000.0) == 10.0


def test_gives_up_after_max_retries(fake_api):
    fake_api.handler = lambda s: (500, {}, {"error": "boom"})
    with pytest.raises(ConnectorError, match="after 5 retries"):
        intercom.fetch_conversations("tok", date(2026, 8, 1), base_url=fake_api.url)
    assert len(fake_api.requests) == 6


def test_intercom_401_gives_clear_error(fake_api):
    fake_api.handler = lambda s: (401, {}, {"type": "error.list", "errors": [{"code": "unauthorized"}]})
    with pytest.raises(ConnectorError, match="token rejected: needs read conversations permission"):
        intercom.fetch_conversations("bad", date(2026, 8, 1), base_url=fake_api.url)
    assert len(fake_api.requests) == 1  # auth errors are not retried


# --------------------------------------------------------------------- Trustpilot

def _review(n: int) -> dict:
    return {
        "id": f"r{n}", "stars": 1 + n % 5, "title": f"Title {n}",
        "text": f"Withdrawal {n} stuck. Email me: someone{n}@mail.com",
        "createdAt": "2026-11-01T10:00:00Z", "language": "en",
        "consumer": {"id": "c", "displayName": "Real Name", "numberOfReviews": 3},
        "companyReply": {"text": "Sorry, write to vip@metawin.com", "createdAt": "2026-11-02T10:00:00Z"} if n % 2 else None,
    }


def _trustpilot_handler(total: int) -> Callable[[Seen], Response]:
    def handle(s: Seen) -> Response:
        if s.path == "/business-units/find":
            return 200, {}, {"id": "bu-1", "displayName": "MetaWin"}
        if s.path == "/business-units/bu-1/reviews":
            page, per = int(s.query["page"]), int(s.query["perPage"])
            ids = range((page - 1) * per, min(page * per, total))
            return 200, {}, {"reviews": [_review(i) for i in ids], "links": []}
        return 404, {}, {"message": "Not found"}
    return handle


def test_trustpilot_resolves_domain_and_paginates(fake_api):
    fake_api.handler = _trustpilot_handler(total=250)
    reviews = trustpilot.fetch_reviews("key", domain="metawin.com", base_url=fake_api.url)

    assert len(reviews) == 250 and reviews[0]["id"] == "r0"
    find, *pages = fake_api.requests
    assert find.query == {"apikey": "key", "name": "metawin.com"}
    assert [p.query["page"] for p in pages] == ["1", "2", "3"]
    assert all(p.query["orderBy"] == "createdat.desc" and p.query["perPage"] == "100" for p in pages)

    r1 = reviews[1]
    assert r1["consumer"] == {} and "Real Name" not in json.dumps(reviews)
    assert "@" not in r1["text"] and "[email]" in r1["text"] and "@" not in r1["companyReply"]["text"]
    assert set(r1) == {"id", "stars", "title", "text", "createdAt", "companyReply", "consumer"}
    parsed = ingest.parse_trustpilot_json({"reviews": reviews})
    assert parsed[1].company_replied is True and parsed[0].company_replied is False


def test_trustpilot_max_reviews_and_business_unit_id(fake_api):
    fake_api.handler = _trustpilot_handler(total=500)
    reviews = trustpilot.fetch_reviews("key", business_unit_id="bu-1", max_reviews=150, base_url=fake_api.url)
    assert len(reviews) == 150
    assert [r.query["page"] for r in fake_api.requests] == ["1", "2"]  # no domain lookup, stops at max


def test_trustpilot_403_and_unknown_domain(fake_api):
    fake_api.handler = lambda s: (403, {}, {"message": "Forbidden"})
    with pytest.raises(ConnectorError, match="API key rejected") as exc:
        trustpilot.fetch_reviews("secret-key", domain="metawin.com", base_url=fake_api.url)
    assert "secret-key" not in str(exc.value)  # the key is in the URL; never echo it

    fake_api.handler = lambda s: (404, {}, {"message": "Not found"})
    with pytest.raises(ConnectorError, match="no Trustpilot business profile found for 'nope.example'"):
        trustpilot.fetch_reviews("key", domain="nope.example", base_url=fake_api.url)


# ---------------------------------------------------------------------------- CLI

def test_fetch_main_intercom_round_trip(fake_api, monkeypatch, tmp_path, capsys):
    fake_api.handler = _intercom_handler([[1], [2]])
    monkeypatch.setattr(intercom, "BASE_URL", fake_api.url)
    monkeypatch.setenv("INTERCOM_TOKEN", "tok")
    out = tmp_path / "data" / "intercom.json"

    assert fetch_main(["intercom", "--since", "2026-08-01", "--out", str(out)]) == 0
    items = ingest.load(out)
    assert [c.id for c in items] == ["intercom:1", "intercom:2"]
    assert all(c.source == "intercom" and "[email]" in c.text for c in items)
    assert "wrote 2 intercom items" in capsys.readouterr().err


def test_fetch_main_trustpilot_round_trip(fake_api, monkeypatch, tmp_path):
    fake_api.handler = _trustpilot_handler(total=3)
    monkeypatch.setattr(trustpilot, "BASE_URL", fake_api.url)
    monkeypatch.setenv("TRUSTPILOT_API_KEY", "key")
    out = tmp_path / "trustpilot.json"

    assert fetch_main(["trustpilot", "--domain", "metawin.com", "--out", str(out)]) == 0
    items = ingest.load(tmp_path)  # directory load auto-detects the JSON shape
    assert [c.id for c in items] == ["trustpilot:r0", "trustpilot:r1", "trustpilot:r2"]
    assert all(c.source == "trustpilot" and c.author_hash == "" for c in items)


def test_fetch_main_requires_env_var_and_reports_errors(fake_api, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("INTERCOM_TOKEN", raising=False)
    assert fetch_main(["intercom", "--since", "2026-08-01", "--out", str(tmp_path / "x.json")]) == 2
    assert "INTERCOM_TOKEN" in capsys.readouterr().err

    fake_api.handler = lambda s: (401, {}, {})
    monkeypatch.setattr(intercom, "BASE_URL", fake_api.url)
    monkeypatch.setenv("INTERCOM_TOKEN", "bad")
    assert fetch_main(["intercom", "--since", "2026-08-01", "--out", str(tmp_path / "x.json")]) == 1
    assert "needs read conversations permission" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


def test_cli_has_no_secret_arguments():
    with pytest.raises(SystemExit):
        fetch_main(["trustpilot", "--domain", "metawin.com", "--api-key", "x"])


# ------------------------------------------------------------------------ privacy

@pytest.mark.parametrize("text,expected", [
    ("mail a.b+c@mail.example.org now", "mail [email] now"),
    ("call +1 (555) 123-4567 or 07700900123", "call [phone] or [phone]"),
    ("sent 0.00012345 BTC, $5,000 lost", "sent 0.00012345 BTC, $5,000 lost"),
    ("on 2026-11-01 12:30 order 12345", "on 2026-11-01 12:30 order 12345"),
    ("tx 0x9f3a1b2c4d5e6f7a8b9c", "tx 0x9f3a1b2c4d5e6f7a8b9c"),
])
def test_redact(text, expected):
    assert redact(text) == expected
