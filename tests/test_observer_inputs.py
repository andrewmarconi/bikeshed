from datetime import datetime

from observer_helpers import add_entry, add_journal, add_review, add_run

from bikeshed.observer.inputs import (
    before_moment, benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs, think_vs_act_inputs,
)
from bikeshed.observer.metrics import BenchmarkAnswer, RunInfo, closed_runs
from bikeshed.view import make_who

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


def test_think_vs_act_inputs_pair_each_note_with_its_rounds_moves(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt the mango plan."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "b", "think", {"text": "B's secret."}, round=2, visible_to=["b"])
    add_entry(conn, "r1", "a", "adopt", {"target": 1, "reason": "Mango is fine."}, refs=[1], round=2)
    add_entry(conn, "r1", "a", "think", {"text": "Say no quietly."}, round=3, visible_to=["a"])
    add_entry(conn, "r1", "a", "private_message", {"to": "c", "text": "Between us: no."}, round=3,
              visible_to=["a", "c"], to="c")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": "To a from b."}, round=3,
              visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["round 2 think note", "round 2 actions", "round 3 think note", "round 3 actions"]
    assert "I doubt the mango plan." in got["round 2 think note"] and "B's secret." not in got["round 2 think note"]
    assert "Mango is fine." in got["round 2 actions"] and "Pear" not in got["round 2 actions"]
    assert "Between us: no." in got["round 3 actions"] and "To a from b." not in got["round 3 actions"]


def test_think_vs_act_inputs_leave_out_failed_turns_but_keep_plain_passes(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note one."}, round=1, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "model_error"}, round=1)
    add_entry(conn, "r1", "a", "think", {"text": "Note two."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "invalid_action"}, round=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note three."}, round=3, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {}, round=3)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["round 2 think note", "round 2 actions", "round 3 think note", "round 3 actions"]
    assert "failure" not in got["round 2 actions"] and "Pear" in got["round 2 actions"]
    assert "pass" in got["round 3 actions"]


def test_think_vs_act_inputs_none_when_every_noted_round_only_failed(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note."}, round=1, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "model_error"}, round=1)
    who = make_who({i: store.public(i) for i in store.ids()})
    assert think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who) is None


def test_think_vs_act_inputs_none_without_think_notes(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pear", "method": "m"})
    who = make_who({i: store.public(i) for i in store.ids()})
    assert think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who) is None


def _info(run_id, end, agents=("a",)):
    return RunInfo(run_id, "g", None, None, False, "budget", tuple(agents), end, end)


def test_before_is_the_latest_run_that_ended_earlier_not_the_one_created_earlier(community):
    store, _ = community
    created_first = _info("x1", "2000-01-02T03:00:00+00:00")   # created first, finished last
    created_second = _info("x2", "2000-01-02T02:00:00+00:00")
    runs = [created_first, created_second]                      # closed_runs order: creation
    assert before_moment(store, runs, created_second, "a") is None
    assert before_moment(store, runs, created_first, "a") == datetime.fromisoformat(created_second.end)


def test_a_control_agents_before_is_its_reset(community):
    store, _ = community
    store.create("e", "fake", "Seed.", control=True)
    store.set_public("e", "Eve", "Eve headline", "I am Eve.")
    store.save_seed_public("e")
    store.write_private("e", "self.md", "Changed by a cycle.")
    store.reset_to_seed("e")
    reset = next(c.at for c in reversed(store.history("e")) if c.subject == "reset control agent e to seed")
    earlier = _info("x1", "2000-01-01T00:00:00+00:00", agents=("e",))
    now = _info("x2", FUTURE, agents=("e",))
    assert before_moment(store, [earlier, now], now, "e") == reset


def test_a_reset_after_the_run_is_not_its_before(community):
    store, _ = community
    store.create("e", "fake", "Seed.", control=True)
    store.set_public("e", "Eve", "Eve headline", "I am Eve.")
    store.save_seed_public("e")
    store.write_private("e", "self.md", "Changed.")
    store.reset_to_seed("e")                                    # committed now, after both runs below
    earlier = _info("x1", "2000-01-01T00:00:00+00:00", agents=("e",))
    this = _info("x2", "2000-01-02T00:00:00+00:00", agents=("e",))
    assert before_moment(store, [earlier, this], this, "e") == datetime.fromisoformat(earlier.end)


def test_private_public_reads_only_the_journal_written_before_the_reviews(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    add_journal(conn, "r1", "a", "before", "Bea talked too much.")
    add_journal(conn, "r1", "a", "after", "Bea's review of me was fair.")
    add_review(conn, "r1", "a", "b", 2, less_of="rambling")
    who = make_who({i: store.public(i) for i in store.ids()})
    got = private_public_inputs(conn, closed_runs(conn)[0], "a", who)
    assert "Bea talked too much." in got["journal"] and "fair" not in got["journal"]
