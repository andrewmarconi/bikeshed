import json

from aiswarm.models.fake import FakeModel
from aiswarm.observer.codebook import theme_counts, update_codebook
from aiswarm.observer.judge import Judge
from aiswarm.observer.store import Judgment, ObserverStore


async def test_new_labels_are_assigned_and_known_ones_skipped(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("honesty", "honesty", "Says what it thinks.")
    reply = json.dumps({"assignments": [{"label": "candour", "code": "honesty", "description": "Says what it thinks."},
                                        {"label": "tidiness", "code": "tidiness", "description": "Keeps order."}]})
    client = FakeModel([reply])
    got = await update_codebook(Judge(client, store, "sonnet"), store, "r1", ["honesty", "candour", "tidiness"])
    assert got.status == "ok"
    assert store.code_for("candour") == "honesty" and store.code_for("tidiness") == "tidiness"
    assert "honesty: Says what it thinks." in client.calls[0][1][0]["content"]


async def test_nothing_new_makes_no_call(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("honesty", "honesty", "d")
    client = FakeModel([])
    assert await update_codebook(Judge(client, store, "sonnet"), store, "r1", ["honesty"]) is None
    assert client.calls == []


def test_theme_counts_use_codes(tmp_path):
    store = ObserverStore(tmp_path / "o.db")
    store.assign("candour", "honesty", "d")
    j = Judgment("themes", "r1", "a", "ok", {"themes": [
        {"label": "candour", "polarity": "expressed", "evidence": "x", "source": "s"},
        {"label": "speed", "polarity": "expressed", "evidence": "y", "source": "s"}]}, None, None, "p")
    failed = Judgment("themes", "r1", "b", "failed", None, "boom", None, "p")
    assert theme_counts(store, [j, failed]) == {"a": {"honesty": 1, "speed": 1}}
