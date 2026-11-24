# Classifier accuracy

How well the offline keyword rules (`radar/taxonomy.py`) pick the primary
complaint category, measured on two hand-written, labelled test sets. Both
sets are synthetic: they were invented for testing and are not real reviews.

| Set | Rows | How it was written | Score | What the score means |
|---|---:|---|---:|---|
| `tests/fixtures/challenge_set.csv` | 150 | By a separate agent that never saw the rules | 59% before tuning, 94% after | **In-sample.** The rules were tuned on this set's mistakes. |
| `tests/fixtures/heldout_set.csv` | 120 | By another separate agent that saw neither the rules nor the first set | **67%** on its single blind run, 83% after one round of general fixes | **67% is the honest figure for unseen text.** 83% is optimistic, because its mistakes were seen before those fixes. |

## What this means in practice

- **Expect roughly 70–80% accuracy on real reviews.** Accuracy may be lower
  on writing styles neither set covers.
- **The usual failure is a miss, not a wrong label.** A real complaint
  phrased in a way the rules don't recognise falls into "Other". Other
  categories are rarely confused with each other. So the rules undercount
  problems rather than inventing them.
- **The rules can't read non-English complaints.**
- **They are good enough for trends**: what rose, when, and next to which
  promotion.
- **Before quoting exact percentages to anyone**, do one of these:
  1. Check 100+ rows with `radar label`, then pass `--labels` to `report`.
     Checked rows override the rules.
  2. Classify with `--claude`, and measure it on the same sets with
     `radar eval ... --claude`. It has not been measured yet, because no API
     key was available while it was being built.

The go/no-go verdict (`verdict.md`) repeats this warning whenever a report is
mostly rule-classified.

## Re-measuring

```bash
radar eval tests/fixtures/challenge_set.csv tests/fixtures/challenge_set.csv
radar eval tests/fixtures/heldout_set.csv tests/fixtures/heldout_set.csv
```

`tests/test_accuracy.py` fails if either score drops below a floor, so a rule
change can't make things quietly worse. Since both sets have now been used for
tuning, a truly blind number needs a new set written by someone who hasn't
seen the rules. Real labelled reviews are better still.
