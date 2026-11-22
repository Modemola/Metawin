"""Readers for each feedback source.

Supported inputs (auto-detected by `load()`):

* Trustpilot reviews copied from a browser and pasted into a .txt file.
* Trustpilot Business API JSON (what MetaWin can pull for its own profile).
* Intercom conversations JSON (list or {"conversations": [...]} from the
  Conversations API / export).
* Generic CSV with at least `text` and `date` columns (Discord exports, sheets).
"""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Complaint, hash_author, text_id

# --------------------------------------------------------------------------- dates

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def parse_date(value: str | int | float | None, today: date | None = None) -> date | None:
    """Parse the date formats these sources actually emit; None if unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # unix seconds (Intercom)
        return datetime.fromtimestamp(value, tz=timezone.utc).date()
    s = str(value).strip()
    today = today or date.today()

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)  # ISO
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})$", s)  # Nov 12, 2026
    if m and m[1].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS.index(m[1].lower()) + 1, int(m[2]))
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})$", s)  # 12 Nov 2026
    if m and m[2].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS.index(m[2].lower()) + 1, int(m[1]))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)  # 11/12/2026 (US order)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2]))
    m = re.match(r"^(\d+|an?|one)\s+(minute|hour|day|week|month)s?\s+ago$", s, re.I)
    if m:
        n = 1 if m[1].lower() in ("a", "an", "one") else int(m[1])
        days = {"minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[m[2].lower()]
        return today - timedelta(days=days)
    if s.lower() in ("today", "just now"):
        return today
    if s.lower() == "yesterday":
        return today - timedelta(days=1)
    return None


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


# --------------------------------------------------------- Trustpilot (pasted text)

_RATED = re.compile(r"^Rated\s+([1-5])\s+out of\s+5\s+stars?$", re.I)
_EXPERIENCE = re.compile(r"^Date of experience:?\s*(.+)$", re.I)
_REVIEW_COUNT = re.compile(r"^\d+\s+reviews?$", re.I)
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_NOISE = {"•", "·", "useful", "share", "report", "verified", "invited", "redirected", "see more", "read more", "unprompted review"}
_BODY_END = re.compile(r"^(useful|share|report|reply from\b.*|see more|read more)$", re.I)


def parse_trustpilot_paste(raw: str, today: date | None = None) -> list[Complaint]:
    """Parse review text copied from trustpilot.com/review/<domain> in a browser.

    Each review is anchored on its "Rated N out of 5 stars" line. Reviewer name,
    country and review-count lines that precede the next anchor are dropped, and
    a "Reply from <company>" block marks that the company answered.
    """
    lines = [ln.strip() for ln in raw.splitlines()]
    anchors = [i for i, ln in enumerate(lines) if _RATED.match(ln)]

    def header(anchor: int, floor: int) -> tuple[int, str]:
        """(index where this review's header starts, reviewer name).

        Walks back over country / "N reviews" / bullet lines; only if at least
        one of those markers is present is the line above them taken as the
        name, so body text of the previous review is never swallowed."""
        j, saw_marker = anchor - 1, False
        while j > floor and (not lines[j] or _REVIEW_COUNT.match(lines[j]) or _COUNTRY.match(lines[j]) or lines[j].lower() in _NOISE):
            saw_marker = saw_marker or bool(lines[j])
            j -= 1
        if saw_marker and j > floor and lines[j] and not _BODY_END.match(lines[j]) and len(lines[j]) <= 60:
            return j, lines[j]
        return anchor, ""

    heads = [header(a, anchors[k - 1] if k else -1) for k, a in enumerate(anchors)]
    out: list[Complaint] = []
    for n, start in enumerate(anchors):
        end = heads[n + 1][0] if n + 1 < len(anchors) else len(lines)
        block = lines[start + 1 : end]
        rating = int(_RATED.match(lines[start])[1])
        author = heads[n][1]

        review_date = None
        replied = any(re.match(r"^reply from\b", ln, re.I) for ln in block)
        content: list[str] = []
        for ln in block:
            if not ln or ln.lower() in _NOISE:
                continue
            m = _EXPERIENCE.match(ln)
            if m:
                review_date = review_date or parse_date(m[1], today)
                continue
            if _BODY_END.match(ln):
                break
            if not content and review_date is None:
                d = parse_date(ln, today)
                if d:
                    review_date = d
                    continue
            content.append(ln)

        if not content:
            continue
        title, body = content[0], "\n".join(content[1:])
        out.append(
            Complaint(
                id=text_id("trustpilot", author, str(review_date), title, body),
                source="trustpilot",
                text=body or title,
                title=title if body else "",
                date=review_date,
                rating=rating,
                author_hash=hash_author(author),
                company_replied=replied,
            )
        )
    return out


# ------------------------------------------------------------ Trustpilot (API JSON)

def parse_trustpilot_json(data: dict | list) -> list[Complaint]:
    """Trustpilot Business API review objects (or a list of them)."""
    reviews = data.get("reviews", []) if isinstance(data, dict) else data
    out = []
    for r in reviews:
        consumer = r.get("consumer") or {}
        out.append(
            Complaint(
                id=f"trustpilot:{r.get('id') or text_id('trustpilot', r.get('text', ''))}",
                source="trustpilot",
                text=r.get("text", ""),
                title=r.get("title", ""),
                date=parse_date(r.get("createdAt") or (r.get("dates") or {}).get("publishedDate")),
                rating=r.get("stars") or r.get("rating"),
                author_hash=hash_author(consumer.get("displayName", "")),
                company_replied=bool(r.get("companyReply")),
            )
        )
    return out


# ---------------------------------------------------------------- Intercom (JSON)

def parse_intercom_json(data: dict | list) -> list[Complaint]:
    """Intercom conversation objects. Uses the opening message plus every later
    message written by the player (author.type == "user" / "lead")."""
    convs = data.get("conversations", data.get("data", [])) if isinstance(data, dict) else data
    out = []
    for c in convs:
        src = c.get("source") or {}
        parts = [_strip_html(src.get("body", ""))]
        cp = c.get("conversation_parts") or {}
        for p in cp.get("conversation_parts", []) if isinstance(cp, dict) else cp:
            if (p.get("author") or {}).get("type") in ("user", "lead", "contact") and p.get("body"):
                parts.append(_strip_html(p["body"]))
        text = "\n".join(t for t in parts if t)
        if not text:
            continue
        out.append(
            Complaint(
                id=f"intercom:{c.get('id')}",
                source="intercom",
                text=text,
                title=_strip_html(src.get("subject", "")),
                date=parse_date(c.get("created_at")),
                company_replied=True,  # tickets always get some reply; track resolution instead
            )
        )
    return out


# --------------------------------------------------------------------- CSV (generic)

def parse_csv(path: Path) -> list[Complaint]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            text = row.get("text") or row.get("body") or row.get("message") or row.get("content") or ""
            if not text:
                continue
            source = row.get("source") or "csv"
            rating = row.get("rating") or row.get("stars")
            replied = row.get("company_replied") or row.get("replied")
            out.append(
                Complaint(
                    id=row.get("id") or text_id(source, text),
                    source=source,
                    text=text,
                    title=row.get("title", ""),
                    date=parse_date(row.get("date") or row.get("created_at")),
                    rating=int(rating) if rating and rating.isdigit() else None,
                    author_hash=hash_author(row.get("author", "")),
                    url=row.get("url", ""),
                    company_replied=None if not replied else replied.lower() in ("1", "true", "yes", "y"),
                )
            )
    return out


# ------------------------------------------------------------------------ dispatch

def load(path: str | Path, today: date | None = None) -> list[Complaint]:
    path = Path(path)
    if path.is_dir():
        items: list[Complaint] = []
        for p in sorted(path.iterdir()):
            if p.suffix.lower() in (".txt", ".json", ".csv"):
                items.extend(load(p, today))
        return dedupe(items)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return parse_csv(path)
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(raw)
        sample = data if isinstance(data, list) else next(iter(v for v in data.values() if isinstance(v, list)), [])
        first = sample[0] if sample else {}
        if "conversation_parts" in first or "source" in first and isinstance(first.get("source"), dict):
            return parse_intercom_json(data)
        return parse_trustpilot_json(data)
    return parse_trustpilot_paste(raw, today)


def dedupe(items: list[Complaint]) -> list[Complaint]:
    """Overlapping paste/export files shouldn't double-count a review."""
    seen: dict[str, Complaint] = {}
    for c in items:
        seen.setdefault(c.id, c)
    return list(seen.values())
