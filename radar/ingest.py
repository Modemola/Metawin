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

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


_RELATIVE = re.compile(r"\b(ago|today|yesterday|just now)\b", re.I)


def is_relative_date(value: str) -> bool:
    """'2 days ago' depends on when the page was copied, so it's approximate."""
    return bool(_RELATIVE.search(value or ""))


def parse_date(value: str | int | float | None, today: date | None = None, dayfirst: bool = False) -> date | None:
    """Parse the date formats these sources actually emit; None if unknown or invalid.

    Slash dates are month-first (11/12/2026 = 12 Nov) unless `dayfirst`."""
    try:
        return _parse_date(value, today, dayfirst)
    except (ValueError, OverflowError, OSError):
        return None  # e.g. "Nov 31, 2026" or 13/13/2026: undated beats crashing the run


def _parse_date(value, today, dayfirst):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # unix seconds (Intercom)
        return datetime.fromtimestamp(value, tz=timezone.utc).date()
    s = str(value).strip()
    # "Updated Nov 12, 2026", "Nov 12, 2026 · Edited", "Posted 2 days ago"
    s = re.sub(r"^(updated|edited|posted|reviewed)\s+", "", s, flags=re.I)
    s = re.sub(r"\s*[·•|-]\s*(updated|edited)$", "", s, flags=re.I).strip()
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
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", s)  # 11/12/2026
    if m:
        a, b = int(m[1]), int(m[2])
        day, month = (a, b) if dayfirst or a > 12 else (b, a)
        return date(int(m[3]), month, day)
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

# Rating line variants seen when copying a review page: the image alt text
# ("Rated 1 out of 5 stars"), screen-reader text without "Rated", a "1/5"
# form, or the stars themselves as characters.
_RATED = re.compile(
    r"^(?:Rated\s+)?([1-5])\s+out of\s+5(?:\s+stars?)?\.?$"
    r"|^Rating:?\s*([1-5])\s*/\s*5$"
    r"|^(★{1,5})[☆✩✰\s]*$",
    re.I,
)


def _rating(line: str) -> int | None:
    m = _RATED.match(line)
    if not m:
        return None
    if m[3]:
        return len(m[3])
    return int(m[1] or m[2])


_EXPERIENCE = re.compile(r"^Date of experience:?\s*(.+)$", re.I)
_REVIEW_COUNT = re.compile(r"^\d+\s+reviews?$", re.I)
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_NAME_LIKE = re.compile(r"^[^\W\d][\w'’.\- ]{0,38}$(?<![.!?,;:])", re.UNICODE)
_NOISE = {"•", "·", "useful", "share", "report", "verified", "invited", "redirected", "see more", "read more", "unprompted review"}
_BODY_END = re.compile(r"^(useful|share|report|reply from\b.*|see more|read more)$", re.I)


def parse_trustpilot_paste(raw: str, today: date | None = None) -> list[Complaint]:
    """Parse review text copied from trustpilot.com/review/<domain> in a browser.

    Each review is anchored on its "Rated N out of 5 stars" line. Reviewer name,
    country and review-count lines that precede the next anchor are dropped, and
    a "Reply from <company>" block marks that the company answered.
    """
    lines = [ln.strip() for ln in raw.splitlines()]
    anchors = [i for i, ln in enumerate(lines) if _rating(ln)]

    def header(anchor: int, floor: int) -> tuple[int, str]:
        """(index where this review's header starts, reviewer name).

        Walks back over country / "N reviews" / bullet / initials lines; the line
        above them is taken as the name only when an "N reviews" line was seen,
        so body text of the previous review is never swallowed."""
        j, saw_count, saw_country = anchor - 1, False, False
        while j > floor and (not lines[j] or _REVIEW_COUNT.match(lines[j]) or _COUNTRY.match(lines[j]) or lines[j].lower() in _NOISE):
            saw_count = saw_count or bool(_REVIEW_COUNT.match(lines[j]))
            saw_country = saw_country or bool(_COUNTRY.match(lines[j]))
            j -= 1
        candidate = lines[j] if j > floor else ""
        if not candidate or _BODY_END.match(candidate):
            return j + 1, ""
        # An "N reviews" line proves a profile header. A country code alone could
        # sit under a body line, so then the line must also look like a name:
        # short, few words, no sentence punctuation. Erring towards treating it as
        # a name keeps reviewer names out of quoted text.
        if (saw_count and len(candidate) <= 60) or (saw_country and _NAME_LIKE.match(candidate)):
            return j, candidate
        return j + 1, ""

    heads = [header(a, anchors[k - 1] if k else -1) for k, a in enumerate(anchors)]
    out: list[Complaint] = []
    for n, start in enumerate(anchors):
        end = heads[n + 1][0] if n + 1 < len(anchors) else len(lines)
        block = lines[start + 1 : end]
        rating = _rating(lines[start])
        author = heads[n][1]

        review_date, approx_date = None, False
        replied = any(re.match(r"^reply from\b", ln, re.I) for ln in block)
        content: list[str] = []
        for ln in block:
            if not ln or ln.lower() in _NOISE:
                continue
            m = _EXPERIENCE.match(ln)
            if m:
                if review_date is None or approx_date:
                    review_date, approx_date = parse_date(m[1], today) or review_date, False
                continue
            if _BODY_END.match(ln):
                break
            if not content and review_date is None:
                d = parse_date(ln, today)
                if d:
                    review_date, approx_date = d, is_relative_date(ln)
                    continue
            content.append(ln)

        # Profile initials / country of the next reviewer when no "Useful" line ended the body.
        while content and _COUNTRY.match(content[-1]):
            content.pop()
        if not content:
            continue
        title, body = content[0], "\n".join(content[1:])
        out.append(
            Complaint(
                # A relative date ("2 days ago") changes with the day the page was
                # copied, so it can't be part of the identity used for de-duplication.
                id=text_id("trustpilot", author, "" if approx_date else str(review_date), title, body),
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

_STAFF = {"admin", "bot", "team", "operator"}
_PLAYER = {"user", "lead", "contact"}


def parse_intercom_json(data: dict | list) -> list[Complaint]:
    """Intercom conversation objects. Uses the opening message plus every later
    message written by the player (author.type == "user" / "lead")."""
    convs = data.get("conversations", data.get("data", [])) if isinstance(data, dict) else data
    out = []
    for c in convs:
        src = c.get("source") or {}
        # Outbound conversations open with a message from staff or a bot; only
        # the player's own words count as the complaint.
        opener = (src.get("author") or {}).get("type")
        parts = [] if opener in _STAFF else [_strip_html(src.get("body", ""))]
        cp = c.get("conversation_parts") or {}
        for p in cp.get("conversation_parts", []) if isinstance(cp, dict) else cp:
            if (p.get("author") or {}).get("type") in _PLAYER and p.get("body"):
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

def _csv_rating(value: str) -> int | None:
    """'5', '5.0' or '4.5' -> 1..5; anything else -> None."""
    try:
        r = round(float(value))
    except (TypeError, ValueError):
        return None
    return r if 1 <= r <= 5 else None


def parse_csv(path: Path) -> list[Complaint]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip().lower(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]
    # Day-first if any slash date in the file can only be day-first (13/11/2026).
    slash = [re.match(r"^(\d{1,2})[/.](\d{1,2})[/.]\d{4}$", r.get("date") or r.get("created_at") or "") for r in rows]
    dayfirst = any(m and int(m[1]) > 12 for m in slash)
    for row in rows:
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
                date=parse_date(row.get("date") or row.get("created_at"), dayfirst=dayfirst),
                rating=_csv_rating(rating) if rating else None,
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
