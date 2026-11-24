"""Render a Report as one self-contained HTML file (no external requests).

All review text is untrusted input and is HTML-escaped everywhere.
"""

from __future__ import annotations

from datetime import date
from html import escape

from .analyze import EventWindow, Report, distinct_texts, week_start
from .models import Complaint
from .taxonomy import category_label

# Chart sizes (SVG user units; the SVGs scale to their container).
BAR_W, BAR_ROW, BAR_LABEL_W = 640, 30, 230
LINE_W, LINE_H, PAD = 720, 220, 36


def _e(s) -> str:
    return escape(str(s), quote=True)


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def _fmt_date(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "undated"


# ------------------------------------------------------------------------- charts

def _bar_chart(rows: list[tuple[str, int]], total: int) -> str:
    """Horizontal bars, one series, sorted descending."""
    if not rows:
        return "<p class=muted>No data.</p>"
    top = max(n for _, n in rows)
    plot_w = BAR_W - BAR_LABEL_W - 60
    h = len(rows) * BAR_ROW + 8
    out = [f'<div class="chartwrap"><svg class="chart" viewBox="0 0 {BAR_W} {h}" role="img" aria-label="Complaints by category">']
    for i, (key, n) in enumerate(rows):
        y = i * BAR_ROW + 4
        w = max(6, plot_w * n / top)
        share = n / total if total else 0
        tip = f"{category_label(key)}: {n} complaints ({share:.0%} of negative)"
        out.append(
            f'<g class="hit" data-tip="{_e(tip)}">'
            f'<rect x="0" y="{y - 2}" width="{BAR_W}" height="{BAR_ROW}" fill="transparent"/>'
            f'<text x="{BAR_LABEL_W - 10}" y="{y + 15}" text-anchor="end" class="axis-label">{_e(category_label(key))}</text>'
            f'<path class="bar" d="M{BAR_LABEL_W},{y + 4} h{w - 4} a4,4 0 0 1 4,4 v10 a4,4 0 0 1 -4,4 h-{w - 4} z"/>'
            f'<text x="{BAR_LABEL_W + w + 6}" y="{y + 15}" class="value-label">{n}</text></g>'
        )
    out.append(f'<line x1="{BAR_LABEL_W}" x2="{BAR_LABEL_W}" y1="0" y2="{h}" class="baseline"/></svg></div>')
    return "".join(out)


def _line_chart(r: Report, events: list[EventWindow] | None = None) -> str:
    """Weekly negative-review count with spike weeks marked."""
    weeks = r.weeks
    if len(weeks) < 2:
        return "<p class=muted>Need at least two weeks of dated reviews for a trend.</p>"
    vals = [r.weekly_total[w] for w in weeks]
    top = max(max(vals), 1)
    nice = next(n for n in (4, 8, 12, 20, 40, 60, 80, 100, 200, 400, 1000, 2000, 4000, 10**9) if n >= top)
    pw, ph = LINE_W - PAD - 12, LINE_H - PAD - 12
    x = lambda i: PAD + pw * i / (len(weeks) - 1)
    y = lambda v: 12 + ph * (1 - v / nice)
    spike_weeks = {s.week for s in r.spikes}

    out = [f'<div class="chartwrap"><svg class="chart" viewBox="0 0 {LINE_W} {LINE_H}" role="img" aria-label="Negative reviews per week">']
    for t in range(0, 5):
        v = nice * t / 4
        out.append(f'<line x1="{PAD}" x2="{LINE_W - 12}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{"baseline" if t == 0 else "grid"}"/>')
        out.append(f'<text x="{PAD - 6}" y="{y(v) + 4:.1f}" text-anchor="end" class="axis-label">{v:g}</text>')
    step = max(1, len(weeks) // 6)
    for i in range(0, len(weeks), step):
        anchor = "end" if x(i) > LINE_W - 40 else "middle"
        out.append(f'<text x="{x(i):.1f}" y="{LINE_H - 8}" text-anchor="{anchor}" class="axis-label">{weeks[i].strftime("%d %b")}</text>')
    for ev in events or []:
        wk = week_start(ev.day)
        if wk in weeks:
            i = weeks.index(wk) + ev.day.weekday() / 7
            out.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="12" y2="{12 + ph}" class="event-line"/>'
                       f'<text x="{x(i) + 4:.1f}" y="22" class="axis-label">{_e(ev.name)}</text>')
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    out.append(f'<polyline points="{pts}" class="line"/>')
    colw = pw / (len(weeks) - 1)
    for i, (w, v) in enumerate(zip(weeks, vals, strict=True)):
        spiking = [s for s in r.spikes if s.week == w]
        tip = f"Week of {w.strftime('%d %b %Y')}: {v} negative reviews"
        if spiking:
            tip += " — spike: " + ", ".join(f"{s.label} ({s.count})" for s in spiking)
        out.append(
            f'<g class="hit" data-tip="{_e(tip)}"><rect x="{x(i) - colw / 2:.1f}" y="0" width="{colw:.1f}" height="{LINE_H}" fill="transparent"/>'
            f'<line class="crosshair" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="12" y2="{12 + ph}"/>'
            + (f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="5" class="spike-dot"/>' if w in spike_weeks else f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" class="dot"/>')
            + "</g>"
        )
    out.append("</svg></div>")
    return "".join(out)


def _heatmap(r: Report, cats: list[str]) -> str:
    """Category x week counts, one sequential hue."""
    weeks = r.weeks
    if not weeks or not cats:
        return ""
    top = max((r.weekly_by_category[c][w] for c in cats for w in weeks), default=0) or 1
    steps = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"]
    cell_w = max(10, min(28, (LINE_W - BAR_LABEL_W) / len(weeks)))
    cell_h = 24
    w_total = BAR_LABEL_W + cell_w * len(weeks) + 4
    h_total = cell_h * len(cats) + 26
    out = [f'<div class="chartwrap"><svg class="chart" viewBox="0 0 {w_total:.0f} {h_total}" role="img" aria-label="Complaints by category and week">']
    for ci, cat in enumerate(cats):
        yy = ci * cell_h
        out.append(f'<text x="{BAR_LABEL_W - 10}" y="{yy + 16}" text-anchor="end" class="axis-label">{_e(category_label(cat))}</text>')
        for wi, w in enumerate(weeks):
            v = r.weekly_by_category[cat][w]
            fill = steps[0] if v == 0 else steps[min(5, 1 + int(4 * v / top))]
            tip = f"{category_label(cat)} — week of {w.strftime('%d %b')}: {v}"
            out.append(
                f'<rect class="hit cell" data-tip="{_e(tip)}" x="{BAR_LABEL_W + wi * cell_w:.1f}" y="{yy + 1}" '
                f'width="{cell_w - 2:.1f}" height="{cell_h - 2}" rx="3" fill="{fill}"/>'
            )
    step = max(1, len(weeks) // 6)
    for wi in range(0, len(weeks), step):
        anchor = "end" if wi > len(weeks) - 3 else "start"
        xx = BAR_LABEL_W + (wi + 1) * cell_w - 2 if anchor == "end" else BAR_LABEL_W + wi * cell_w
        out.append(f'<text x="{xx:.1f}" y="{h_total - 6}" text-anchor="{anchor}" class="axis-label">{weeks[wi].strftime("%d %b")}</text>')
    out.append("</svg></div>")
    legend = "".join(f'<span class="sw" style="background:{s}"></span>' for s in steps)
    return "".join(out) + f'<div class="legend"><span>0</span>{legend}<span>{top}+ per week</span></div>'


# -------------------------------------------------------------------- page pieces

def _tile(value: str, label: str, note: str = "") -> str:
    return f'<div class="tile"><div class="tile-v">{_e(value)}</div><div class="tile-l">{_e(label)}</div>' + (f'<div class="tile-n">{_e(note)}</div>' if note else "") + "</div>"


def _quote(c: Complaint) -> str:
    stars = f"{c.rating}★" if c.rating else c.source
    text = c.text if len(c.text) <= 420 else c.text[:419].rstrip() + "…"
    tags = "".join(f'<span class="tag">{_e(p)}</span>' for p in c.promos)
    if c.mentions_bot_support:
        tags += '<span class="tag">mentions bot support</span>'
    if c.needs_human:
        tags += '<span class="tag tag-warn">needs a person</span>'
    title = f"<b>{_e(c.title)}</b> " if c.title else ""
    return (
        f'<blockquote><div class="q-meta">{_e(stars)} · {_e(_fmt_date(c.date))} · {_e(c.source)}'
        f'{" · severity " + str(c.severity) if c.severity > 1 else ""}</div>'
        f"<p>{title}{_e(text)}</p><div>{tags}</div></blockquote>"
    )


def _event_section(ev: EventWindow) -> str:
    cats = sorted(set(ev.before) | set(ev.after), key=lambda k: -(ev.after.get(k, 0) - ev.before.get(k, 0)))
    rows = [[category_label(k), ev.before.get(k, 0), ev.after.get(k, 0), f"{ev.after.get(k, 0) - ev.before.get(k, 0):+d}"] for k in cats]
    rows.append(["All negative reviews", ev.total_before, ev.total_after, f"{ev.total_after - ev.total_before:+d}"])
    promos = ", ".join(f"{_e(p)} ({n})" for p, n in ev.promos_after.most_common(4)) or "none named"
    quotes = "".join(_quote(c) for c in distinct_texts(ev.after_items, 2))
    return (f'<section><h2>{_e(ev.name)}: before vs after</h2>'
            f'<p class="muted">Negative reviews in the {ev.days} days before {ev.day:%d %b %Y} vs the {ev.days} days from that date. '
            f'Promotions named afterwards: {promos}.</p>'
            + _table(["Category", "Before", "After", "Change"], rows) + quotes + "</section>")


def _table(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in row) + "</tr>" for row in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def render(r: Report, replies: list[tuple[Complaint, str]], title: str = "Complaint Radar", sample: bool = False,
           events: list[EventWindow] | None = None) -> str:
    s = r.stats
    neg = s["negative"] or 1
    cats = [k for k, _ in r.by_category]
    top_cat = cats[0] if cats else None

    tiles = "".join([
        _tile(str(s["negative"]), "negative reviews analysed", f"of {s['total']} total · avg rating {s['avg_rating'] or '–'}"),
        _tile(_pct(s["money_related"] / neg), "involve money or account access", "deposits, withdrawals, promos, closures"),
        _tile(_pct(s["promo_related"] / neg), "mention a specific promotion"),
        _tile(_pct(s["reply_rate"]), "publicly answered by MetaWin", f"{s['unreplied_severe']} money-related reviews unanswered" if s["reply_known"] else "source has no reply data"),
        _tile(str(s["bot_mentions"]), "complain support is a bot / no human", f"of {s['support_related']} support-related"),
    ])

    spike_rows = "".join(
        f'<li><span class="status warn" aria-hidden="true">▲</span> <b>Spike</b> · week of {_e(sp.week.strftime("%d %b %Y"))}: '
        f'<b>{_e(sp.label)}</b> — {sp.count} reviews vs ~{sp.baseline:g}/week before'
        + (" · linked promos: " + ", ".join(f"{_e(p)} ({n})" for p, n in sp.promos) if sp.promos else "")
        + "</li>"
        for sp in r.spikes[:12]
    ) or "<li class=muted>No week stood out from its baseline. Spikes need at least 4 reviews in a category in one week and 2.5× the previous 4-week average.</li>"

    promo_rows = []
    for p, n in r.promo_totals:
        cc = r.promo_by_category[p].most_common(1)[0]
        promo_rows.append([p, n, f"{n / neg:.0%}", category_label(cc[0]), cc[1]])

    sections = []
    for k in cats[:6]:
        ex = "".join(_quote(c) for c in r.examples(k))
        sections.append(f'<details><summary><b>{_e(category_label(k))}</b> <span class="muted">({dict(r.by_category)[k]})</span></summary>{ex}</details>')

    def _reply(c: Complaint, d: str) -> str:
        return (f'<div class="reply">{_quote(c)}<label>Draft reply (edit before posting)</label>'
                f'<textarea rows="4">{_e(d)}</textarea><button class="copy" type="button">Copy</button></div>')

    reply_html = "".join(_reply(c, d) for c, d in replies[:3])
    if len(replies) > 3:
        reply_html += f'<details><summary>{len(replies) - 3} more drafts</summary>{"".join(_reply(c, d) for c, d in replies[3:])}</details>'
    reply_html = reply_html or "<p class=muted>No unanswered negative reviews with reply data in this dataset.</p>"

    event_html = "".join(_event_section(ev) for ev in events or [])
    weekly_rows = [[w.isoformat(), r.weekly_total[w]] + [r.weekly_by_category[c][w] for c in cats[:6]] for w in r.weeks]
    banner = '<div class="banner">SAMPLE DATA — every review in this report is synthetic, generated to test the layout. None of it is real MetaWin feedback.</div>' if sample else ""
    sources = ", ".join(f"{k} ({v})" for k, v in s["sources"].items())
    classified = ", ".join(f"{k} ({v})" for k, v in s["classified_by"].items())

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>{CSS}</style></head>
<body><main>
{banner}
<header><h1>{_e(title)}</h1>
<p class="muted">{_e(_fmt_date(s["date_from"]))} – {_e(_fmt_date(s["date_to"]))} · sources: {_e(sources)} · classified by: {_e(classified)}{" · " + str(s["undated"]) + " undated" if s["undated"] else ""}</p></header>

<section class="tiles">{tiles}</section>

<section><h2>What players complain about</h2>
<p class="muted">Primary reason per negative review (1–2★, or any support ticket).{(" Top issue: <b>" + _e(category_label(top_cat)) + "</b>.") if top_cat else ""}</p>
{_bar_chart(r.by_category, neg)}
<details class="tv"><summary>Table view</summary>{_table(["Category", "Reviews", "Share"], [[category_label(k), n, f"{n / neg:.0%}"] for k, n in r.by_category])}</details>
</section>

<section><h2>Early warning: spikes</h2><ul class="spikes">{spike_rows}</ul></section>

{event_html}
<section><h2>Negative reviews per week</h2>
<p class="muted">Larger dots mark weeks where a category spiked.</p>
{_line_chart(r, events)}
<h3>By category and week</h3>
{_heatmap(r, cats[:6])}
<details class="tv"><summary>Table view</summary>{_table(["Week", "All"] + [category_label(c) for c in cats[:6]], weekly_rows)}</details>
</section>

<section><h2>Which promotions generate complaints</h2>
{_table(["Promotion", "Complaints", "Share of negative", "Most common issue", "Count"], promo_rows) if promo_rows else "<p class=muted>No promotion names detected.</p>"}
</section>

<section><h2>What players actually say</h2>{"".join(sections)}</section>

<section><h2>Reply queue</h2>
<p class="muted">Unanswered negative reviews, most serious first, with a draft that never confirms account details or promises money. A person reviews every reply before it's posted.</p>
{reply_html}</section>

<footer class="muted">Reviewer names are never stored (hashed only). Generated by Complaint Radar.</footer>
</main>
<div id="tip" role="tooltip" hidden></div>
<script>{JS}</script>
</body></html>"""


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--base:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--warn:#fab219;--crit:#d03b3b;--seq-0:#f0efec;--seq-1:#cde2fb;--seq-2:#9ec5f4;--seq-3:#5598e7;--seq-4:#256abf;--seq-5:#104281}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--seq-0:#262624;--seq-1:#104281;--seq-2:#1c5cab;--seq-3:#2a78d6;--seq-4:#5598e7;--seq-5:#9ec5f4}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--seq-0:#262624;--seq-1:#104281;--seq-2:#1c5cab;--seq-3:#2a78d6;--seq-4:#5598e7;--seq-5:#9ec5f4}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:0 0 6px}h3{font-size:15px;margin:20px 0 6px}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px;margin:16px 0}
.muted{color:var(--ink2)}.banner{background:var(--warn);color:#0b0b0b;font-weight:600;padding:10px 14px;border-radius:8px;margin-bottom:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;background:none;border:0;padding:0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px}.tile-v{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums}
.tile-l{color:var(--ink);font-size:13px}.tile-n{color:var(--ink2);font-size:12px;margin-top:4px}
.chartwrap{overflow-x:auto}.chart{width:100%;min-width:560px;height:auto;display:block;overflow:visible}.axis-label{fill:var(--ink2);font-size:12px}.value-label{fill:var(--ink);font-size:12px;font-variant-numeric:tabular-nums}
.bar{fill:var(--s1)}.grid{stroke:var(--grid);stroke-width:1}.baseline{stroke:var(--base);stroke-width:1}.line{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round}
.event-line{stroke:var(--ink2);stroke-width:1.5;stroke-dasharray:4 4}.dot{fill:var(--s1);stroke:var(--surface);stroke-width:2}.spike-dot{fill:var(--crit);stroke:var(--surface);stroke-width:2}
.crosshair{stroke:var(--base);stroke-width:1;opacity:0}.hit:hover .crosshair{opacity:1}.hit:hover .bar{opacity:.85}.cell:hover{stroke:var(--ink);stroke-width:1.5}
.legend{display:flex;align-items:center;gap:3px;font-size:12px;color:var(--ink2);margin-top:6px;flex-wrap:wrap}.legend .sw{width:22px;height:12px;border-radius:3px;display:inline-block}
.spikes{list-style:none;padding:0;margin:0}.spikes li{padding:8px 0;border-top:1px solid var(--border)}.spikes li:first-child{border-top:0}.status.warn{color:#b07a00}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}th{color:var(--ink2);font-weight:600}
details{margin:8px 0}summary{cursor:pointer;padding:4px 0}.tv summary{color:var(--ink2);font-size:13px}
blockquote{margin:10px 0;padding:10px 12px;border-left:3px solid var(--base);background:var(--page);border-radius:0 8px 8px 0}blockquote p{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.q-meta{font-size:12px;color:var(--ink2)}.tag{display:inline-block;font-size:11px;padding:1px 8px;margin:2px 4px 0 0;border:1px solid var(--border);border-radius:99px;color:var(--ink2)}
.tag-warn{border-color:var(--crit);color:var(--crit)}.reply{border-top:1px solid var(--border);padding-top:8px;margin-top:12px}.reply label{font-size:12px;color:var(--ink2)}
textarea{width:100%;font:inherit;font-size:14px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--page);color:var(--ink)}
button.copy{margin-top:6px;font:inherit;font-size:13px;padding:4px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--ink);cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);font-size:12px;padding:6px 9px;border-radius:6px;max-width:300px;z-index:10}
footer{font-size:12px;margin-top:24px}
@media (max-width:600px){.tile-v{font-size:24px}section{padding:14px}}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('.hit').forEach(el=>{
  el.addEventListener('mousemove',e=>{tip.textContent=el.dataset.tip;tip.hidden=false;
    const x=Math.min(e.clientX+12,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px';});
  el.addEventListener('mouseleave',()=>{tip.hidden=true;});
});
document.querySelectorAll('button.copy').forEach(b=>b.addEventListener('click',async()=>{
  const t=b.previousElementSibling.value;try{await navigator.clipboard.writeText(t);b.textContent='Copied';}catch{b.textContent='Select & copy manually';}
  setTimeout(()=>b.textContent='Copy',1500);}));
"""
