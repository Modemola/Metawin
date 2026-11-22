# Complaint Radar

Turns player feedback (Trustpilot reviews, Intercom support conversations,
Discord/CSV exports) into a one-page report:

- **What players complain about**: every negative review gets one primary
  reason, such as promo not credited, withdrawal, account flagged, KYC,
  fairness, fees, bugs or support.
- **Early warning**: weeks where a complaint type jumps compared with its recent
  baseline, with the promotions those complaints mention. This catches a
  credit drop that's going wrong before it spreads on X.
- **Which promotions cause complaints**: Lossback, WinBack/Rakeback, MetaMax
  rank rewards, X giveaways, Cash Drops, free spins and others.
- **Complaints about bot support**: how many complaints say support is a bot,
  or that they couldn't reach a real person.
- **Reply queue**: unanswered negative reviews, most serious first, each with
  a draft reply. Drafts never confirm account details or promise money, and a
  person edits every reply before posting it.

Every run also writes a row-level CSV, so you can check or correct the
classifications by hand.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[claude,dev]"

# Try it on synthetic data (clearly marked as such in the report)
.venv/bin/python -m radar samples/synthetic_trustpilot.txt -o out/sample_report.html --sample

# Real data: put files in data/ (any mix of .txt / .json / .csv)
.venv/bin/python -m radar data/ -o out/report.html
```

Outputs: `out/report.html` (the dashboard), `out/report.csv` (one row per
review with its classification), `out/report.json` (headline numbers).

### Classifying with Claude (optional)

The default classifier is a set of keyword rules in `radar/taxonomy.py`. It's
free, works offline and is easy to tune. For better accuracy on mixed,
sarcastic or non-English complaints, set `ANTHROPIC_API_KEY` and add `--claude`.
Results are cached in `.cache/`, so re-running never pays twice for the same
review. `--personalize-replies` also has Claude adapt each reply draft to its
review. If no key is set, both options fall back to the rules and templates.

## Collecting Trustpilot reviews

Trustpilot blocks automated scraping, so collect reviews in a normal browser:

1. Open `https://www.trustpilot.com/review/metawin.com`, then filter to 1★ and
   2★ and sort by most recent.
2. Select from the first reviewer's name down to the last review on the page
   and copy it (Ctrl/Cmd+C).
3. Paste it into a text file in `data/`, such as `data/trustpilot_p1.txt`.
   Repeat for more pages. Pages can go in one file or several, and duplicates
   are removed automatically.

The parser finds each review by its "Rated N out of 5 stars" line and detects
"Reply from MetaWin" blocks, which is how it calculates the public reply rate.

## Supported inputs

| Input | How it's detected |
|---|---|
| Trustpilot copied from the browser | any `.txt` file |
| Trustpilot Business API reviews JSON | `.json` with `reviews[]` (`stars`, `text`, `createdAt`, `companyReply`) |
| Intercom conversations JSON | `.json` with `conversations[]` (`source.body`, `conversation_parts`) |
| Anything else (Discord, sheets) | `.csv` with `text` and `date` columns; `rating`, `title`, `source`, `author`, `company_replied` are optional |

## Privacy

Reviewer names are never stored. They're hashed, which lets repeat complainers
be counted without identifying anyone. Review text only appears in the local
report you generate.

## Tests

```bash
.venv/bin/python -m pytest -q
```
