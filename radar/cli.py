"""Command line.

    python -m radar report DATA [options]     build report.html, brief.md, verdict.md, CSV
    python -m radar eval DATA LABELS.csv      measure the classifier against human labels
    python -m radar label DATA                write a page for labelling complaints by hand
    python -m radar watch DATA                post Slack/Discord alerts for new spikes
    python -m radar fetch intercom|trustpilot pull a company's own data via its API

`python -m radar DATA ...` (no subcommand) still means `report`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from . import analyze, brief, classify, ingest, labels, replies, report, watch
from .models import Complaint

COMMANDS = ("report", "eval", "label", "watch", "fetch")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["report", *argv]  # backwards compatible: `radar DATA`
    if argv and argv[0] == "fetch":
        return _fetch(argv[1:])

    ap = argparse.ArgumentParser(prog="radar", description="Turn player complaints into categories, spike alerts and a pitch brief.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("report", help="Build the HTML report, brief, verdict and CSV")
    _common(p)
    p.add_argument("-o", "--out", default="out/report.html", help="HTML report path (default: out/report.html)")
    p.add_argument("--labels", help="CSV of human-checked categories that override the classifier")
    p.add_argument("--personalize-replies", action="store_true", help="Let Claude adapt reply drafts to each review")
    p.add_argument("--title", default="Complaint Radar — MetaWin")
    p.add_argument("--product", default="MetaWin", help="Company name used in the brief")
    p.add_argument("--sample", action="store_true", help="Mark outputs as synthetic sample data")
    p.add_argument("--replies", type=int, default=15, help="How many reply drafts to include")
    p.add_argument("--event", action="append", default=[], metavar="YYYY-MM-DD:NAME",
                   help="Compare complaints before/after a dated event, e.g. 2026-10-20:Lossback drop (repeatable)")
    p.add_argument("--window", type=int, default=14, help="Days before/after each --event (default 14)")

    p = sub.add_parser("eval", help="Score the classifier against a labels CSV")
    _common(p)
    p.add_argument("labels", help="labels.csv with id,category[,promos,mentions_bot_support]")
    p.add_argument("-o", "--out", default="out/eval.md")

    p = sub.add_parser("label", help="Write a page for checking/correcting categories by hand")
    _common(p)
    p.add_argument("-o", "--out", default="out/label.html")

    p = sub.add_parser("watch", help="Alert a Slack/Discord webhook about new spikes")
    _common(p)
    p.add_argument("--labels", help="Optional labels CSV to apply first")
    p.add_argument("--webhook-env", default="RADAR_WEBHOOK_URL", help="Env var holding the webhook URL (default RADAR_WEBHOOK_URL)")
    p.add_argument("--state", default=".cache/watch_state.json", help="Remembers which spikes were already sent")
    p.add_argument("--dry-run", action="store_true", help="Print alerts instead of posting them")

    args = ap.parse_args(argv)
    try:
        return {"report": _report, "eval": _eval, "label": _label, "watch": _watch}[args.cmd](args)
    except (labels.LabelError, ValueError) as e:
        print(f"[radar] {e}", file=sys.stderr)
        return 2


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", help="File or folder: Trustpilot paste (.txt), Trustpilot/Intercom JSON, or CSV")
    p.add_argument("--claude", action="store_true", help="Classify with Claude (needs ANTHROPIC_API_KEY); default is offline rules")


def _load_and_classify(args) -> list[Complaint] | None:
    items = ingest.load(args.input)
    if not items:
        print(f"[radar] No reviews found in {args.input}. For Trustpilot pastes, make sure each review's "
              "'Rated N out of 5 stars' line was copied.", file=sys.stderr)
        return None
    print(f"[radar] loaded {len(items)} items", file=sys.stderr)
    (classify.classify_with_claude if args.claude else classify.classify_rules)(items)
    if getattr(args, "labels", None):
        n = labels.apply_labels(items, labels.load_labels(args.labels))
        print(f"[radar] applied {n} human labels", file=sys.stderr)
    return items


def _report(args) -> int:
    items = _load_and_classify(args)
    if items is None:
        return 1
    try:
        events = [analyze.compare_event(items, *analyze.parse_event(e), days=args.window) for e in args.event]
    except ValueError as e:
        print(f"[radar] Bad --event value ({e}). Use YYYY-MM-DD:Name, e.g. 2026-10-20:Lossback drop", file=sys.stderr)
        return 2
    rep = analyze.analyze(items)
    queue = replies.reply_queue(items, args.replies)
    if args.personalize_replies:
        queue = replies.personalize_with_claude(queue)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(rep, queue, title=args.title, sample=args.sample, events=events), encoding="utf-8")
    write_csv(items, out.with_suffix(".csv"))
    out.with_suffix(".json").write_text(json.dumps(rep.stats, default=str, indent=2))

    verdict = brief.assess(rep, events)
    out.with_name("brief.md").write_text(brief.render_brief(rep, verdict, product=args.product, sample=args.sample), encoding="utf-8")
    out.with_name("verdict.md").write_text(brief.render_verdict(verdict), encoding="utf-8")

    s = rep.stats
    print(f"[radar] {s['negative']} negative of {s['total']}; top issues: "
          + ", ".join(f"{k} {n}" for k, n in rep.by_category[:3]), file=sys.stderr)
    print(f"[radar] {len(rep.spikes)} spike(s); report -> {out}", file=sys.stderr)
    print(f"[radar] go/no-go: {verdict.decision} -> {out.with_name('verdict.md')}; pitch draft -> {out.with_name('brief.md')}", file=sys.stderr)
    return 0


def _eval(args) -> int:
    gold = labels.load_labels(args.labels)
    items = _load_and_classify(args)
    if items is None:
        return 1
    res = labels.evaluate(items, gold)
    name = "Claude classifier" if args.claude else "keyword rules"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(labels.render_eval(res, name), encoding="utf-8")
    print(f"[radar] {name}: accuracy {res.accuracy:.0%} on {res.n} labelled items -> {out}", file=sys.stderr)
    return 0


def _label(args) -> int:
    items = _load_and_classify(args)
    if items is None:
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(labels.render_label_page(items), encoding="utf-8")
    print(f"[radar] labelling page -> {out} (open it in a browser, then use 'Download labels.csv')", file=sys.stderr)
    return 0


def _watch(args) -> int:
    items = _load_and_classify(args)
    if items is None:
        return 1
    url = os.environ.get(args.webhook_env)
    if not url and not args.dry_run:
        print(f"[radar] {args.webhook_env} is not set; printing alerts instead (use --dry-run to silence this).", file=sys.stderr)
    sent = watch.run(analyze.analyze(items), Path(args.state), url, dry_run=args.dry_run)
    print(f"[radar] {len(sent)} new alert(s)" + ("" if url and not args.dry_run else " (not posted)"), file=sys.stderr)
    return 0


def _fetch(argv: list[str]) -> int:
    try:
        from .connectors import fetch_main
    except ImportError:
        print("[radar] connectors are not installed in this build.", file=sys.stderr)
        return 2
    return fetch_main(argv)


def write_csv(items: list[Complaint], path: Path) -> None:
    """Row-level export so the classification can be checked and corrected by hand.
    The id and category columns double as a labels file for `--labels` / `eval`."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "promos", "mentions_bot_support", "source", "date", "rating", "labels", "severity",
                    "needs_human", "company_replied", "classified_by", "summary", "title", "text"])
        for c in items:
            w.writerow([c.id, c.category, "|".join(c.promos), c.mentions_bot_support, c.source, c.date or "", c.rating or "",
                        "|".join(c.labels), c.severity, c.needs_human,
                        "" if c.company_replied is None else c.company_replied, c.classified_by, c.summary, c.title, c.text])
