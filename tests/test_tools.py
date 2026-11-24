"""Tests for labels/eval, the labelling page, watch alerts, parser variants and the CLI."""

import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from radar import analyze, classify, cli, ingest, labels, watch
from radar.models import Complaint

TODAY = date(2026, 11, 22)


def _c(i, text, d=date(2026, 11, 1), rating=1):
    return Complaint(id=f"t:{i}", source="t", text=text, date=d, rating=rating, company_replied=False)


# ------------------------------------------------------------------ labels / eval

def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_labels_override_and_validate(tmp_path):
    items = classify.classify_rules([_c(1, "my withdrawal is pending for days"), _c(2, "site is down")])
    f = _write(tmp_path, "l.csv", "id,category,promos,mentions_bot_support\nt:1,kyc,,true\n")
    assert labels.apply_labels(items, labels.load_labels(f)) == 1
    assert (items[0].category, items[0].classified_by, items[0].mentions_bot_support) == ("kyc", "human", True)
    assert items[0].labels[0] == "kyc" and "withdrawal" in items[0].labels
    assert items[1].classified_by == "rules"

    bad = _write(tmp_path, "bad.csv", "id,category\nt:1,nonsense\n")
    with pytest.raises(labels.LabelError, match="unknown category"):
        labels.load_labels(bad)
    bad_promo = _write(tmp_path, "bp.csv", "id,category,promos\nt:1,kyc,Mystery promo\n")
    with pytest.raises(labels.LabelError, match="unknown promo"):
        labels.load_labels(bad_promo)


def test_evaluate_scores_and_lists_mistakes():
    items = classify.classify_rules([
        _c(1, "withdrawal pending for days"),          # rules: withdrawal
        _c(2, "lossback never credited"),              # rules: promo_not_credited
        _c(3, "this place is a joke"),                 # rules: other
    ])
    gold = {"t:1": labels.Label("withdrawal"), "t:2": labels.Label("promo_not_credited"),
            "t:3": labels.Label("fairness"), "t:missing": labels.Label("other")}
    res = labels.evaluate(items, gold)
    assert (res.n, res.correct, res.missing) == (3, 2, 1)
    assert res.per_category["fairness"]["recall"] == 0.0
    assert res.per_category["withdrawal"]["precision"] == 1.0
    md = labels.render_eval(res)
    assert "67%" in md and "Game fairness" in md and "t:3" in md


def test_label_page_escapes_and_lists_only_negatives():
    items = classify.classify_rules([_c(1, "</script><script>alert(1)</script> lossback never credited"),
                                     _c(2, "great site", rating=5)])
    page = labels.render_label_page(items)
    assert "</script><script>alert(1)" not in page  # JSON is escaped for <script> context
    assert "t:1" in page and "t:2" not in page


# ----------------------------------------------------------------------- watch

def _spiky_items():
    items = [_c(f"b{w}", "withdrawal pending for days", d=date(2026, 9, 7) + timedelta(weeks=w)) for w in range(6)]
    items += [_c(f"s{n}", f"my lossback was never credited #{n}", d=date(2026, 10, 19)) for n in range(6)]
    return classify.classify_rules(items)


def test_watch_alerts_once_and_only_for_recent_spikes(tmp_path):
    rep = analyze.analyze(_spiky_items())
    state = tmp_path / "state.json"
    assert watch.pending_alerts(rep, {}, as_of=date(2026, 12, 30)) == []  # too old
    alerts = watch.pending_alerts(rep, {}, as_of=date(2026, 10, 21))
    assert len(alerts) == 1 and "Promo / bonus not credited" in alerts[0].text and "Lossback (6)" in alerts[0].text
    assert alerts[0].text.count('> "') == 2  # two distinct quotes, not six copies

    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/hook"
    try:
        sent = watch.run(rep, state, url, as_of=date(2026, 10, 21))
        again = watch.run(rep, state, url, as_of=date(2026, 10, 21))
    finally:
        srv.shutdown()
    assert len(sent) == 1 and again == []  # remembered in state, not re-sent
    assert received and "text" in received[0]
    assert json.loads(state.read_text())["sent"]


def test_webhook_payload_shape():
    assert "content" in watch.payload_for("https://discord.com/api/webhooks/1/x", "hi")
    assert "text" in watch.payload_for("https://hooks.slack.com/services/x", "hi")
    assert len(watch.payload_for("https://discord.com/api/webhooks/1/x", "a" * 5000)["content"]) <= 2000


# ------------------------------------------------------------------ parser variants

@pytest.mark.parametrize("rating_line,expected", [
    ("Rated 2 out of 5 stars", 2),
    ("2 out of 5 stars", 2),
    ("Rating: 2/5", 2),
    ("★★☆☆☆", 2),
])
def test_paste_rating_variants(rating_line, expected):
    raw = f"Jo K.\nUS\n2 reviews\n{rating_line}\nUpdated Nov 12, 2026\nTitle\nBody text here.\nUseful\nShare"
    (c,) = ingest.parse_trustpilot_paste(raw, TODAY)
    assert (c.rating, c.date, c.title, c.text) == (expected, date(2026, 11, 12), "Title", "Body text here.")


def test_paste_drops_next_reviewers_initials_without_useful_line():
    raw = "Rated 1 out of 5 stars\nT1\nBody one.\nAB\nRated 1 out of 5 stars\nT2\nBody two."
    a, b = ingest.parse_trustpilot_paste(raw, TODAY)
    assert a.text == "Body one." and b.text == "Body two."


def test_parse_date_edited_suffix():
    assert ingest.parse_date("Nov 12, 2026 · Edited") == date(2026, 11, 12)
    assert ingest.parse_date("Updated 3 days ago", TODAY) == date(2026, 11, 19)


# ------------------------------------------------------------------------- CLI

def test_cli_end_to_end(tmp_path):
    data = tmp_path / "reviews.txt"
    data.write_text(
        "\n\n".join(
            f"Pat {i}\nUS\n1 review\nRated 1 out of 5 stars\nNov {i + 1}, 2026\nLossback missing\n"
            f"My lossback was never credited, case {i}.\nUseful\nShare"
            for i in range(5)
        )
    )
    out = tmp_path / "out" / "report.html"
    assert cli.main([str(data), "-o", str(out), "--event", "2026-11-03:Drop"]) == 0  # no subcommand = report
    for name in ("report.html", "report.csv", "report.json", "brief.md", "verdict.md"):
        assert (out.parent / name).exists(), name
    assert "Drop: before vs after" in out.read_text()

    # The row-level CSV doubles as a labels file.
    assert cli.main(["eval", str(data), str(out.with_suffix(".csv")), "-o", str(tmp_path / "eval.md")]) == 0
    assert "100%" in (tmp_path / "eval.md").read_text()

    assert cli.main(["label", str(data), "-o", str(tmp_path / "label.html")]) == 0
    assert cli.main([str(data), "-o", str(out), "--event", "not-a-date"]) == 2


def test_cli_empty_input_returns_error(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("nothing useful here")
    assert cli.main(["report", str(empty), "-o", str(tmp_path / "r.html")]) == 1
