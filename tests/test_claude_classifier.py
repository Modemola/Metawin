"""The Claude classifier's plumbing, tested with a fake client (no network, no key):
batching, mapping answers back to the right complaint, caching, and every
failure falling back to the keyword rules."""

import json
from types import SimpleNamespace

import pytest

from radar import classify
from radar.models import Complaint

anthropic = pytest.importorskip("anthropic")  # optional dependency: pip install .[claude]


def _items(n):
    return [Complaint(id=f"c{i}", source="t", text=f"complaint number {i} my withdrawal is pending", rating=1) for i in range(n)]


class FakeClient:
    """Answers each batch with whatever `answer(payload)` returns."""

    def __init__(self, answer, stop_reason="end_turn"):
        self.calls = []
        self.answer, self.stop_reason = answer, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        payload = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        text = json.dumps({"results": self.answer(payload)})
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def _result(i, category="fees", **extra):
    return {"id": str(i), "category": category, "other_categories": [], "promos": [], "severity": 1,
            "needs_human": False, "mentions_bot_support": False, "summary": f"summary {i}", **extra}


@pytest.fixture
def fake(monkeypatch):
    def install(client):
        monkeypatch.setattr(anthropic, "Anthropic", lambda: client)
        return client
    return install


def test_batches_map_back_by_id_even_when_reordered(fake, tmp_path):
    items = _items(45)  # 3 batches of 20/20/5
    client = fake(FakeClient(lambda payload: [_result(p["id"], summary=None) | {"summary": p["text"]} for p in reversed(payload)]))
    classify.classify_with_claude(items, cache_path=tmp_path / "cache.json")
    assert len(client.calls) == 3
    assert all(c.summary == c.text and c.classified_by == "claude" for c in items)  # each answer landed on its own item
    req = client.calls[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_cache_means_rerun_costs_nothing(fake, tmp_path):
    cache = tmp_path / "cache.json"
    fake(FakeClient(lambda payload: [_result(p["id"]) for p in payload]))
    classify.classify_with_claude(_items(5), cache_path=cache)
    second = fake(FakeClient(lambda payload: pytest.fail("should not call the API")))
    items = classify.classify_with_claude(_items(5), cache_path=cache)
    assert second.calls == [] and all(c.category == "fees" for c in items)


def test_partial_bad_and_refused_answers_fall_back_to_rules(fake, tmp_path):
    def answer(payload):
        return [_result(0, category="not_a_category"), _result(1, promos=["Made up promo", "Lossback"]), {"id": "99"}]
    fake(FakeClient(answer))
    items = classify.classify_with_claude(_items(3), cache_path=tmp_path / "c.json")
    assert items[0].classified_by == "rules" and items[0].category == "withdrawal"  # invalid category rejected
    assert items[1].classified_by == "claude" and items[1].promos == ["Lossback"]  # unknown promo dropped
    assert items[2].classified_by == "rules"  # no answer for it

    fake(FakeClient(lambda payload: [_result(p["id"]) for p in payload], stop_reason="refusal"))
    refused = classify.classify_with_claude(_items(2), cache_path=tmp_path / "r.json")
    assert all(c.classified_by == "rules" for c in refused)
