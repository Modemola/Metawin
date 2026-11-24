"""Early warning: post a Slack/Discord alert when a complaint spike appears.

Meant to run on a schedule (cron, a CI job, a cloud scheduled task) against
fresh data. It remembers which spikes it has already reported in a small state
file, so each spike is announced once.

The webhook URL is read from an environment variable (default
RADAR_WEBHOOK_URL), never from the command line: anyone holding it can post
to the channel.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .analyze import Report, Spike, distinct_texts

RECENT_WEEKS = 2  # only alert on spikes in the latest two weeks of data


@dataclass
class Alert:
    key: str
    text: str


def _spike_key(sp: Spike) -> str:
    return f"{sp.week.isoformat()}:{sp.category}"


def pending_alerts(rep: Report, state: dict, as_of: date | None = None) -> list[Alert]:
    """Spikes in the most recent weeks that haven't been announced yet."""
    latest = as_of or rep.stats["date_to"]
    if not latest:
        return []
    cutoff = latest - timedelta(weeks=RECENT_WEEKS)
    alerts = []
    for sp in sorted(rep.spikes, key=lambda s: (s.week, -s.count)):
        key = _spike_key(sp)
        if sp.week < cutoff or key in state.get("sent", {}):
            continue
        alerts.append(Alert(key, format_alert(rep, sp)))
    return alerts


def format_alert(rep: Report, sp: Spike) -> str:
    lines = [
        f":warning: Complaint spike: {sp.label}",
        f"{sp.count} negative reviews in the week of {sp.week:%d %b %Y} (usually ~{sp.baseline:g}/week).",
    ]
    if sp.promos:
        lines.append("Promotions mentioned: " + ", ".join(f"{p} ({n})" for p, n in sp.promos))
    week = [c for c in rep.negatives if c.category == sp.category and c.date and sp.week <= c.date < sp.week + timedelta(days=7)]
    week.sort(key=lambda c: -c.severity)
    for c in distinct_texts(week, 2):
        text = " ".join(c.full_text.split())
        lines.append(f'> "{text[:180]}{"…" if len(text) > 180 else ""}"')
    return "\n".join(lines)


def payload_for(url: str, text: str) -> dict:
    """Discord webhooks take {"content"} (max 2000 chars); Slack takes {"text"}."""
    host = urlparse(url).hostname or ""
    if host.endswith("discord.com") or host.endswith("discordapp.com"):
        return {"content": text[:1990]}
    return {"text": text}


def post(url: str, text: str, retries: int = 3) -> None:
    body = json.dumps(payload_for(url, text)).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
                return
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500
            if not retryable or attempt == retries:
                raise
            wait = float(e.headers.get("Retry-After") or 2 ** attempt)
            time.sleep(min(wait, 30))
        except urllib.error.URLError:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def run(rep: Report, state_path: Path, webhook_url: str | None, dry_run: bool = False,
        as_of: date | None = None) -> list[Alert]:
    """Send pending alerts; record each one in the state file only once it's delivered."""
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.setdefault("sent", {})
    alerts = pending_alerts(rep, state, as_of)
    for a in alerts:
        if dry_run or not webhook_url:
            print(a.text + "\n")
            continue
        post(webhook_url, a.text)
        state["sent"][a.key] = date.today().isoformat()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
    return alerts
