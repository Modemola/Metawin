import json
from datetime import date

from radar import analyze, classify, ingest, replies, report
from radar.models import Complaint
from radar.taxonomy import match_categories, match_promos, mentions_bot_support

TODAY = date(2026, 11, 22)

PASTE = """Alex B.
US
3 reviews
Rated 1 out of 5 stars
Nov 12, 2026
Lossback never paid
Deposited $250 for the 100% lossback, lost it.
Never got the lossback credited.
Date of experience: November 10, 2026
Useful
Share

Sam K.
GB
1 review
Rated 5 out of 5 stars
2 days ago
Fast
Withdrew in minutes.
Useful
Share
Reply from MetaWin
Thanks Sam!
"""


def test_trustpilot_paste_parses_each_review_without_leaking_names():
    items = ingest.parse_trustpilot_paste(PASTE, TODAY)
    assert len(items) == 2
    a, b = items
    assert (a.rating, a.title, a.date) == (1, "Lossback never paid", date(2026, 11, 12))
    assert "Sam" not in a.text and "GB" not in a.text  # next reviewer's header dropped
    assert a.company_replied is False and b.company_replied is True
    assert b.date == date(2026, 11, 20)  # "2 days ago"
    assert a.author_hash and "Alex" not in a.author_hash


def test_paste_without_header_markers_keeps_body_text():
    raw = "Rated 2 out of 5 stars\nTitle one\nBody line that ends the review.\nRated 1 out of 5 stars\nTitle two\nBody two"
    items = ingest.parse_trustpilot_paste(raw, TODAY)
    assert [c.text for c in items] == ["Body line that ends the review.", "Body two"]


def test_trustpilot_and_intercom_json(tmp_path):
    tp = tmp_path / "tp.json"
    tp.write_text(json.dumps({"reviews": [{"id": "r1", "stars": 2, "title": "t", "text": "withdrawal pending for days",
                                           "createdAt": "2026-11-01T10:00:00Z", "companyReply": None}]}))
    ic = tmp_path / "ic.json"
    ic.write_text(json.dumps({"conversations": [{"id": "9", "created_at": 1793000000,
                                                 "source": {"body": "<p>My rakeback wasn't credited</p>", "subject": ""},
                                                 "conversation_parts": {"conversation_parts": [
                                                     {"author": {"type": "admin"}, "body": "Looking into it"},
                                                     {"author": {"type": "user"}, "body": "<p>still nothing</p>"}]}}]}))
    (t,), (i,) = ingest.load(tp), ingest.load(ic)
    assert t.id == "trustpilot:r1" and t.company_replied is False and t.date == date(2026, 11, 1)
    assert i.source == "intercom" and "still nothing" in i.text and "Looking into it" not in i.text


def test_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("date,text,rating,source\n2026-10-01,Account closed for multi accounting,1,discord\n")
    (c,) = ingest.load(p)
    assert (c.source, c.rating, c.date) == ("discord", 1, date(2026, 10, 1))


def test_rules_disambiguate_promo_vs_deposit():
    assert match_categories("Deposited $250 for the lossback offer. It was never credited.")[0] == "promo_not_credited"
    # "lost it" = lost at the tables, not a lost deposit
    assert match_categories("Deposited $250 for the 100% lossback offer, lost it and never got the lossback credited.")[0] == "promo_not_credited"
    assert match_categories("Withdrawal was fast, but the fee ate 15% of it.")[0] == "fees"
    assert match_categories("Fees are fine, the problem is my deposit of $750 never landed.")[0] == "deposit_missing"
    assert match_categories("Sent 0.2 ETH, transaction confirmed, deposit never credited.")[0] == "deposit_missing"
    assert match_categories("Fixed withdrawal fee is too much on small amounts.")[0] == "fees"
    assert match_categories("My withdrawal has been pending for 3 days.")[0] == "withdrawal"


