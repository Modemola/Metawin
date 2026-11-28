"""Core data types shared by every stage of the pipeline."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

FORUM_SOURCES = {"bitcointalk"}

# On forums most posts are bystanders discussing someone else's problem. A post
# counts as a complaint only if the writer describes their own experience.
_FIRST_PERSON = re.compile(
    r"\bmy (account|acc|withdraw\w*|deposit\w*|balance|funds|money|bonus|lossback|rakeback|winnings|ticket|kyc|documents|"
    r"wallet|cash ?out|payout|w/d|reward\w*|vip|request)\b|"
    r"\bi (have |had |'ve )?(deposited|withdrew|requested|won|lost|sent|tried|contacted|waited|was (banned|locked|asked)|"
    r"can'?t|cannot|couldn'?t|never (got|received)|still haven'?t|am (still )?waiting)\b|"
    r"\b(locked|kicked|banned) me\b|\bme and my\b",
    re.I,
)


@dataclass
class Complaint:
    """One piece of player feedback from any source (review, ticket, post)."""

    id: str
    source: str  # "trustpilot", "intercom", "csv", ...
    text: str
    date: date | None = None
    title: str = ""
    rating: int | None = None  # 1-5 where the source has stars
    author_hash: str = ""  # never store raw names; see hash_author()
    url: str = ""
    company_replied: bool | None = None  # None = source doesn't tell us

    # Filled in by classification
    category: str = "other"
    labels: list[str] = field(default_factory=list)
    promos: list[str] = field(default_factory=list)
    mentions_bot_support: bool = False
    severity: int = 1  # 1 = minor, 2 = money/access affected, 3 = large sum or legal/threat
    summary: str = ""
    needs_human: bool = False
    classified_by: str = "rules"

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.text}".strip()

    @property
    def is_negative(self) -> bool:
        if self.rating is not None:
            return self.rating <= 2
        # Forum threads mix complaints with commentary: only posts that name a
        # problem count. Support tickets have no rating and are complaints by default.
        if self.source in FORUM_SOURCES:
            return self.category != "other" and bool(_FIRST_PERSON.search(self.full_text))
        return True


def hash_author(name: str) -> str:
    """Stable, non-reversible id so repeat complainers can be counted without names."""
    name = name.strip().lower()
    if not name:
        return ""
    return hashlib.sha256(name.encode()).hexdigest()[:12]


def text_id(source: str, *parts: str) -> str:
    h = hashlib.sha256("\x1f".join((source, *parts)).encode()).hexdigest()[:16]
    return f"{source}:{h}"
