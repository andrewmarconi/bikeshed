import sqlite3

from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import (
    Influence, action_mix, by_category, closed_runs, credit_pairs, credits, harness_changes, influence, open_runs,
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


from observer_helpers import add_review  # noqa: E402

from aiswarm.observer.metrics import (  # noqa: E402
    BenchmarkAnswer, benchmark_answers, by_category, dimension_scores, harness_changes, identity_events,
    norm_events, peer_scores, self_peer_gap, twins,
)
from aiswarm.view import make_who  # noqa: E402


def test_peer_scores_and_self_gap(community):
    store, conn = community
    add_run(conn, "r1")
    add_review(conn, "r1", "b", "a", 4)
    add_review(conn, "r1", "c", "a", 2)
    add_review(conn, "r1", "a", "a", 5)
    assert peer_scores(conn, "r1") == {"a": 3.0}
    assert self_peer_gap(conn, "r1") == {"a": 2.0}


def test_dimension_scores_use_peer_ratings_only(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('r1', 'b', 'dimension', 'clarity', 'Clear?', 'adopted', 't')")
    conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES ('clarity', 'Clear?', 1, 'r1')")
    peer = add_review(conn, "r1", "b", "a", 4)
    own = add_review(conn, "r1", "a", "a", 5)
    conn.execute("INSERT INTO review_ratings (review_id, dimension_id, rating, reason) VALUES (?, 1, 2, 'x')", (peer,))
    conn.execute("INSERT INTO review_ratings (review_id, dimension_id, rating, reason) VALUES (?, 1, 5, 'x')", (own,))
    assert dimension_scores(conn, "r1") == {"a": {"clarity": 2.0}}


def test_identity_events_with_prior_peer_score(community):
    store, conn = community
    add_run(conn, "r1", created="2000-01-01T00:00:00+00:00")
    add_review(conn, "r1", "b", "a", 1)
    store.set_public("a", "Ash", "New headline", "I am Ash.")
    store.write_private("a", "self.md", "I changed.")
    store.write_private("a", "lessons.md", "Not an identity event.")
    events = [e for e in identity_events(conn, store, closed_runs(conn)) if e.agent_id == "a"]
    kinds = [e.kind for e in events]
    assert "rename" in kinds and "self.md" in kinds and "lessons.md" not in kinds
    rename = next(e for e in events if e.kind == "rename")
    assert rename.detail == "Alpha → Ash" and rename.prior_peer == 1.0


def test_twins_match_the_controls_seed_temperament(tmp_path):
    from aiswarm.profiles import ProfileStore

    store = ProfileStore(tmp_path / "community")
    store.create("d", "fake", "Wants evidence.")
    store.create("x", "fake", "Something else.")
    store.create("e", "fake", "Wants evidence.\n", control=True)
    store.write_private("d", "temperament.md", "Rewritten later.")
    assert twins(store) == {"e": ["d"]}


def test_by_category_counts_endings_and_actions(community):
    store, conn = community
    add_run(conn, "r1", category="contested", ended="budget")
    add_run(conn, "r2", category="contested", ended="consensus")
    add_run(conn, "r3")
    add_entry(conn, "r1", "a", "vote", {"target": 1, "stance": "support", "reason": "r"})
    stats = by_category(conn, closed_runs(conn))
    assert stats["contested"].runs == 2 and stats["contested"].endings == {"budget": 1, "consensus": 1}
    assert stats["contested"].actions["vote"] == 1
    assert stats["ad hoc"].runs == 1


def test_benchmark_answers_use_synthesis_or_latest_proposals(community):
    store, conn = community
    add_run(conn, "r1", benchmark=True)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Old", "method": "m"})
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pepperoni", "method": "m"})
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Mushroom", "method": "m"})
    add_entry(conn, "r1", "system", "run_closed", {"reason": "budget", "call_id": None})
    add_run(conn, "r2", benchmark=True, ended="consensus")
    call = add_entry(conn, "r2", "a", "call_consensus", {"synthesis": "Margherita for all.", "credits": []})
    add_entry(conn, "r2", "system", "run_closed", {"reason": "consensus", "call_id": call})
    add_run(conn, "r3")
    who = make_who({i: store.public(i) for i in store.ids()})
    answers = benchmark_answers(conn, closed_runs(conn), who)
    assert answers == [
        BenchmarkAnswer("r1", False, "- Alpha (a): Pepperoni\n- Bea (b): Mushroom"),
        BenchmarkAnswer("r2", True, "Margherita for all."),
    ]


def test_norm_events(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, text, status, created_at) "
                 "VALUES ('r1', 'a', 'norm', 'Be kind.', 'adopted', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('r1', 'b', 'dimension', 'clarity', 'Clear?', 'open', 't')")
    events = norm_events(conn)
    assert [(e.author, e.kind, e.text, e.status) for e in events] == [
        ("a", "norm", "Be kind.", "adopted"), ("b", "dimension", "clarity: Clear?", "open")]


def test_harness_changes_marks_runs_whose_provenance_changed(community, monkeypatch):
    import aiswarm.observer.metrics as metrics

    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2")
    monkeypatch.setattr(metrics, "provenance_changes", lambda c, run_id: ["code"] if run_id == "r2" else [])
    assert harness_changes(conn, closed_runs(conn)) == [1]


def test_closed_runs_record_the_process_and_tag_process_2(community):
    store, conn = community
    add_run(conn, "r1", goal_id="best-fruit")
    add_run(conn, "r2", process=2)
    r1, r2 = closed_runs(conn)
    assert (r1.process, r1.tag) == (1, "best-fruit")
    assert (r2.process, r2.tag) == (2, "r2 (P2)")


def test_a_change_of_process_is_a_harness_marker(community):
    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2", process=2)
    add_run(conn, "r3", process=2)
    add_run(conn, "r4")
    assert harness_changes(conn, closed_runs(conn)) == [1, 3]


def test_by_category_splits_by_process(community):
    store, conn = community
    add_run(conn, "r1", category="contested")
    add_run(conn, "r2", category="contested", process=2)
    stats = by_category(conn, closed_runs(conn))
    assert stats["contested"].runs == 1 and stats["contested (P2)"].runs == 1


def test_action_mix_counts_private_messages(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "hi"}, visible_to=["a", "b"], to="b")
    assert action_mix(conn, closed_runs(conn)[0])["a"]["private_message"] == 1


def test_closed_runs_reads_a_store_from_before_process_2():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE runs (id TEXT, goal TEXT, status TEXT, ended_reason TEXT, agent_ids TEXT, created_at TEXT, "
        "goal_id TEXT, goal_category TEXT, is_benchmark INTEGER);"
        "CREATE TABLE entries (run_id TEXT, created_at TEXT);"
        "CREATE TABLE postrun_steps (run_id TEXT, updated_at TEXT);"
        "INSERT INTO runs VALUES ('r1', 'g', 'closed', 'budget', '[\"a\"]', '2026-10-01T00:00:00+00:00', "
        "NULL, NULL, 0);")
    assert [(r.id, r.process) for r in closed_runs(conn)] == [("r1", 1)]
