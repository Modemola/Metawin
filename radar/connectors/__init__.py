"""Pull feedback straight from a company's own Intercom or Trustpilot account.

Usage (secrets come from environment variables only, never the command line)::

    INTERCOM_TOKEN=... fetch_main(["intercom", "--since", "2026-08-01", "--out", "data/intercom.json"])
    TRUSTPILOT_API_KEY=... fetch_main(["trustpilot", "--domain", "metawin.com", "--out", "data/trustpilot.json"])

The written JSON is auto-detected by `radar.ingest.load()`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from ._http import ConnectorError

__all__ = ["ConnectorError", "fetch_main"]

TOKEN_ENV = {"intercom": "INTERCOM_TOKEN", "trustpilot": "TRUSTPILOT_API_KEY"}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="radar fetch",
        description="Download feedback from an API into a JSON file the radar can read. "
        "Credentials are read from environment variables, never from arguments.",
    )
    sub = ap.add_subparsers(dest="source", required=True)

    ic = sub.add_parser("intercom", help="Intercom conversations (needs INTERCOM_TOKEN)")
    ic.add_argument("--since", required=True, type=date.fromisoformat, help="First day to include, YYYY-MM-DD (UTC)")
    ic.add_argument("--until", type=date.fromisoformat, help="Last day to include, YYYY-MM-DD (UTC); default: today")
    ic.add_argument("--max", type=int, default=5000, help="Maximum conversations (default 5000)")
    ic.add_argument("--out", default="data/intercom.json", help="Output JSON path")

    tp = sub.add_parser("trustpilot", help="Trustpilot reviews (needs TRUSTPILOT_API_KEY)")
    target = tp.add_mutually_exclusive_group(required=True)
    target.add_argument("--domain", help="Business domain, e.g. metawin.com")
    target.add_argument("--business-unit-id", help="Trustpilot business unit id (skips the domain lookup)")
    tp.add_argument("--max", type=int, default=5000, help="Maximum reviews (default 5000)")
    tp.add_argument("--out", default="data/trustpilot.json", help="Output JSON path")
    return ap


def _progress(msg: str) -> None:
    print(f"[radar fetch] {msg}", file=sys.stderr, flush=True)


def _fetch(args: argparse.Namespace, secret: str) -> tuple[dict, int]:
    """Run the chosen connector; return (JSON payload, item count)."""
    if args.source == "intercom":
        from . import intercom

        convs = intercom.fetch_conversations(
            secret, args.since, args.until, args.max, base_url=intercom.BASE_URL, progress=_progress
        )
        return {"conversations": convs}, len(convs)
    from . import trustpilot

    reviews = trustpilot.fetch_reviews(
        secret, args.business_unit_id, args.domain, args.max, base_url=trustpilot.BASE_URL, progress=_progress
    )
    return {"reviews": reviews}, len(reviews)


def fetch_main(argv: list[str]) -> int:
    """CLI entry point: fetch from an API and write radar-readable JSON. Returns an exit code."""
    args = _parser().parse_args(argv)
    env = TOKEN_ENV[args.source]
    secret = os.environ.get(env, "").strip()
    if not secret:
        print(f"error: set the {env} environment variable (credentials are never taken as arguments)", file=sys.stderr)
        return 2
    try:
        payload, count = _fetch(args, secret)
    except ConnectorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    _progress(f"wrote {count} {args.source} items to {out}")
    return 0
