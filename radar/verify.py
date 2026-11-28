"""Check that every quotation in a document appears verbatim in the source data.

A pitch that misquotes a player is worse than no pitch. Run this on any brief
or evidence note before it leaves your hands:

    radar verify-quotes out/brief.md data/

Quotes are text in straight double quotes (at least 12 characters). An
ellipsis (…) inside a quote marks a cut: each piece must appear in the same
source item, in order. Whitespace differences are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Complaint

_QUOTE = re.compile(r'"([^"\n]{12,})"')


@dataclass
class QuoteCheck:
    quote: str
    found_in: str | None  # complaint id, or None if not found


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def check_quotes(document: str, items: list[Complaint]) -> list[QuoteCheck]:
    texts = [(c.id, _norm(c.full_text)) for c in items]
    results = []
    for q in _QUOTE.findall(document):
        pieces = [_norm(p) for p in q.split("…") if p.strip()]
        found = None
        for cid, text in texts:
            pos, ok = 0, True
            for piece in pieces:
                i = text.find(piece, pos)
                if i < 0:
                    ok = False
                    break
                pos = i + len(piece)
            if ok:
                found = cid
                break
        results.append(QuoteCheck(q, found))
    return results
