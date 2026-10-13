from bikeshed.board import Entry
from bikeshed.watch.state import FEED_MAX, RulingLine, RunState, feed_line, feed_text


def state():
    return RunState("r1", "Pick a fruit.", "best-fruit", "unsettled", 12)


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
    assert s.phase == "paused — resume with bikeshed run --resume r1"
    s.apply({"event": "run_closed", "reason": "budget", "call_id": None})
    assert s.phase == "closed: budget" and s.closed_reason == "budget" and not s.finished
    s.apply({"event": "postrun_started", "run_id": "r1"})
    s.apply({"event": "postrun_step_finished", "step": "review", "agent": "a", "status": "done"})
    assert s.phase == "post-run" and s.current_step == "review" and s.postrun_seen
    s.apply({"event": "postrun_paused", "step": "review"})
    assert s.phase == "post-run paused — resume with bikeshed postrun r1"
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


def test_feed_text_is_one_line_and_keeps_a_long_summary_whole():
    text = feed_text(entry(5, "propose", {"answer_summary": "line one\nline [two] " + "x" * 300 + " end", "method": "m"}), who)
    assert "\n" not in text and "[two]" in text and text.endswith(" end")


def test_feed_text_truncates_only_runaway_entries():
    text = feed_text(entry(5, "propose", {"answer_summary": "x" * 5000, "method": "m"}), who)
    assert len(text) == FEED_MAX and text.endswith("…")


def test_feed_text_summarises_process2_entries():
    assert feed_text(entry(7, "think", {"text": "where I stand"}), who) == "💭 #7 r2 Ash (a): where I stand"
    assert feed_text(entry(8, "private_message", {"to": "b", "text": "psst"}), who) == "🔒 #8 r2 Ash (a) → Bea (b): psst"
    assert feed_text(entry(9, "abstain", {"target": 4, "reason": "unsure"}), who).endswith("abstain → #4: unsure")


def test_feed_lines_know_their_pane_and_parts():
    think = feed_line(entry(7, "think", {"text": "hm"}), who)
    pm = feed_line(entry(8, "private_message", {"to": "b", "text": "psst"}), who)
    vote = feed_line(entry(9, "vote", {"target": 4, "stance": "object", "reason": "no"}), who)
    failed = feed_line(entry(10, "pass", {"failure": "model_error"}), who)
    assert (think.pane, pm.pane, vote.pane, failed.pane) == ("thinking", "private", "table", "table")
    assert (pm.to, pm.to_name) == ("b", "Bea (b)")
    assert (vote.stance, vote.body, vote.target) == ("object", "no", " → #4")
    assert failed.failure == "model_error" and failed.text == "#10 r2 Ash (a) pass: (failed: model_error)"


def test_process_2_steps_set_the_phase_and_status():
    s = RunState("r1", "g", "r1", "ad hoc", 12, process=2)
    s.apply({"event": "run_started", "agents": ["a", "b"]})
    s.apply({"event": "agent_step_started", "round": 2, "agent": "a", "phase": "think"})
    assert s.step_phase == "think" and s.cards["a"].state == "thinking…"
    s.apply({"event": "agent_step_started", "round": 2, "agent": "b"})
    assert s.step_phase == "react" and s.cards["b"].state == "speaking…"
    s.apply({"event": "agent_step_started", "round": 2, "agent": "a", "phase": "poll"})
    assert s.step_phase == "poll" and s.cards["a"].state == "polling…"


def test_process_1_steps_keep_thinking_and_no_phase():
    s = state()
    s.apply({"event": "agent_step_started", "round": 1, "agent": "a"})
    assert s.cards["a"].state == "thinking…" and s.step_phase is None


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


def test_feed_text_for_scorer_rulings():
    rejected = entry(45, "scorer_rejected", {"call_id": 31, "feedback": "Name one\ntopping."}, author="system")
    accepted = entry(46, "scorer_accepted", {"call_id": 40}, author="system")
    sys_who = lambda a: "system" if a == "system" else who(a)  # noqa: E731
    assert feed_text(rejected, sys_who) == "#45 r2 system scorer_rejected → #31: Name one topping."
    assert feed_text(accepted, sys_who) == "#46 r2 system scorer_accepted → #40"
    assert feed_line(rejected, sys_who).pane == "table"


def _points(conn, *rows):
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES (?, ?, ?, ?, 't')", rows)


def test_refresh_reads_lifetime_points_and_the_newest_ruling(community):
    store, conn = community
    add_run(conn, "r0", agents=("a", "b"))
    _points(conn, ("r0", "a", 1, "idea"))
    add_run(conn, "r1", agents=("a", "b"), status="running")
    first = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "Fruit", "credits": []})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Kiwi", "credits": []})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": second, "feedback": "Say why."}, refs=[second])
    s = refreshed(community)
    assert s.cards["a"].points == 1 and s.cards["b"].points == 0
    assert s.ruling == RulingLine("rejected", second)
    third = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "Kiwi, sweet", "credits": []})
    add_entry(conn, "r1", "system", "scorer_accepted", {"call_id": third}, refs=[third])
    _points(conn, ("r1", "a", third, "me"), ("r1", "b", third, "kiwi"))
    s.apply({"event": "run_closed", "reason": "consensus", "call_id": third})  # points are reloaded on close
    s.refresh(conn, store, 5.0)
    assert s.cards["a"].points == 2 and s.cards["b"].points == 1
    assert s.ruling == RulingLine("accepted", third)


def test_refresh_without_a_ruling(community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running")
    assert refreshed(community).ruling is None


def test_refresh_works_on_a_store_from_before_the_scorer(community):
    store, conn = community
    conn.execute("DROP TABLE scorer_points")  # an older store: read-only readers never migrate it
    add_run(conn, "r1", agents=("a", "b"), status="running")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    s = refreshed(community)
    assert s.feed and s.cards["a"].points == 0 and not s.profiles_stale


def test_has_table(community):
    from bikeshed.db import has_table
    store, conn = community
    assert has_table(conn, "scorer_points") and not has_table(conn, "nope")
