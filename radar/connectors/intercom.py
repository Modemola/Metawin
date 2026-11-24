"""Fetch support conversations from the Intercom REST API (version 2.11).

API shapes used:

* ``POST /conversations/search`` with body::

      {"query": {"operator": "AND", "value": [
           {"field": "created_at", "operator": ">", "value": <unix seconds>},
           {"field": "created_at", "operator": "<", "value": <unix seconds>}]},
       "pagination": {"per_page": 150, "starting_after": "<cursor>"}}

  returns ``{"conversations": [{"id": ..., ...}], "pages": {"next":
  {"starting_after": "<cursor>"}}}``; ``pages.next`` is absent on the last page.

* ``GET /conversations/{id}`` returns the full conversation, including
  ``source`` ({"subject", "body", "author"}) and ``conversation_parts``
  ({"conversation_parts": [{"part_type", "body", "author": {"type", "name",
  "email", ...}}]}).

The output keeps only what `radar.ingest.parse_intercom_json` reads. Contacts,
author names and emails are dropped, and contact details inside message
bodies are redacted (see `radar.connectors.privacy`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from ._http import request_json
from .privacy import redact

BASE_URL = "https://api.intercom.io"
INTERCOM_VERSION = "2.11"
PER_PAGE = 150
AUTH_HINT = "Intercom token rejected: needs read conversations permission"

Progress = Callable[[str], None]


def fetch_conversations(
    token: str,
    since: date,
    until: date | None = None,
    max_conversations: int = 5000,
    base_url: str = BASE_URL,
    progress: Progress | None = None,
) -> list[dict]:
    """Up to `max_conversations` conversations created on or after `since` (and on
    or before `until`, both UTC days), as privacy-stripped dicts.

    Raises `ConnectorError` on a rejected token or a persistent API failure."""
    log = progress or (lambda _msg: None)
    headers = _headers(token)
    ids = list(_search_ids(base_url, headers, since, until, max_conversations, log))
    log(f"intercom: found {len(ids)} conversations, downloading messages")
    out = []
    for n, conv_id in enumerate(ids, 1):
        full = request_json("GET", f"{base_url}/conversations/{conv_id}", headers=headers, auth_hint=AUTH_HINT)
        out.append(clean_conversation(full))
        if n % 50 == 0 or n == len(ids):
            log(f"intercom: {n}/{len(ids)} conversations downloaded")
    return out


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Intercom-Version": INTERCOM_VERSION,
    }


def search_query(since: date, until: date | None) -> dict[str, Any]:
    """Search filter for created_at in [since 00:00 UTC, until+1 00:00 UTC).

    Intercom only offers strict ">" / "<", so the lower bound is one second
    before midnight to include conversations created exactly at 00:00:00."""
    clauses: list[dict[str, Any]] = [
        {"field": "created_at", "operator": ">", "value": _unix(since) - 1},
    ]
    if until is not None:
        clauses.append({"field": "created_at", "operator": "<", "value": _unix(until + timedelta(days=1))})
    return {"operator": "AND", "value": clauses}


def _search_ids(
    base_url: str, headers: dict[str, str], since: date, until: date | None, limit: int, log: Progress
) -> Iterator[str]:
    """Page through /conversations/search and yield up to `limit` unique ids."""
    seen: set[str] = set()
    cursor: str | None = None
    while len(seen) < limit:
        pagination: dict[str, Any] = {"per_page": PER_PAGE}
        if cursor:
            pagination["starting_after"] = cursor
        body = {"query": search_query(since, until), "pagination": pagination}
        page = request_json("POST", f"{base_url}/conversations/search", headers=headers, body=body, auth_hint=AUTH_HINT)
        for conv in page.get("conversations") or []:
            conv_id = str(conv.get("id"))
            if conv_id not in seen and len(seen) < limit:
                seen.add(conv_id)
                yield conv_id
        log(f"intercom: searched {len(seen)} conversations so far")
        cursor = ((page.get("pages") or {}).get("next") or {}).get("starting_after")
        if not cursor:
            return


def clean_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    """Reduce a full Intercom conversation to the fields the radar reads.

    Keeps id, created_at, source.subject/body and each part's author *type*
    and body. Everything that identifies a person is dropped."""
    src = conv.get("source") or {}
    parts_obj = conv.get("conversation_parts") or {}
    parts = parts_obj.get("conversation_parts", []) if isinstance(parts_obj, dict) else parts_obj
    return {
        "id": str(conv.get("id")),
        "created_at": conv.get("created_at"),
        "source": {
            "subject": redact(src.get("subject")),
            "body": redact(src.get("body")),
            # Type only ("user", "lead", "admin", "bot"): outbound messages start with staff text.
            "author": {"type": (src.get("author") or {}).get("type")},
        },
        "conversation_parts": {
            "conversation_parts": [
                {"author": {"type": (p.get("author") or {}).get("type")}, "body": redact(p.get("body"))}
                for p in parts
                if p.get("body")
            ]
        },
    }


def _unix(d: date) -> int:
    return int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())
