from aiswarm.board import Entry
from aiswarm.watch.state import FEED_MAX, RunState, feed_text


def state():
    return RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)


def who(agent_id):
    return {"a": "Ash (a)", "b": "Bea (b)"}.get(agent_id, f"{agent_id} ({agent_id})")


def entry(id_, type_, payload, author="a", round_=2):
    return Entry(id_, "r1", round_, author, type_, payload, [], "all", "t")


def test_events_drive_round_phase_and_cards():
    s = state()
    s.apply({"event": "run_started", "agents": ["a", "b"]})
    assert s.phase == "rounds" and set(s.cards) == {"a", "b"}
    s.apply({"event": "round_started", "round": 3})
    s.apply({"event": "agent_step_started", "round": 3, "agent": "a"})
    assert s.round == 3 and s.cards["a"].state == "thinking…"
    s.apply({"event": "agent_step_finished", "agent": "a", "action": "pass", "failure": "invalid_action"})
    s.apply({"event": "action_posted", "agent": "a", "entry": 7, "type": "pass", "failure": "invalid_action"})
    assert s.cards["a"].state == "failed: invalid_action" and s.cards["a"].last == "pass #7"
    s.apply({"event": "agent_step_finished", "agent": "b", "action": "propose", "failure": None})
    assert s.cards["b"].state == "idle"


def test_phases_through_pause_close_and_post_run():
    s = state()
    s.apply({"event": "run_paused", "round": 2})
    assert s.phase == "paused — resume with aiswarm run --resume r1"
    s.apply({"event": "run_closed", "reason": "budget", "call_id": None})
    assert s.phase == "closed: budget" and s.closed_reason == "budget" and not s.finished
    s.apply({"event": "postrun_started", "run_id": "r1"})
    s.apply({"event": "postrun_step_finished", "step": "review", "agent": "a", "status": "done"})
    assert s.phase == "post-run" and s.current_step == "review" and s.postrun_seen
    s.apply({"event": "postrun_paused", "step": "review"})
    assert s.phase == "post-run paused — resume with aiswarm postrun r1"
    s.apply({"event": "postrun_finished", "failed": 0})
    assert s.phase == "done" and s.finished and s.current_step is None


def test_unknown_events_are_ignored():
    s = state()
    s.apply({"event": "library_retrieved"})
    s.apply({"no": "event"})
    assert s.phase == "waiting"


def test_feed_text_per_type():
    assert feed_text(entry(5, "propose", {"answer_summary": "Mango", "method": "m"}), who) == \
        "#5 r2 Ash (a) propose: Mango"
    assert feed_text(entry(6, "critique", {"target": 5, "about": "answer", "points": "Too sweet."}, "b"), who) == \
        "#6 r2 Bea (b) critique → #5: Too sweet."
    assert feed_text(entry(7, "vote", {"target": 9, "stance": "support", "reason": "ok"}), who) == \
        "#7 r2 Ash (a) vote → #9: support: ok"
    assert feed_text(entry(8, "pass", {"failure": "invalid_action"}), who) == \
        "#8 r2 Ash (a) pass: (failed: invalid_action)"
    assert feed_text(entry(9, "pass", {}), who) == "#9 r2 Ash (a) pass"
    assert feed_text(entry(10, "write_file", {"path": "rules.md", "version": 3, "note": "tidy"}), who) == \
        "#10 r2 Ash (a) write_file: rules.md v3: tidy"


def test_feed_text_is_one_line_and_truncated():
    text = feed_text(entry(5, "propose", {"answer_summary": "line one\nline [two] " + "x" * 300, "method": "m"}), who)
    assert "\n" not in text and len(text) == FEED_MAX and text.endswith("…")
    assert "[two]" in text


from observer_helpers import add_entry, add_review, add_run  # noqa: E402


def refreshed(community):
    store, conn = community
    s = RunState("r1", "g", "r1", "ad hoc", 12)
    s.refresh(conn, store, 5.0)
    return s


def test_refresh_builds_feed_calls_files_and_cards(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"))
    p = add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    call = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Mango it is", "credits": []}, refs=[p])
    add_entry(conn, "r1", "a", "vote", {"target": call, "stance": "support", "reason": "yes"})
    add_entry(conn, "r1", "a", "write_file", {"path": "rules.md", "version": 1, "note": "n"}, round=2)
    add_entry(conn, "r1", "b", "write_file", {"path": "rules.md", "version": 2, "note": "n"}, round=3)
    s = refreshed(community)
    assert [f.entry_id for f in s.feed] == [p, call, call + 1, call + 2, call + 3]
    assert s.feed[0].text == f"#{p} r1 Alpha (a) propose: Mango"
    assert s.calls == [type(s.calls[0])(call, "Bea (b)", 2, 0)]
    assert [(f.path, f.version, f.author, f.round) for f in s.files] == [("rules.md", 2, "b", 3)]
    assert s.cards["a"].name == "Alpha" and s.cards["a"].headline == "Alpha headline"
    assert s.cards["a"].reputation is None and not s.profiles_stale


def test_refresh_adds_only_new_entries(community):
    store, conn = community
    add_run(conn, "r1", agents=("a",))
    add_entry(conn, "r1", "a", "pass", {})
    s = refreshed(community)
    add_entry(conn, "r1", "a", "pass", {})
    s.refresh(conn, store, 5.0)
    assert len(s.feed) == 2


def test_reputation_and_names_reload_only_when_stale(community):
    store, conn = community
    add_run(conn, "r0", agents=("a", "b"))
    add_review(conn, "r0", "b", "a", 4)
    add_run(conn, "r1", agents=("a", "b"))
    s = refreshed(community)
    assert s.cards["a"].reputation == 4.0
    store.set_public("a", "Ashley", "h", "i")
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].name == "Alpha"
    s.apply({"event": "postrun_step_finished", "step": "update", "agent": "a", "status": "done"})
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].name == "Ashley"


def test_post_run_grid_counts_done_and_failed(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"))
    for agent, status in (("a", "done"), ("b", "failed")):
        conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, subject, status, detail, updated_at) "
                     "VALUES ('r1', 'journal', ?, '', ?, '', 't')", (agent, status))
    s = refreshed(community)
    journal = next(r for r in s.postrun if r.step == "journal")
    assert (journal.done, journal.failed, journal.failed_agents) == (1, 1, ["b"])
    assert [r.step for r in s.postrun][0] == "journal" and len(s.postrun) == 8


def test_a_failed_refresh_keeps_the_previous_state(community):
    import sqlite3

    store, conn = community
    add_run(conn, "r1", agents=("a",))
    add_entry(conn, "r1", "a", "pass", {})
    s = refreshed(community)
    before = (list(s.feed), list(s.calls), dict(s.cards))

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    try:
        s.refresh(Broken(), store, 5.0)
    except sqlite3.OperationalError:
        pass
    assert (s.feed, s.calls, s.cards) == before
