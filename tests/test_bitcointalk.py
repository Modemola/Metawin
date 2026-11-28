"""Bitcointalk connector: parsing real SMF markup patterns, no network."""

import csv
from datetime import date

from radar import ingest
from radar.connectors import bitcointalk as bt
from radar.connectors import fetch_main


def _post(msg, author, when, body, op=False, cls="td_headerandpost"):
    op_mark = '<span title="This member is the topic-starter" style="cursor: help;">(OP)</span>' if op else ""
    return f"""
<tr><td valign="top" width="16%" rowspan="2" class="poster_info">
<b><a href="https://bitcointalk.org/index.php?action=profile;u=1" title="View the profile of {author}">{author}</a></b> {op_mark}
<div class="smalltext">Member<br /></div></td>
<td valign="top" width="85%" class="{cls}"><table><tr><td valign="middle">
<div style="font-weight: bold;" class="subject" id="subject_{msg}"><a href="https://bitcointalk.org/index.php?topic=99.msg{msg}#msg{msg}">Withdrawal stuck</a></div>
<div class="smalltext">{when}</div></td></tr></table>
<hr /><div class="post">{body}</div> </td> </tr> <tr> <td valign="bottom" class="smalltext" width="85%"></td></tr>
"""


PAGE = (
    "<html><head><title>MetaWin withdrawal stuck</title></head><body>"
    + _post("101", "Alice Player", '<span class="edited">March 30, 2026, 06:31:20 AM</span>',
            "My withdrawal of 900 USDT is pending for 5 days.<br />Call me +44 7700 900123.", op=True)
    + _post("102", "Bob", "Today at 09:00:00 AM",
            '<div class="quoteheader"><a href="#">Quote from: Alice</a></div><div class="quote">'
            '<div class="quoteheader">Quote from: X</div><div class="quote">nested old text</div>'
            "My withdrawal of 900 USDT is pending</div>Same here, support never answers.",
            cls="td_headerandpost with_column_bug")
    + '<a class="navPages" href="https://bitcointalk.org/index.php?topic=99.20">2</a>'
    + "</body></html>"
)


def test_parse_page_handles_header_variants_quotes_dates_and_privacy():
    posts = bt.parse_page(PAGE, "99", today=date(2026, 11, 28))
    assert [p.msg_id for p in posts] == ["101", "102"]
    a, b = posts
    assert a.is_op and not b.is_op and a.posted == date(2026, 3, 30) and b.posted == date(2026, 11, 28)
    assert "[phone]" in a.text and "7700" not in a.text
    assert b.text == "Same here, support never answers."  # both quote levels removed
    assert a.author_hash and "Alice" not in a.author_hash and a.author_hash != b.author_hash
    assert bt.last_offset(PAGE, "99") == 20


def test_fetch_topic_pages_politely_and_warns_on_parse_gaps(monkeypatch):
    waits, logs, requested = [], [], []
    monkeypatch.setattr(bt, "sleep", waits.append)
    broken = PAGE.replace('<div class="post">', '<div class="postbody">')  # layout drift on page 2

    def fake(topic, offset, base):
        requested.append(offset)
        return PAGE if offset == 0 else broken

    posts = list(bt.fetch_topic("99", delay=3.0, fetch=fake, progress=logs.append))
    assert requested == [0, 20] and waits == [3.0]
    assert len(posts) == 2  # page 2 parsed nothing
    assert any("WARNING" in m and "parsed 0 of 2" in m for m in logs)


def test_cli_writes_csv_that_ingest_reads_without_names(monkeypatch, tmp_path):
    monkeypatch.setattr(bt, "fetch_page", lambda t, o, b=bt.BASE_URL, timeout=30: PAGE if o == 0 else "")
    monkeypatch.setattr(bt, "sleep", lambda s: None)
    out = tmp_path / "bt.csv"
    assert fetch_main(["bitcointalk", "--topic", "99", "--complaints-only", "--out", str(out)]) == 0
    raw = out.read_text()
    assert "Alice" not in raw and "Bob" not in raw
    rows = list(csv.DictReader(out.open()))
    assert {r["id"] for r in rows} == {"bitcointalk:101", "bitcointalk:102"}
    items = ingest.load(out)
    assert all(c.source == "bitcointalk" and c.rating is None for c in items)
    assert items[0].title == "Withdrawal stuck" and items[1].title == ""  # subject kept only for the opening post


def test_forum_posts_count_only_when_they_name_a_problem():
    from radar import classify
    from radar.models import Complaint

    chat = Complaint(id="b:1", source="bitcointalk", text="Glad everything is back to normal, nice project")
    moan = Complaint(id="b:2", source="bitcointalk", text="my withdrawal has been pending for 5 days")
    ticket = Complaint(id="i:1", source="intercom", text="hello there")
    classify.classify_rules([chat, moan, ticket])
    assert (chat.is_negative, moan.is_negative, ticket.is_negative) == (False, True, True)
