"""Human labels: correct the classifier, and measure how good it is.

A labels file is a CSV with an `id` column and a `category` column (the
correct category, using the keys in taxonomy.CATEGORIES). Optional columns:
`promos` ("|"-separated promo names) and `mentions_bot_support` (true/false).

The same file serves two purposes:

* `radar report --labels labels.csv` - the human answer overrides the
  classifier for those rows, so the numbers in a pitch reflect checked data.
* `radar eval data/ labels.csv` - compares the classifier against the human
  answers: accuracy, per-category precision/recall, and every mistake, so the
  rules (or the Claude prompt) can be improved where they actually fail.

`radar label data/` writes a page for producing the file without a spreadsheet.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from .models import Complaint
from .taxonomy import CATEGORIES, PROMOS, category_label

TRUE = {"1", "true", "yes", "y"}


@dataclass
class Label:
    category: str
    promos: list[str] | None = None  # None = not labelled, keep classifier's
    mentions_bot_support: bool | None = None


class LabelError(ValueError):
    pass


def load_labels(path: str | Path) -> dict[str, Label]:
    out: dict[str, Label] = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("id"):
                continue
            cat = row.get("category", "")
            if cat not in CATEGORIES:
                raise LabelError(f"{path}:{n}: unknown category {cat!r}; use one of {', '.join(CATEGORIES)}")
            promos = None
            if row.get("promos") is not None and "promos" in row:
                promos = [p for p in row["promos"].split("|") if p]
                unknown = [p for p in promos if p not in PROMOS]
                if unknown:
                    raise LabelError(f"{path}:{n}: unknown promo(s) {unknown}; use names from taxonomy.PROMOS")
            bot = row.get("mentions_bot_support")
            out[row["id"]] = Label(cat, promos, None if not bot else bot.lower() in TRUE)
    return out


def apply_labels(items: list[Complaint], labels: dict[str, Label]) -> int:
    """Overwrite classifier output with human labels. Returns rows changed."""
    n = 0
    for c in items:
        lab = labels.get(c.id)
        if not lab:
            continue
        c.labels = [lab.category, *[x for x in c.labels if x != lab.category]]
        c.category = lab.category
        if lab.promos is not None:
            c.promos = lab.promos
        if lab.mentions_bot_support is not None:
            c.mentions_bot_support = lab.mentions_bot_support
        c.classified_by = "human"
        n += 1
    return n


# ---------------------------------------------------------------------- evaluation

@dataclass
class EvalResult:
    n: int
    correct: int
    per_category: dict[str, dict[str, float]]  # precision, recall, f1, support
    confusion: Counter  # (gold, predicted) -> count
    mistakes: list[tuple[Complaint, str]] = field(default_factory=list)  # (item, gold)
    missing: int = 0  # labelled ids not found in the data

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


def evaluate(items: list[Complaint], labels: dict[str, Label]) -> EvalResult:
    by_id = {c.id: c for c in items}
    pairs = [(by_id[i], lab.category) for i, lab in labels.items() if i in by_id]
    confusion = Counter((gold, c.category) for c, gold in pairs)
    per = {}
    for k in CATEGORIES:
        tp = confusion[(k, k)]
        pred = sum(v for (g, p), v in confusion.items() if p == k)
        gold = sum(v for (g, p), v in confusion.items() if g == k)
        if not pred and not gold:
            continue
        prec = tp / pred if pred else 0.0
        rec = tp / gold if gold else 0.0
        per[k] = {"precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0, "support": gold}
    mistakes = [(c, g) for c, g in pairs if c.category != g]
    return EvalResult(len(pairs), len(pairs) - len(mistakes), per, confusion, mistakes, len(labels) - len(pairs))


def render_eval(res: EvalResult, name: str = "classifier") -> str:
    lines = [f"# Evaluation: {name}", "",
             f"**Accuracy: {res.accuracy:.0%}** ({res.correct}/{res.n} labelled items)"
             + (f" · {res.missing} labelled ids not found in the data" if res.missing else ""), ""]
    if res.n < 50:
        lines += [f"_Only {res.n} labelled items: treat these numbers as rough. 100+ gives a usable estimate._", ""]
    lines += ["| Category | Precision | Recall | F1 | Labelled |", "|---|---:|---:|---:|---:|"]
    for k, m in sorted(res.per_category.items(), key=lambda kv: -kv[1]["support"]):
        lines.append(f"| {category_label(k)} | {m['precision']:.0%} | {m['recall']:.0%} | {m['f1']:.0%} | {int(m['support'])} |")
    confused = [(g, p, v) for (g, p), v in res.confusion.most_common() if g != p][:8]
    if confused:
        lines += ["", "## Most common confusions", ""]
        lines += [f"- {v}× labelled **{category_label(g)}** but classified as **{category_label(p)}**" for g, p, v in confused]
    if res.mistakes:
        lines += ["", "## Mistakes", ""]
        for c, gold in res.mistakes[:40]:
            text = " ".join(c.full_text.split())[:200]
            lines.append(f"- `{c.id}` → got {c.category}, should be **{gold}**: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------- labelling page

def render_label_page(items: list[Complaint], title: str = "Label complaints") -> str:
    """A self-contained page: one row per complaint with a pre-filled category
    dropdown; 'Download labels.csv' exports the answers. Progress is kept in
    the browser so a long session can be resumed."""
    cats = [(k, v["label"]) for k, v in CATEGORIES.items()]
    rows = [{"id": c.id, "cat": c.category, "promos": c.promos, "bot": c.mentions_bot_support,
             "rating": c.rating, "date": c.date.isoformat() if c.date else "", "text": c.full_text[:1500]}
            for c in items if c.is_negative]
    # Escape <, > and & so no review text can end the <script> block or switch the
    # HTML parser into a comment/escaped mode ("<!--<script>").
    data = (json.dumps({"rows": rows, "cats": cats, "promos": list(PROMOS)})
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--border:rgba(11,11,11,.12);--accent:#2a78d6;--done:#006300}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--border:rgba(255,255,255,.12);--accent:#3987e5;--done:#0ca30c}}}}
:root[data-theme="dark"]{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--border:rgba(255,255,255,.12);--accent:#3987e5;--done:#0ca30c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:20px 16px 80px}}h1{{font-size:22px;margin:0 0 4px}}.muted{{color:var(--ink2)}}
.bar{{position:sticky;top:0;background:var(--page);padding:10px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--border);z-index:2}}
button{{font:inherit;padding:6px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--ink);cursor:pointer}}
button.primary{{background:var(--accent);color:#fff;border-color:transparent}}
.item{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px;margin:10px 0}}
.item.checked{{border-left:4px solid var(--done)}}.meta{{font-size:12px;color:var(--ink2)}}
.text{{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}}select{{font:inherit;padding:4px;max-width:100%}}
.row{{display:flex;gap:12px;flex-wrap:wrap;align-items:center}}label{{font-size:13px}}
details{{font-size:13px}}.promos label{{display:inline-block;margin-right:10px}}
</style></head><body><main>
<h1>{escape(title)}</h1>
<p class="muted">Each negative complaint shows the category the tool picked. Correct any that are wrong, tick <b>Checked</b> when you've looked at it,
then download <code>labels.csv</code>. Only checked rows are exported. Aim for 100+.</p>
<div class="bar"><span id="progress"></span><button class="primary" id="dl">Download labels.csv</button>
<label><input type="checkbox" id="hide"> Hide checked</label></div>
<div id="list"></div></main>
<script>
const D={data};
const KEY='radar-labels-v1';
let saved={{}};try{{saved=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(e){{}}
function persist(){{try{{localStorage.setItem(KEY,JSON.stringify(saved))}}catch(e){{}}}}
const list=document.getElementById('list');
function esc(s){{const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}}
D.rows.forEach((r,i)=>{{
  const s=saved[r.id]||{{cat:r.cat,promos:r.promos,bot:r.bot,checked:false}};saved[r.id]=s;
  const el=document.createElement('div');el.className='item'+(s.checked?' checked':'');el.dataset.id=r.id;
  const opts=D.cats.map(([k,l])=>`<option value="${{k}}" ${{k===s.cat?'selected':''}}>${{esc(l)}}</option>`).join('');
  const promos=D.promos.map(p=>`<label><input type="checkbox" value="${{esc(p)}}" ${{s.promos.includes(p)?'checked':''}}> ${{esc(p)}}</label>`).join('');
  el.innerHTML=`<div class="meta">#${{i+1}} · ${{r.rating?r.rating+'★':'ticket'}} · ${{esc(r.date)||'undated'}} · tool said: ${{esc(D.cats.find(c=>c[0]===r.cat)[1])}}</div>
    <div class="text">${{esc(r.text)}}</div>
    <div class="row"><select aria-label="Category">${{opts}}</select>
    <label><input type="checkbox" class="bot" ${{s.bot?'checked':''}}> says support is a bot / no human</label>
    <label><input type="checkbox" class="chk" ${{s.checked?'checked':''}}> <b>Checked</b></label></div>
    <details><summary>Promotions mentioned</summary><div class="promos">${{promos}}</div></details>`;
  el.querySelector('select').onchange=e=>{{s.cat=e.target.value;s.checked=true;el.querySelector('.chk').checked=true;el.classList.add('checked');persist();count()}};
  el.querySelector('.bot').onchange=e=>{{s.bot=e.target.checked;persist()}};
  el.querySelector('.chk').onchange=e=>{{s.checked=e.target.checked;el.classList.toggle('checked',s.checked);persist();count();filter()}};
  el.querySelectorAll('.promos input').forEach(cb=>cb.onchange=()=>{{s.promos=[...el.querySelectorAll('.promos input:checked')].map(x=>x.value);persist()}});
  list.appendChild(el);
}});
function count(){{const n=D.rows.filter(r=>saved[r.id].checked).length;document.getElementById('progress').textContent=`${{n}} / ${{D.rows.length}} checked`}}
function filter(){{const h=document.getElementById('hide').checked;document.querySelectorAll('.item').forEach(el=>el.style.display=h&&saved[el.dataset.id].checked?'none':'')}}
document.getElementById('hide').onchange=filter;
document.getElementById('dl').onclick=()=>{{
  const q=v=>'"'+String(v).replace(/"/g,'""')+'"';
  const lines=['id,category,promos,mentions_bot_support'].concat(D.rows.filter(r=>saved[r.id].checked).map(r=>{{const s=saved[r.id];return [q(r.id),q(s.cat),q(s.promos.join('|')),s.bot].join(',')}}));
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines.join('\\n')],{{type:'text/csv'}}));a.download='labels.csv';a.click();
}};
count();
</script></body></html>"""
