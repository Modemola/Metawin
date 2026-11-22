"""Turn classified complaints into the numbers a support/ops lead acts on."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .models import Complaint
from .taxonomy import CATEGORIES, category_label

SPIKE_MIN_COUNT = 4  # ignore "spikes" of a handful of reviews
MIN_HISTORY_WEEKS = 2  # need some history before calling anything unusual
SPIKE_RATIO = 2.5  # this week vs the average of the previous BASELINE_WEEKS
BASELINE_WEEKS = 4


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass
class Spike:
    week: date
    category: str
    count: int
    baseline: float
    promos: list[tuple[str, int]]  # promos most mentioned in the spike's complaints

    @property
    def label(self) -> str:
        return category_label(self.category)


@dataclass
class Report:
    items: list[Complaint]
    negatives: list[Complaint]
    weeks: list[date]
    by_category: list[tuple[str, int]]
    weekly_total: dict[date, int]
    weekly_by_category: dict[str, dict[date, int]]
    spikes: list[Spike]
    promo_totals: list[tuple[str, int]]
    promo_by_category: dict[str, Counter]
    stats: dict = field(default_factory=dict)

    def examples(self, category: str, n: int = 3) -> list[Complaint]:
        pool = [c for c in self.negatives if c.category == category]
        pool.sort(key=lambda c: (-c.severity, -(c.date.toordinal() if c.date else 0)))
        return pool[:n]


def analyze(items: list[Complaint]) -> Report:
    negatives = [c for c in items if c.is_negative]
    dated = [c for c in negatives if c.date]

    weeks: list[date] = []
    if dated:
        w, last = week_start(min(c.date for c in dated)), week_start(max(c.date for c in dated))
        while w <= last:
            weeks.append(w)
            w += timedelta(days=7)

    weekly_total = {w: 0 for w in weeks}
    weekly_by_cat: dict[str, dict[date, int]] = {k: {w: 0 for w in weeks} for k in CATEGORIES}
    for c in dated:
        w = week_start(c.date)
        weekly_total[w] += 1
        weekly_by_cat[c.category][w] += 1

    by_cat = Counter(c.category for c in negatives)
    promo_totals = Counter(p for c in negatives for p in c.promos)
    promo_by_cat: dict[str, Counter] = defaultdict(Counter)
    for c in negatives:
        for p in c.promos:
            promo_by_cat[p][c.category] += 1

    spikes = _find_spikes(weeks, weekly_by_cat, dated)

    rated = [c for c in items if c.rating]
    reply_known = [c for c in negatives if c.company_replied is not None]
    support_related = [c for c in negatives if "support" in c.labels]
    authors = Counter(c.author_hash for c in negatives if c.author_hash)
    stats = {
        "total": len(items),
        "negative": len(negatives),
        "avg_rating": round(sum(c.rating for c in rated) / len(rated), 2) if rated else None,
        "reply_rate": round(sum(c.company_replied for c in reply_known) / len(reply_known), 3) if reply_known else None,
        "reply_known": len(reply_known),
        "unreplied_severe": sum(1 for c in reply_known if not c.company_replied and c.severity >= 2),
        "bot_mentions": sum(c.mentions_bot_support for c in negatives),
        "support_related": len(support_related),
        "needs_human": sum(c.needs_human for c in negatives),
        "money_related": sum(c.severity >= 2 for c in negatives),
        "promo_related": sum(bool(c.promos) for c in negatives),
        "repeat_complainers": sum(1 for n in authors.values() if n > 1),
        "date_from": min((c.date for c in items if c.date), default=None),
        "date_to": max((c.date for c in items if c.date), default=None),
        "undated": sum(1 for c in negatives if not c.date),
        "sources": dict(Counter(c.source for c in items)),
        "classified_by": dict(Counter(c.classified_by for c in items)),
    }
    return Report(
        items=items,
        negatives=negatives,
        weeks=weeks,
        by_category=by_cat.most_common(),
        weekly_total=weekly_total,
        weekly_by_category=weekly_by_cat,
        spikes=spikes,
        promo_totals=promo_totals.most_common(),
        promo_by_category=dict(promo_by_cat),
        stats=stats,
    )


def _find_spikes(weeks: list[date], weekly_by_cat: dict[str, dict[date, int]], dated: list[Complaint]) -> list[Spike]:
    spikes = []
    for cat, series in weekly_by_cat.items():
        values = [series[w] for w in weeks]
        for i, w in enumerate(weeks):
            prior = values[max(0, i - BASELINE_WEEKS) : i]
            if len(prior) < MIN_HISTORY_WEEKS:
                continue
            baseline = sum(prior) / len(prior)
            if values[i] >= SPIKE_MIN_COUNT and values[i] >= SPIKE_RATIO * max(baseline, 1.0):
                promos = Counter(
                    p for c in dated if c.category == cat and week_start(c.date) == w for p in c.promos
                ).most_common(3)
                spikes.append(Spike(w, cat, values[i], round(baseline, 1), promos))
    spikes.sort(key=lambda s: (s.week, s.count), reverse=True)
    return spikes
