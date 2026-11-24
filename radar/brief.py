"""The pitch: a short findings brief, plus an internal go/no-go on whether the
data is strong enough to send at all.

Two outputs, kept separate on purpose:
* brief.md   - sendable. Leads with findings, neutral tone, short quotes, no names.
* verdict.md - internal. Is there enough data, and is any finding strong enough
               that a founder would act on it? The thresholds are a heuristic
               starting point, not a guarantee; a person makes the final call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .analyze import EventWindow, Report
from .models import Complaint
from .taxonomy import category_label

# What "enough data" means for the verdict.
MIN_NEGATIVE = 50
MIN_SPAN_DAYS = 60

# Categories too generic to lead a pitch with: everyone knows players dislike support.
GENERIC = {"support", "other"}

# How each category reads inside a sentence ("X% of negative reviews are about ...").
PHRASE = {
    "deposit_missing": "deposits that never showed up",
    "withdrawal": "withdrawals being delayed or blocked",
    "promo_not_credited": "promotions or bonuses that were never credited",
    "account_restricted": "accounts being closed, restricted or flagged",
    "kyc": "verification (KYC) demands",
    "fairness": "games feeling rigged or unfair",
    "fees": "fees",
    "ux_bugs": "bugs and site problems",
    "support": "support being slow or unhelpful",
    "other": "other issues",
}


def _distinct(items: list[Complaint], used: set[str], n: int) -> list[Complaint]:
    """Up to n quotes, skipping ones already shown and near-identical texts."""
    out, seen = [], set()
    for c in items:
        key = " ".join(c.text.lower().split())[:120]
        if c.id in used or key in used or key in seen:
            continue
        out.append(c)
        seen.add(key)
        if len(out) == n:
            break
    return out


@dataclass
class Finding:
    key: str
    headline: str
    detail: str
    strength: float  # 0..1, used to rank
    strong: bool  # would a founder plausibly act on this alone?
    quotes: list[Complaint] = field(default_factory=list)


@dataclass
class Verdict:
    decision: str  # "GO", "MAYBE", "NO-GO"
    enough_data: bool
    reasons: list[str]
    findings: list[Finding]


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def find(r: Report, events: list[EventWindow] | None = None) -> list[Finding]:
    s, neg = r.stats, r.negatives
    n = len(neg) or 1
    out: list[Finding] = []

    # 1. The dominant specific complaint.
    specific = [(k, c) for k, c in r.by_category if k not in GENERIC]
    if specific:
        k, c = specific[0]
        share = c / n
        promos = [p for p, _ in _promos_for(r, k)][:2]
        out.append(Finding(
            "top_category",
            f"{_pct(share)} of negative reviews are about {PHRASE.get(k, category_label(k).lower())}",
            f"{c} of {len(neg)} negative reviews"
            + (f"; the promotions most often named are {', '.join(promos)}" if promos else "") + ".",
            strength=min(1.0, share / 0.4),
            strong=share >= 0.30 and c >= 10,
            quotes=r.examples(k, 20),
        ))

    # 2. A single promotion behind many complaints.
    if r.promo_totals:
        p, c = r.promo_totals[0]
        cat, cc = r.promo_by_category[p].most_common(1)[0]
        share = c / n
        quotes = sorted((x for x in neg if p in x.promos), key=lambda x: (-x.severity, -(x.date.toordinal() if x.date else 0)))[:20]
        out.append(Finding(
            "promo",
            f"{p} is named in {_pct(share)} of negative reviews",
            f"{c} reviews mention it; {cc} of them are about {PHRASE.get(cat, category_label(cat).lower())}.",
            strength=min(1.0, share / 0.25),
            strong=share >= 0.10 and c >= 8,
            quotes=quotes,
        ))
        # If that promo is mostly behind the top category already, say it once.
        if len(out) == 2 and out[0].key == "top_category" and specific[0][0] == cat and cc / c >= 0.6:
            top, promo = out
            top.detail = f"{specific[0][1]} of {len(neg)} negative reviews. {p} alone is named in {c} of them ({_pct(c / n)} of all negative reviews)."
            top.strong = top.strong or promo.strong
            out.pop()

    # 3. Unanswered money complaints sitting in public.
    if s["reply_rate"] is not None and s["reply_known"] >= 20:
        rr, open_money = s["reply_rate"], s["unreplied_severe"]
        out.append(Finding(
            "reply_gap",
            f"Only {_pct(rr)} of negative reviews have a public reply",
            f"{open_money} reviews about money or account access are unanswered, and anyone checking the site before depositing can see them.",
            strength=min(1.0, (1 - rr) * open_money / 40),
            strong=rr <= 0.25 and open_money >= 20,
            quotes=[c for c in neg if c.company_replied is False and c.severity >= 2][:20],
        ))

    # 4. Complaints aimed at the automated support itself.
    bot = s["bot_mentions"]
    if bot:
        share = bot / n
        out.append(Finding(
            "bot_support",
            f"{bot} negative reviews say support is a bot or they couldn't reach a person",
            f"That's {_pct(share)} of negative reviews and {_pct(bot / max(s['support_related'], 1))} of those mentioning support.",
            strength=min(1.0, share / 0.2),
            strong=bot >= 10 and share >= 0.10,
            quotes=[c for c in neg if c.mentions_bot_support][:20],
        ))

    # 5. A recent spike, ideally tied to a promotion.
    if r.spikes and s["date_to"]:
        recent = [sp for sp in r.spikes if sp.week >= s["date_to"] - timedelta(days=90)]
        if recent:
            sp = max(recent, key=lambda x: x.count / max(x.baseline, 1))
            promo = f", mostly mentioning {sp.promos[0][0]}" if sp.promos else ""
            out.append(Finding(
                "spike",
                f"{category_label(sp.category)} complaints jumped to {sp.count} in the week of {sp.week:%d %b}",
                f"Against ~{sp.baseline:g} a week before{promo}. A spike like this can be caught within days.",
                strength=min(1.0, sp.count / max(sp.baseline, 1) / 5),
                strong=sp.count >= 6 and bool(sp.promos),
                quotes=[c for c in r.negatives if c.category == sp.category and c.date and sp.week <= c.date < sp.week + timedelta(days=7)][:20],
            ))

    # 6. A named event (credit drop) the user asked about.
    for ev in events or []:
        inc = ev.biggest_increases(1)
        if not inc:
            continue
        k, b, a = inc[0]
        top_promo = ev.promos_after.most_common(1)
        out.append(Finding(
            f"event:{ev.name}",
            f"After {ev.name}, complaints about {PHRASE.get(k, category_label(k).lower())} went from {b} to {a}",
            f"Negative reviews in the {ev.days} days after vs before: {ev.total_after} vs {ev.total_before}"
            + (f"; {top_promo[0][0]} named {top_promo[0][1]} times." if top_promo else "."),
            strength=min(1.0, (a - b) / 10),
            strong=a - b >= 5,
            quotes=[c for c in ev.after_items if c.category == k][:20],
        ))

    # Events the user named lead: that's the conversation they want to start.
    out.sort(key=lambda f: (f.strong, f.key.startswith("event:"), f.strength), reverse=True)
    return out


def _promos_for(r: Report, category: str) -> list[tuple[str, int]]:
    rows = [(p, cats[category]) for p, cats in r.promo_by_category.items() if cats[category]]
    return sorted(rows, key=lambda x: -x[1])


def assess(r: Report, events: list[EventWindow] | None = None) -> Verdict:
    s = r.stats
    findings = find(r, events)
    span = (s["date_to"] - s["date_from"]).days if s["date_from"] and s["date_to"] else 0
    enough = s["negative"] >= MIN_NEGATIVE and span >= MIN_SPAN_DAYS
    strong = [f for f in findings if f.strong]

    reasons = [
        f"{s['negative']} negative reviews over {span} days "
        + ("— enough to stand behind the numbers." if enough else f"— thin (want {MIN_NEGATIVE}+ over {MIN_SPAN_DAYS}+ days); collect more pages."),
        f"{len(strong)} strong finding(s): " + ("; ".join(f.headline for f in strong) if strong else "none") + ".",
    ]
    rules_share = s["classified_by"].get("rules", 0) / max(s["total"], 1)
    if rules_share > 0.5:
        reasons.append(
            "Mostly classified by keyword rules, which scored 67% on a blind test of unseen phrasing (they miss "
            "complaints into 'other' and can't read non-English). Before quoting percentages, check 100+ rows with "
            "`radar label` and rerun with `--labels`, or classify with `--claude`."
        )

    if enough and len(strong) >= 2:
        decision = "GO"
    elif strong and (enough or len(strong) >= 2):
        decision = "MAYBE"
    else:
        decision = "NO-GO"
    return Verdict(decision, enough, reasons, findings)


def _q(c: Complaint, limit: int = 220) -> str:
    text = " ".join((c.text or c.title).split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    stars = f"{c.rating}★, " if c.rating else ""
    when = f"{c.date:%b %Y}" if c.date else "undated"
    return f'> "{text}" — {stars}{when}'


def render_brief(r: Report, v: Verdict, product: str = "MetaWin", max_findings: int = 3, sample: bool = False) -> str:
    """Sendable one-pager in Markdown (pastes cleanly into email, X DMs or Notion)."""
    s = r.stats
    picked = [f for f in v.findings if f.strong][:max_findings] or v.findings[:max_findings]
    period = f"{s['date_from']:%d %b %Y} – {s['date_to']:%d %b %Y}" if s["date_from"] else "the collected period"
    lines = ["> **SAMPLE DATA. Do not send.** Every review behind this brief is synthetic.", ""] if sample else []
    lines += [
        f"# What {product} players are saying: {period}",
        "",
        f"I collected and categorised {s['negative']} negative public reviews of {product} "
        f"({', '.join(s['sources'])}). Here are the {len(picked)} things that stood out.",
        "",
    ]
    used: set[str] = set()
    for i, f in enumerate(picked, 1):
        quotes = _distinct(f.quotes, used, 2)
        used.update(c.id for c in quotes)
        used.update(" ".join(c.text.lower().split())[:120] for c in quotes)
        lines += [f"## {i}. {f.headline}", "", f.detail, ""]
        lines += [_q(c) for c in quotes] + [""]
    lines += [
        "## What I can do with this",
        "",
        "I built a small tool that does this automatically: it sorts every review or ticket by cause, "
        "flags the week a promotion starts producing complaints, and drafts "
        "public replies that never confirm account details or promise money.",
        "",
        "Pointed at your Intercom conversations, it would show the same picture for private tickets, "
        "a far larger and more private dataset than public reviews. Happy to run it on a sample for free.",
        "",
        "_Method: public reviews only, reviewer names removed. Each review is counted once, under its main issue; "
        "the full row-level data is available._",
    ]
    return "\n".join(lines)


def render_verdict(v: Verdict) -> str:
    lines = [f"# Go / no-go: {v.decision}", ""]
    lines += [f"- {x}" for x in v.reasons]
    lines += ["", "## All findings, strongest first", ""]
    for f in v.findings:
        lines.append(f"- {'**strong**' if f.strong else 'weak'} ({f.strength:.2f}): {f.headline}. {f.detail}")
    lines += [
        "",
        "_GO = enough data and 2+ strong findings. MAYBE = one strong finding, or strong findings on thin data. "
        "The thresholds are a starting heuristic: read the quotes and the CSV before deciding._",
    ]
    return "\n".join(lines)
