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

Every run also writes:

- **`brief.md`**: the pitch. A short findings brief that leads with the two or
  three strongest findings, each with numbers and short quotes (no reviewer
  names), and a closing offer to run the tool on private support tickets. It
  pastes cleanly into an email, an X DM or Notion. A brief made from `--sample`
  data is stamped "Do not send".
- **`verdict.md`**: internal only. A go/no-go on whether the data supports
  pitching at all: is there enough of it (50+ negative reviews over 60+ days),
  and are there at least two findings strong enough that a founder would act
  on them? Treat it as a starting point, and read the quotes and the CSV
  before deciding.
- **A row-level CSV**, so you can check or correct the classifications by hand.

### Before/after a promotion or credit drop

```bash
.venv/bin/python -m radar data/ --event "2026-10-20:Lossback drop" --event "2026-11-05:Cash Drop"
```

For each `--event`, the report compares negative reviews in the 14 days
before and after the date (`--window` changes the number of days), lists which
promotions were named afterwards, and marks the date on the weekly chart. A
clear jump becomes the lead finding in `brief.md`.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[claude,dev]"

# Try it on synthetic data (clearly marked as such in the report)
.venv/bin/python -m radar report samples/synthetic_trustpilot.txt -o out/sample_report.html --sample

# Real data: put files in data/ (any mix of .txt / .json / .csv)
.venv/bin/python -m radar report data/ -o out/report.html
```

Outputs, all in `out/`: `report.html` (the dashboard), `brief.md` (the pitch),
`verdict.md` (go/no-go), `report.csv` (one row per review with its
classification) and `report.json` (headline numbers).

### Classifying with Claude (optional)

The default classifier is a set of keyword rules in `radar/taxonomy.py`. It's
free, works offline and is easy to tune. For better accuracy on mixed,
sarcastic or non-English complaints, set `ANTHROPIC_API_KEY` and add `--claude`.
Results are cached in `.cache/`, so re-running never pays twice for the same
review. `--personalize-replies` also has Claude adapt each reply draft to its
review. If no key is set, both options fall back to the rules and templates.

## Checking accuracy before quoting numbers

The numbers in a pitch are only as good as the classification, so check it.

```bash
# 1. Write a labelling page. Open it in a browser, correct any wrong
#    categories, tick "Checked", then click "Download labels.csv".
.venv/bin/python -m radar label data/ -o out/label.html

# 2. Measure the classifier against your labels: accuracy, per-category
#    precision/recall, the most common confusions, and every mistake.
.venv/bin/python -m radar eval data/ labels.csv
.venv/bin/python -m radar eval data/ labels.csv --claude   # compare with Claude

# 3. Build the report with your labels applied. The checked rows override
#    the classifier, so the brief quotes checked data.
.venv/bin/python -m radar report data/ --labels labels.csv
```

The row-level `report.csv` has the same `id` and `category` columns, so
correcting it in a spreadsheet also produces a valid labels file.

**How accurate are the keyword rules?** They scored **67% on a blind test**
of complaint phrasing they had never seen. The usual failure is a real
complaint landing in "Other". That's fine for trends, but check before quoting
exact percentages. [`docs/ACCURACY.md`](docs/ACCURACY.md) has the full
method, both test sets, and what the numbers do and don't mean.

## Before anything leaves your hands: check the quotes

```bash
.venv/bin/python -m radar verify-quotes out/brief.md data/
```

Every quotation (text in double quotes) must appear word for word in the
source data. An ellipsis (…) marks a cut, and the pieces must appear in order
in the same post. The command lists any quote it can't find and exits non-zero,
so a misquote can't slip into a pitch.

## Early warning alerts (Slack / Discord)

```bash
export RADAR_WEBHOOK_URL="https://hooks.slack.com/services/..."   # or a Discord webhook URL
.venv/bin/python -m radar watch data/            # posts each new spike once
.venv/bin/python -m radar watch data/ --dry-run  # print instead of posting
```

Run it on a schedule against fresh data (see the connectors below). It only
alerts on spikes in the latest two weeks of data, and remembers what it has
sent in `.cache/watch_state.json`, so nothing is announced twice. The webhook
URL is read from an environment variable, never from the command line.

## Public forum posts (Bitcointalk)

No account or key needed. Players often take unresolved casino disputes to
Bitcointalk, including MetaWin's official announcement thread.

```bash
# Whole complaint threads, plus the complaints posted in the announcement thread since a date
.venv/bin/python -m radar fetch bitcointalk --topic 5578771 --topic 5576505 --out data/bitcointalk_threads.csv
.venv/bin/python -m radar fetch bitcointalk --topic 5483159 --since 2025-06-01 --complaints-only --out data/bitcointalk_ann.csv
```

The fetcher leaves 3 seconds between requests, drops quoted text so a
complaint isn't counted once per reply, and hashes usernames before writing
anything. Forum threads mix complaints with commentary, so a forum post only
counts as a complaint when it names a problem **and** describes the writer's
own experience ("my withdrawal", "I deposited…"). Treat forum data as evidence
of **specific incidents** to read and quote. Counts mostly reflect which
threads you fetched, not how common a problem is.

## Collecting Trustpilot reviews

Trustpilot blocks automated scraping, so collect reviews in a normal browser:

1. Open `https://www.trustpilot.com/review/metawin.com`, then filter to 1★ and
   2★ and sort by most recent.
2. Select from the first reviewer's name down to the last review on the page
   and copy it (Ctrl/Cmd+C).
3. Paste it into a text file in `data/`, such as `data/trustpilot_p1.txt`.
   Repeat for more pages. Pages can go in one file or several, and duplicates
   are removed automatically.

The parser finds each review by its rating line ("Rated 1 out of 5 stars",
"1 out of 5 stars", "Rating: 1/5" or ★☆☆☆☆) and detects "Reply from MetaWin"
blocks, which is how it calculates the public reply rate. If a review shows
"See more", expand it before copying, or only the shortened text is captured.

## Connecting to live data (pilot)

A company that agrees to a pilot can run the tool on its own data with just an
API token. Tokens are read from environment variables only, never from
command-line arguments, so they don't end up in shell history.

```bash
# Intercom support conversations created since a date (--until is optional)
export INTERCOM_TOKEN=...          # Intercom access token with "read conversations"
.venv/bin/python -m radar fetch intercom --since 2026-08-01 --out data/intercom.json

# Trustpilot reviews for the company's own profile
export TRUSTPILOT_API_KEY=...      # Trustpilot API key for the business's own profile
.venv/bin/python -m radar fetch trustpilot --domain metawin.com --out data/trustpilot.json
# or skip the domain lookup: --business-unit-id <id>

.venv/bin/python -m radar data/ -o out/report.html
```

- **Intercom** needs an access token (Developer Hub app) with the
  *read conversations* permission. A 401/403 means the token lacks it.
- **Trustpilot** needs an API key issued for the business's own Trustpilot
  profile.
- `--max` caps how many conversations or reviews are downloaded (default
  5000). Rate limits (HTTP 429) and server errors are retried automatically.
- Nothing that identifies a player is written to disk: Intercom contacts and
  author names/emails and Trustpilot reviewer names are dropped, and email
  addresses and phone numbers inside messages become `[email]` / `[phone]`.

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
