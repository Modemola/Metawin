"""Core data types shared by every stage of the pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date


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
        # Tickets have no rating: treat them as complaints by default.
        return self.rating is None or self.rating <= 2


def hash_author(name: str) -> str:
    """Stable, non-reversible id so repeat complainers can be counted without names."""
    name = name.strip().lower()
    if not name:
        return ""
    return hashlib.sha256(name.encode()).hexdigest()[:12]


def text_id(source: str, *parts: str) -> str:
    h = hashlib.sha256("\x1f".join((source, *parts)).encode()).hexdigest()[:16]
    return f"{source}:{h}"