def test_promos_and_bot_detection():
    assert match_promos("Picked in the X giveaway, rank up reward missing") == ["MetaMax rank rewards", "X / username giveaway"]
    assert mentions_bot_support("Live chat is just an AI bot that loops")
    assert mentions_bot_support("impossible to reach a real person")
    assert not mentions_bot_support("support was fine and quick")


def _c(d, cat_text, rating=1):
    return Complaint(id=f"x:{d}:{cat_text}:{rating}", source="t", text=cat_text, date=d, rating=rating, company_replied=False)


def test_spike_needs_history_and_volume():
    items = []
    for wk in range(6):  # steady 1/week of withdrawals
        items.append(_c(date(2026, 9, 7) + (date(2026, 9, 14) - date(2026, 9, 7)) * wk, "withdrawal pending for days"))
    for n in range(6):  # sudden lossback burst in week 6
        items.append(_c(date(2026, 10, 19), f"lossback never credited {n}"))
    rep = analyze.analyze(classify.classify_rules(items))
    assert [(s.category, s.count) for s in rep.spikes] == [("promo_not_credited", 6)]
    assert rep.spikes[0].promos == [("Lossback", 6)]


def test_report_escapes_review_text_and_drafts_never_promise_money():
    evil = _c(date(2026, 11, 1), '<script>alert(1)</script> my lossback was never credited, $5,000')
    classify.classify_rules([evil])
    queue = replies.reply_queue([evil])
    html = report.render(analyze.analyze([evil]), queue)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    draft = queue[0][1].lower()
    assert "refund" not in draft and "we will credit" not in draft


def test_event_window_counts_before_and_after():
    items = [_c(date(2026, 10, 10), "withdrawal pending for days"),
             _c(date(2026, 10, 20), "lossback never credited a"),
             _c(date(2026, 10, 22), "lossback never credited b")]
    classify.classify_rules(items)
    ev = analyze.compare_event(items, *analyze.parse_event("2026-10-20:Lossback drop"))
    assert ev.name == "Lossback drop"
    assert ev.before == {"withdrawal": 1} and ev.after == {"promo_not_credited": 2}
    assert ev.biggest_increases(1) == [("promo_not_credited", 0, 2)]
    assert ev.promos_after["Lossback"] == 2


def test_verdict_no_go_on_thin_data_and_brief_dedupes_quotes():
    from radar import brief
    items = [_c(date(2026, 11, d), "same lossback never credited text") for d in range(1, 6)]
    rep = analyze.analyze(classify.classify_rules(items))
    v = brief.assess(rep)
    assert v.decision == "NO-GO" and not v.enough_data
    md = brief.render_brief(rep, v, sample=True)
    assert md.startswith("> **SAMPLE DATA")
    assert md.count("same lossback never credited text") == 1  # identical texts quoted once


def test_verdict_go_needs_volume_span_and_two_strong_findings():
    from radar import brief
    items = []
    for d in range(0, 120, 2):  # 60 reviews over ~4 months, lossback-heavy, unanswered
        day = date(2026, 7, 1) + __import__("datetime").timedelta(days=d)
        items.append(_c(day, f"lossback never credited, support is a bot, no real person {d}"))
    rep = analyze.analyze(classify.classify_rules(items))
    assert brief.assess(rep).decision == "GO"


def test_intercom_outbound_message_is_not_counted_as_the_complaint():
    conv = {"id": "o1", "created_at": 1793000000,
            "source": {"body": "<p>Hi! Your weekly rakeback is ready.</p>", "author": {"type": "admin"}},
            "conversation_parts": {"conversation_parts": [{"author": {"type": "user"}, "body": "it never arrived"}]}}
    silent = {"id": "o2", "created_at": 1793000000, "source": {"body": "Promo news", "author": {"type": "bot"}},
              "conversation_parts": {"conversation_parts": []}}
    (c,) = ingest.parse_intercom_json([conv, silent])
    assert c.text == "it never arrived"
