"""Read public Bitcointalk forum threads (e.g. a casino's announcement thread or
complaint threads in "Scam Accusations") into rows the radar can analyse.

Bitcointalk is plain server-rendered HTML (SMF). Each topic page holds up to 20
posts; page N is ``index.php?topic=<id>.<20*N>``. Every post sits in a
``td.td_headerandpost`` cell with the timestamp in ``div.smalltext`` and the
body in ``div.post``; the author is in the preceding ``td.poster_info``.

Choices that matter:

* Quoted text (``div.quote``) is removed, so a complaint quoted in ten replies
  is counted once.
* Author names are hashed before anything is written: the output never holds
  a username.
* Requests are spaced out (`delay` seconds) and identify the tool, to stay a
  polite reader of a volunteer-run forum.
"""

from __future__ import annotations

import csv
import html
import re
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..models import hash_author
from .privacy import redact

BASE_URL = "https://bitcointalk.org/index.php"
PER_PAGE = 20
USER_AGENT = "complaint-radar/0.1 (research tool; low request rate)"
Progress = Callable[[str], None]

# Indirection so tests can avoid real waiting and real requests.
sleep: Callable[[float], None] = time.sleep


@dataclass
class Post:
    msg_id: str
    topic_id: str
    topic_title: str
    author_hash: str
    posted: date | None
    subject: str
    text: str
    is_op: bool

    @property
    def url(self) -> str:
        return f"https://bitcointalk.org/index.php?topic={self.topic_id}.msg{self.msg_id}#msg{self.msg_id}"


def fetch_page(topic_id: str, offset: int, base_url: str = BASE_URL, timeout: float = 30) -> str:
    req = urllib.request.Request(f"{base_url}?topic={topic_id}.{offset}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("latin-1")  # SMF serves ISO-8859-1


# ---------------------------------------------------------------------- parsing

_AUTHOR = re.compile(r'title="View the profile of ([^"]+)"')
_MSG_ID = re.compile(r'id="subject_(\d+)"')
_SUBJECT = re.compile(r'id="subject_\d+"><a [^>]*>(.*?)</a>', re.S)
_DATE = re.compile(r'<div class="smalltext">(?:<span[^>]*>)?(.*?)(?:</span>|</div>)', re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_PAGES = re.compile(r"topic=(\d+)\.(\d+)\"")


def _clean(fragment: str) -> str:
    """HTML fragment -> plain text, with quoted posts removed."""
    # Remove quotes innermost-first so nested quotes go too.
    prev = None
    while prev != fragment:
        prev = fragment
        fragment = re.sub(r'<div class="quoteheader">(?:(?!<div).)*?</div>', "", fragment, flags=re.S)
        fragment = re.sub(r'<div class="quote">(?:(?!<div class="quote">).)*?</div>', "", fragment, flags=re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<img[^>]*>", " ", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def parse_bitcointalk_date(value: str, today: date | None = None) -> date | None:
    """'March 30, 2026, 06:31:20 AM' or 'Today at 06:31:20 AM'."""
    value = html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()
    today = today or date.today()
    if value.lower().startswith("today"):
        return today
    m = re.match(r"([A-Z][a-z]+ \d{1,2}, \d{4})", value)
    if not m:
        return None
    try:
        return datetime.strptime(m[1], "%B %d, %Y").date()
    except ValueError:
        return None


def parse_page(page_html: str, topic_id: str, today: date | None = None) -> list[Post]:
    title_m = _TITLE.search(page_html)
    title = html.unescape(title_m[1]).strip() if title_m else ""
    posts = []
    for chunk in page_html.split('class="poster_info"')[1:]:
        info, _, rest = chunk.partition('class="td_headerandpost')  # may carry extra classes
        header, found, body = rest.partition('<div class="post">')
        if not found:
            continue
        # The body is followed by the post's footer cell; quotes inside it have
        # their own nested divs, so cut at the footer rather than at a </div>.
        end = body.find('<td valign="bottom"')
        body = body[:end] if end >= 0 else body
        body = body.rsplit("</div>", 1)[0]
        author_m, msg_m = _AUTHOR.search(info), _MSG_ID.search(header)
        if not msg_m:
            continue
        subj_m, date_m = _SUBJECT.search(header), _DATE.search(header)
        text = redact(_clean(body))
        if not text:
            continue
        posts.append(Post(
            msg_id=msg_m[1],
            topic_id=topic_id,
            topic_title=title,
            author_hash=hash_author(author_m[1]) if author_m else "",
            posted=parse_bitcointalk_date(date_m[1], today) if date_m else None,
            subject=html.unescape(re.sub(r"<[^>]+>", "", subj_m[1])).strip() if subj_m else "",
            text=text,
            is_op="This member is the topic-starter" in info,
        ))
    return posts


def last_offset(page_html: str, topic_id: str) -> int:
    offsets = [int(o) for t, o in _PAGES.findall(page_html) if t == topic_id and int(o) % PER_PAGE == 0]
    return max(offsets, default=0)


def fetch_topic(topic_id: str, max_pages: int = 200, delay: float = 3.0, since: date | None = None,
                base_url: str = BASE_URL, progress: Progress | None = None,
                fetch: Callable[[str, int, str], str] | None = None) -> Iterator[Post]:
    """All posts of a topic (newest pages last), optionally only those on/after `since`."""
    log = progress or (lambda _m: None)
    get = fetch or (lambda t, o, b: fetch_page(t, o, b))
    first = get(topic_id, 0, base_url)
    end = min(last_offset(first, topic_id), (max_pages - 1) * PER_PAGE)
    seen: set[str] = set()
    for offset in range(0, end + PER_PAGE, PER_PAGE):
        page = first if offset == 0 else get(topic_id, offset, base_url)
        parsed = parse_page(page, topic_id)
        expected = page.count('class="poster_info"')
        if len(parsed) < expected:
            # Empty posts are skipped legitimately, but a big gap means the page
            # layout changed (or the forum rate-limited us). Say so loudly.
            log(f"bitcointalk: WARNING topic {topic_id} page {offset // PER_PAGE + 1}: parsed {len(parsed)} of {expected} posts")
        for p in parsed:
            if p.msg_id in seen or (since and p.posted and p.posted < since):
                continue
            seen.add(p.msg_id)
            yield p
        log(f"bitcointalk: topic {topic_id} page {offset // PER_PAGE + 1}/{end // PER_PAGE + 1}")
        if offset < end:
            sleep(delay)


def write_csv(posts: list[Post], path: Path) -> None:
    """CSV that `radar.ingest.load()` reads (source=bitcointalk, no rating)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "source", "date", "title", "text", "author", "url"])
        for p in posts:
            title = p.subject if p.is_op else ""
            # `author` holds the hash already; ingest hashes it again, which is harmless.
            w.writerow([f"bitcointalk:{p.msg_id}", "bitcointalk", p.posted or "", title, p.text, p.author_hash, p.url])


def recent(posts: list[Post], days: int, today: date | None = None) -> list[Post]:
    cutoff = (today or date.today()) - timedelta(days=days)
    return [p for p in posts if p.posted and p.posted >= cutoff]
