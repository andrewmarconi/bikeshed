from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import (
    Influence, action_mix, closed_runs, credit_pairs, credits, influence, open_runs,
)


def test_closed_runs_in_order_with_labels(community):
    store, conn = community
    add_run(conn, "r1", goal_id="best-fruit", category="contested", created="2026-10-01T00:00:00+00:00")
    add_run(conn, "r2", created="2026-10-02T00:00:00+00:00")
    add_run(conn, "r3", status="running", created="2026-10-03T00:00:00+00:00")
    add_entry(conn, "r1", "a", "pass", {}, created="2026-10-01T00:05:00+00:00")
    runs = closed_runs(conn)
    assert [r.id for r in runs] == ["r1", "r2"]
    assert runs[0].label == "best-fruit" and runs[0].category == "contested"
    assert runs[1].label == "r2" and runs[1].category == "ad hoc"
    assert runs[0].end == "2026-10-01T00:05:00+00:00"
    assert open_runs(conn) == ["r3"]


def test_action_mix_counts_failures_separately(community):
    store, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    add_entry(conn, "r1", "a", "pass", {})
    add_entry(conn, "r1", "b", "pass", {"failure": "invalid_action"})
    add_entry(conn, "r1", "system", "goal", {"goal": "g"})
    mix = action_mix(conn, closed_runs(conn)[0])
    assert mix["a"]["propose"] == 1 and mix["a"]["pass"] == 1
    assert mix["b"]["failed"] == 1 and mix["b"]["pass"] == 0
    assert set(mix) == {"a", "b", "c"}


def test_influence_counts_what_others_do_with_your_entries(community):
    store, conn = community
    add_run(conn, "r1")
    p = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    add_entry(conn, "r1", "b", "adopt", {"target": p, "reason": "r"}, refs=[p])
    add_entry(conn, "r1", "c", "refine", {"target": p, "change": "c"}, refs=[p])
    add_entry(conn, "r1", "c", "critique", {"target": p, "about": "answer", "points": "p"}, refs=[p])
    add_entry(conn, "r1", "a", "adopt", {"target": p, "reason": "self"}, refs=[p])
    add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "s", "credits": []}, refs=[p])
    conn.execute("INSERT INTO files (run_id, path, version, author, git_commit, created_at) "
                 "VALUES ('r1', 'f.md', 1, 'c', 'abc', 't')")
    inf = influence(conn, closed_runs(conn)[0])
    assert inf["a"] == Influence(adopted=1, refined=1, critiqued=1, cited=1)
    assert inf["a"].score == 3
    assert inf["c"].files == 1


def test_credits_and_pairs(community):
    store, conn = community
    add_run(conn, "r1", ended="consensus")
    call = add_entry(conn, "r1", "a", "call_consensus",
                     {"synthesis": "s", "credits": [{"agent_id": "a", "contribution": "me"},
                                                    {"agent_id": "b", "contribution": "idea"}]})
    add_entry(conn, "r1", "system", "run_closed", {"reason": "consensus", "call_id": call})
    add_run(conn, "r2")
    add_entry(conn, "r2", "system", "run_closed", {"reason": "budget", "call_id": None})
    got = credits(conn, "r1")
    assert got.caller == "a" and got.credited == {"a": "me", "b": "idea"}
    assert credits(conn, "r2") is None
    assert credit_pairs(conn, closed_runs(conn)) == [("r1", "a", "a"), ("r1", "a", "b")]
