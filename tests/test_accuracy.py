"""Regression floors for the keyword rules on the labelled test sets.

These are not claims of real-world accuracy (see docs/ACCURACY.md): both sets
have been used for tuning. They stop a rule change from making things worse."""

from pathlib import Path

import pytest

from radar import classify, ingest, labels

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name,floor", [("challenge_set.csv", 0.90), ("heldout_set.csv", 0.80)])
def test_rules_accuracy_floor(name, floor):
    path = FIXTURES / name
    items = classify.classify_rules(ingest.load(path))
    res = labels.evaluate(items, labels.load_labels(path))
    assert res.n == len(items) and res.missing == 0
    assert res.accuracy >= floor, labels.render_eval(res)
