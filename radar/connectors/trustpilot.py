"""Fetch a business's own reviews from the Trustpilot public API (v1).

API shapes used (the API key goes in the ``apikey`` query parameter):

* ``GET /business-units/find?name=<domain>`` returns ``{"id": "<business unit id>",
  "displayName": ..., ...}``; 404 if no profile matches.
* ``GET /business-units/{id}/reviews?perPage=100&page=N&orderBy=createdat.desc``
  returns ``{"reviews": [{"id", "stars", "title", "text", "createdAt",
  "companyReply": null | {"text", "createdAt", ...}, "consumer": {...}}],
  "links": [...]}``. A page shorter than ``perPage`` is the last one.

Each returned review keeps only what `radar.ingest.parse_trustpilot_json` reads. The
consumer object is emptied (no display names are stored at all) and contact
details in review text are redacted.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from typing import Any

from ._http import ConnectorError, request_json
from .privacy import redact

BASE_URL = "https://api.trustpilot.com/v1"
PER_PAGE = 100
AUTH_HINT = "Trustpilot API key rejected: use a key for the business's own Trustpilot profile"

Progress = Callable[[str], None]


def fetch_reviews(
    api_key: str,
    business_unit_id: str | None = None,
    domain: str | None = None,
    max_reviews: int = 5000,
    base_url: str = BASE_URL,
    progress: Progress | None = None,
) -> list[dict]:
    """Newest-first reviews for a business unit (given directly or found by domain).

    Wrap the result as ``{"reviews": [...]}`` to get the file shape
    `radar.ingest.parse_trustpilot_json` reads. Raises `ConnectorError` on any
    unrecoverable failure."""
    log = progress or (lambda _msg: None)
    unit = business_unit_id or _find_business_unit(base_url, api_key, domain, log)
    reviews: list[dict] = []
    page = 1
    while len(reviews) < max_reviews:
        batch = _reviews_page(base_url, api_key, unit, page)
        reviews.extend(clean_review(r) for r in batch[: max_reviews - len(reviews)])
        log(f"trustpilot: page {page}, {len(reviews)} reviews so far")
        if len(batch) < PER_PAGE:
            break
        page += 1
    return reviews


def _url(base_url: str, path: str, api_key: str, **params: Any) -> str:
    query = urllib.parse.urlencode({"apikey": api_key, **params})
    return f"{base_url}{path}?{query}"


def _find_business_unit(base_url: str, api_key: str, domain: str | None, log: Progress) -> str:
    """Resolve a domain such as "metawin.com" to its business unit id."""
    if not domain:
        raise ConnectorError("give either a Trustpilot business unit id or a domain")
    found = request_json(
        "GET",
        _url(base_url, "/business-units/find", api_key, name=domain),
        headers={"Accept": "application/json"},
        auth_hint=AUTH_HINT,
        not_found_hint=f"no Trustpilot business profile found for {domain!r}",
    )
    unit = (found or {}).get("id")
    if not unit:
        raise ConnectorError(f"no Trustpilot business profile found for {domain!r}")
    log(f"trustpilot: {domain} is business unit {unit}")
    return str(unit)


def _reviews_page(base_url: str, api_key: str, unit: str, page: int) -> list[dict]:
    path = f"/business-units/{urllib.parse.quote(unit, safe='')}/reviews"
    data = request_json(
        "GET",
        _url(base_url, path, api_key, perPage=PER_PAGE, page=page, orderBy="createdat.desc"),
        headers={"Accept": "application/json"},
        auth_hint=AUTH_HINT,
        not_found_hint=f"Trustpilot business unit {unit!r} not found",
    )
    return list((data or {}).get("reviews") or [])


def clean_review(r: dict[str, Any]) -> dict[str, Any]:
    """Keep id, stars, title, text, createdAt and companyReply; drop the reviewer."""
    reply = r.get("companyReply")
    return {
        "id": r.get("id"),
        "stars": r.get("stars"),
        "title": redact(r.get("title")),
        "text": redact(r.get("text")),
        "createdAt": r.get("createdAt"),
        "companyReply": {"text": redact(reply.get("text")), "createdAt": reply.get("createdAt")} if reply else None,
        "consumer": {},
    }
