"""Command line: python -m radar <input> [options]"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import analyze, brief, classify, ingest, replies, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description="Analyse player complaints and build an HTML report.")
    ap.add_argument("input", help="File or folder: Trustpilot paste (.txt), Trustpilot/Intercom JSON, or CSV")
    ap.add_argument("-o", "--out", default="out/report.html", help="HTML report path (default: out/report.html)")
    ap.add_argument("--claude", action="store_true", help="Classify with Claude (needs ANTHROPIC_API_KEY); default is offline rules")
    ap.add_argument("--personalize-replies", action="store_true", help="Let Claude adapt reply drafts to each review")
    ap.add_argument("--title", default="Complaint Radar — MetaWin")
    ap.add_argument("--sample", action="store_true", help="Mark the report as synthetic sample data")
    ap.add_argument("--replies", type=int, default=15, help="How many reply drafts to include")
    ap.add_argument("--event", action="append", default=[], metavar="YYYY-MM-DD:NAME",
                    help="Compare complaints before/after a dated event, e.g. 2026-10-20:Lossback drop (repeatable)")
    ap.add_argument("--window", type=int, default=14, help="Days before/after each --event (default 14)")
    args = ap.parse_args(argv)

    items = ingest.load(args.input)
    if not items:
        print(f"No reviews found in {args.input}. For Trustpilot pastes, make sure each review's "
              "'Rated N out of 5 stars' line was copied.", file=sys.stderr)
        return 1
    print(f"[radar] loaded {len(items)} items", file=sys.stderr)

    if args.claude:
        classify.classify_with_claude(items)
    else:
        classify.classify_rules(items)

    try:
        events = [analyze.compare_event(items, *analyze.parse_event(e), days=args.window) for e in args.event]
    except ValueError as e:
        print(f"Bad --event value ({e}). Use YYYY-MM-DD:Name, e.g. 2026-10-20:Lossback drop", file=sys.stderr)
        return 2
    rep = analyze.analyze(items)
    queue = replies.reply_queue(items, args.replies)
    if args.personalize_replies:
        queue = replies.personalize_with_claude(queue)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(rep, queue, title=args.title, sample=args.sample, events=events), encoding="utf-8")
    _write_csv(items, out.with_suffix(".csv"))
    out.with_suffix(".json").write_text(json.dumps(rep.stats, default=str, indent=2))

    verdict = brief.assess(rep, events)
    out.with_name("brief.md").write_text(brief.render_brief(rep, verdict, sample=args.sample), encoding="utf-8")
    out.with_name("verdict.md").write_text(brief.render_verdict(verdict), encoding="utf-8")

    s = rep.stats
    print(f"[radar] {s['negative']} negative of {s['total']}; top issues: "
          + ", ".join(f"{k} {n}" for k, n in rep.by_category[:3]), file=sys.stderr)
    print(f"[radar] {len(rep.spikes)} spike(s); report -> {out}", file=sys.stderr)
    print(f"[radar] go/no-go: {verdict.decision} -> {out.with_name('verdict.md')}; pitch draft -> {out.with_name('brief.md')}", file=sys.stderr)
    return 0


def _write_csv(items, path: Path) -> None:
    """Row-level export so the classification can be checked and corrected by hand."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "source", "date", "rating", "category", "labels", "promos", "severity",
                    "needs_human", "mentions_bot_support", "company_replied", "classified_by", "summary", "title", "text"])
        for c in items:
            w.writerow([c.id, c.source, c.date or "", c.rating or "", c.category, "|".join(c.labels), "|".join(c.promos),
                        c.severity, c.needs_human, c.mentions_bot_support,
                        "" if c.company_replied is None else c.company_replied, c.classified_by, c.summary, c.title, c.text])
