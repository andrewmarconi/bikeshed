from observer_helpers import add_entry, add_journal, add_review, add_run

from aiswarm.observer.inputs import (
    benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs, think_vs_act_inputs,
)
from aiswarm.observer.metrics import BenchmarkAnswer, closed_runs
from aiswarm.view import make_who

FUTURE = "2099-01-01T00:00:00+00:00"  # after every fixture commit; git cannot parse far-future dates like 2999


def test_themes_inputs(community):
    store, conn = community
    store.write_private("a", "self.md", "I am careful.")
    add_run(conn, "r1", created=FUTURE)
    add_journal(conn, "r1", "a", "before", "Feeling unsure.")
    add_journal(conn, "r1", "a", "after", "Bea was right.")
    add_review(conn, "r1", "a", "b", 4, more_of="ideas", reason="good")
    add_review(conn, "r1", "a", "a", 5, more_of="patience")
    got = themes_inputs(conn, store, closed_runs(conn)[0], "a")
    assert "Feeling unsure." in got["journal"] and "Bea was right." in got["journal"]
    assert "ideas" in got["reviews written"] and got["reviews written"].count("overall") == 2
    assert "### self-review" in got["reviews written"] and "patience" in got["reviews written"]
    assert got["self.md"] == "I am careful."
    assert got["temperament.md"] == "Temperament of a."


def test_inputs_say_none_when_empty(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    got = themes_inputs(conn, store, closed_runs(conn)[0], "c")
    assert got["journal"] == "(none)" and got["reviews written"] == "(none)"


def test_private_public_inputs_need_peer_reviews(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    who = make_who({i: store.public(i) for i in store.ids()})
    assert private_public_inputs(conn, closed_runs(conn)[0], "a", who) is None
    add_review(conn, "r1", "a", "b", 2, less_of="rambling")
    got = private_public_inputs(conn, closed_runs(conn)[0], "a", who)
    assert "Bea (b)" in got["reviews given"] and "rambling" in got["reviews given"]


def test_self_image_inputs_compare_with_the_previous_run(community):
    store, conn = community
    add_run(conn, "r1", agents=("a",), created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r2", agents=("b",), created=FUTURE)
    add_run(conn, "r3", agents=("a",), created=FUTURE)
    runs = closed_runs(conn)
    got = self_image_inputs(store, runs, runs[2], "a")
    assert 'name = "Alpha"' in got["public profile now"]
    assert set(got) == {"self.md now", "public profile now", "self.md before", "public profile before"}
    first = self_image_inputs(store, runs, runs[0], "a")
    assert first["self.md before"] == "(no earlier run)"


def test_benchmark_inputs():
    answers = [BenchmarkAnswer("r1", False, "Pepperoni"), BenchmarkAnswer("r6", True, "Margherita")]
    assert benchmark_inputs(answers, "r1") == {"this answer": "Pepperoni", "earlier answers": "(none)"}
    assert benchmark_inputs(answers, "r6")["earlier answers"] == "### r1\nPepperoni"


def test_think_vs_act_inputs_hold_only_the_agents_own_thoughts_and_actions(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt the mango plan."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "b", "think", {"text": "B's secret."}, round=2, visible_to=["b"])
    add_entry(conn, "r1", "a", "adopt", {"target": 1, "reason": "Mango is fine."}, refs=[1], round=2)
    add_entry(conn, "r1", "a", "private_message", {"to": "c", "text": "Between us: no."}, round=3,
              visible_to=["a", "c"], to="c")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": "To a from b."}, round=3,
              visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["think notes", "public actions", "private messages sent"]
    assert "I doubt the mango plan." in got["think notes"] and "B's secret." not in got["think notes"]
    assert "Mango is fine." in got["public actions"] and "Pear" not in got["public actions"]
    assert "Between us: no." not in got["public actions"]
    assert "Between us: no." in got["private messages sent"] and "To a from b." not in got["private messages sent"]


def test_think_vs_act_inputs_none_without_think_notes(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pear", "method": "m"})
    who = make_who({i: store.public(i) for i in store.ids()})
    assert think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who) is None
