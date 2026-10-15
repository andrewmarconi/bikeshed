from dataclasses import replace

import pytest

from bikeshed.actions import ActionError, Critique, ReadEntry
from bikeshed.agent import make_reader, make_validator
from bikeshed.board import SYSTEM, Board, Entry
from bikeshed.briefs import EMPTY, SETUP, SETUP_NONE, BriefSpec, assign
from bikeshed.view import METHODS_HEADER, NOTES_HEADER, build_view, collect_inputs, make_who, render_entry
from bikeshed.workspace import Workspace


def setup(tmp_path, community, make_run):
    store, conn = community
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    ws = Workspace(conn, "r1", tmp_path / "ws")
    return store, conn, board, ws


def view_for(agent_id, round, store, board, ws, budget=50_000, view_entries=15):
    inputs = collect_inputs(agent_id=agent_id, goal="Pick a colour", round=round, board=board,
                            store=store, workspace=ws, view_entries=view_entries)
    return build_view(inputs, budget_chars=budget)


def test_private_files_of_others_never_appear(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.write_private("b", "temperament.md", "TEMPERAMENT-B-SECRET")
    store.write_private("b", "lessons.md", "LESSON-B-SECRET")
    store.write_private("b", "peer-notes.md", "NOTES-B-SECRET")
    store.write_private("a", "lessons.md", "LESSON-A-OWN")
    entry = board.append(2, "b", "propose", {"answer_summary": "Blue", "method": "M"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, 'r1', 'b', 'm', 'TRACE-B-SECRET')", (entry.id,))
    view = view_for("a", 2, store, board, ws)
    for secret in ("TEMPERAMENT-B-SECRET", "LESSON-B-SECRET", "NOTES-B-SECRET", "TRACE-B-SECRET"):
        assert secret not in view
    assert "LESSON-A-OWN" in view
    assert "TRACE-B-SECRET" not in view_for("b", 2, store, board, ws)


def test_round_one_hides_other_opening_proposals(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    board.append(1, "b", "propose", {"answer_summary": "B-OPENING", "method": "M"})
    view = view_for("a", 1, store, board, ws)
    assert "B-OPENING" not in view
    assert "opening round" in view
    assert "B-OPENING" in view_for("a", 2, store, board, ws)


def test_shows_goal_profiles_and_open_calls(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.set_public("b", "Beatrix", "Bea headline", "I am Bea.")
    call = board.append(2, "b", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": []})
    board.append(3, "c", "vote", {"target": call.id, "stance": "support", "reason": "ok"}, refs=[call.id])
    ws.write("draft.md", 0, "text", author="c", note="n")
    view = view_for("a", 4, store, board, ws)
    assert "Pick a colour" in view and "# Round 4" in view
    assert "Beatrix (b) — Bea headline" in view
    assert "formerly: Bea" in view
    assert f"#{call.id} by Beatrix (b): support: Beatrix (b), Cyd (c)" in view
    assert "critiqued: no" in view
    assert "draft.md (v1)" in view


def test_trimming_keeps_newest_entries_and_header(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    for i in range(60):
        board.append(2, "b", "question", {"target": 1, "text": f"QUESTION-{i:02d} " + "x" * 200}, refs=[1])
    view = view_for("a", 3, store, board, ws, budget=2_500, view_entries=60)
    assert "Pick a colour" in view
    assert "QUESTION-59" in view
    assert "QUESTION-00" not in view
    assert len(view) <= 2_500
    assert view.endswith("# Current goal\nPick a colour")


def test_view_ends_by_restating_the_current_goal(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.write_private("a", "lessons.md", "An old lesson about pizza.")
    view = view_for("a", 1, store, board, ws)
    assert view.startswith("# Goal\nPick a colour")
    assert view.endswith("\n\n# Current goal\nPick a colour")


def test_carried_over_notes_and_methods_are_labelled_as_from_earlier_runs(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.write_private("a", "lessons.md", "An old lesson.")
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15, library=["- #9 in run old by Bea (b) (propose); goal: G\n  method: M"])
    view = build_view(inputs, budget_chars=50_000)
    assert f"{NOTES_HEADER}\n## lessons.md\nAn old lesson." in view
    assert NOTES_HEADER == "# Your private notes from earlier runs (only you can see these; those runs may have had other goals)"
    assert METHODS_HEADER == "# Methods from earlier runs (each is shown with its own goal, which may differ from this one)"
    assert METHODS_HEADER in view


def test_entries_responding_to_me_survive_outside_recent_window(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    mine = board.append(2, "a", "propose", {"answer_summary": "Red", "method": "M"})
    board.append(2, "b", "critique", {"target": mine.id, "about": "method", "points": "OLD-CRITIQUE"}, refs=[mine.id])
    for i in range(20):
        board.append(3, "c", "pass", {})
    view = view_for("a", 4, store, board, ws, view_entries=5)
    assert "Entries that respond to you" in view
    assert "OLD-CRITIQUE" in view


def test_render_entry_and_who(community):
    store, conn = community
    who = make_who({"a": store.public("a"), "z": None})
    assert who("a") == "Alpha (a)" and who("z") == "z (z)" and who(SYSTEM) == "system"
    text = render_entry(Entry(7, "r1", 2, "a", "critique", {"target": 3, "points": "p"}, [3], "all", "t"), who)
    assert text.splitlines()[0] == "#7 [r2] Alpha (a) critique → #3"
    assert "  points: p" in text


def test_shows_what_i_have_proposed_and_adopted(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    mine = board.append(1, "a", "propose", {"answer_summary": "Red", "method": "M"})
    theirs = board.append(1, "b", "propose", {"answer_summary": "Blue", "method": "M"})
    board.append(2, "a", "adopt", {"target": theirs.id, "reason": "r"}, refs=[theirs.id])
    board.append(3, "a", "adopt", {"target": theirs.id, "reason": "again"}, refs=[theirs.id])
    view = view_for("a", 4, store, board, ws)
    assert f"# Your positions\n- You proposed #{mine.id} (r1)\n- You adopted #{theirs.id} (r2)\n" in view + "\n"
    assert "# Your positions" not in view_for("c", 4, store, board, ws)


def test_view_shows_own_latest_journals_but_never_anyone_elses(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    for n, run_id in enumerate(("20260101-000000-aaaa", "20260102-000000-bbbb", "20260103-000000-cccc")):
        store.write_journal_file("a", run_id, f"JOURNAL-A-{n}\n")
    store.write_journal_file("b", "20260103-000000-cccc", "JOURNAL-B\n")
    view = view_for("a", 2, store, board, ws)
    assert "## journal/20260103-000000-cccc.md\nJOURNAL-A-2" in view and "JOURNAL-A-1" in view
    assert "JOURNAL-A-0" not in view
    assert "JOURNAL-B" not in view
    assert "JOURNAL-A" not in view_for("b", 2, store, board, ws)


def test_view_shows_norms_when_present(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    assert "# Community norms" not in view_for("a", 2, store, board, ws)
    (store.root / "norms.md").write_text("1. Cite your sources.\n")
    assert "# Community norms\n1. Cite your sources." in view_for("a", 2, store, board, ws)


def test_member_lines_show_reputation(tmp_path, community, make_run):
    from bikeshed.reviews import add_review

    store, conn, board, ws = setup(tmp_path, community, make_run)
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'r1'")
    add_review(conn, run_id="r1", reviewer="a", reviewee="b", more_of="m", less_of="l", private_note="p",
               evidence=[1], overall=4, overall_reason="r")
    view = view_for("a", 2, store, board, ws)
    assert "- Bea (b) — Bea headline · ★4.0 (1 review)" in view
    assert "- Cyd (c) — Cyd headline · no reviews yet" in view


def test_member_lines_show_pronouns_when_stated(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.set_public("b", "Bea", "Bea headline", "I am Bea.", pronouns="she/her")
    view = view_for("a", 2, store, board, ws)
    assert "- Bea (b) · she/her — Bea headline · no reviews yet" in view
    assert "- Cyd (c) — Cyd headline · no reviews yet" in view


def test_view_shows_methods_from_earlier_runs(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15, library=["- #9 in run old by Bea (b) (propose); goal: G\n  method: LIB-METHOD"])
    view = build_view(inputs, budget_chars=50_000)
    assert f"{METHODS_HEADER}\n- #9 in run old" in view
    assert view.index("# Methods from earlier runs") < view.index("# Workspace files")
    assert "# Methods from earlier runs" not in view_for("a", 2, store, board, ws)


def test_member_lines_show_dimension_scores(tmp_path, community, make_run):
    from bikeshed.reviews import Reputation

    store, conn, board, ws = setup(tmp_path, community, make_run)
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15)
    inputs = replace(inputs, dimension_scores={"b": [("Care", Reputation(4.5, 2)), ("Candour", Reputation(None, 0))]})
    view = build_view(inputs, budget_chars=50_000)
    assert "- Bea (b) — Bea headline · no reviews yet · Care 4.5 · 0 Scorer points\n" in view


def test_control_agents_see_no_earlier_journals_and_never_their_status(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    store.write_journal_file("d", "20260101-000000-aaaa", "JOURNAL-D-OLD\n")
    store.write_journal_file("a", "20260101-000000-aaaa", "JOURNAL-A-OLD\n")
    view = view_for("d", 2, store, board, ws)
    assert "JOURNAL-D-OLD" not in view and "control" not in view.lower()
    assert "JOURNAL-A-OLD" in view_for("a", 2, store, board, ws)


def test_views_reads_and_targets_hide_other_agents_private_entries(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    message = board.append(2, "a", "private_message", {"to": "b", "text": "SECRET-A-TO-B"}, to="b", visible_to=["a", "b"])
    board.append(2, "a", "think", {"text": "SECRET-THOUGHT-A"}, visible_to=["a"])
    assert "SECRET-A-TO-B" in view_for("a", 3, store, board, ws)
    assert "SECRET-A-TO-B" in view_for("b", 3, store, board, ws)
    view_c = view_for("c", 3, store, board, ws)
    assert "SECRET-A-TO-B" not in view_c and "SECRET-THOUGHT-A" not in view_c
    assert "SECRET-THOUGHT-A" not in view_for("b", 3, store, board, ws)
    inputs = collect_inputs(agent_id="c", goal="Pick a colour", round=3, board=board, store=store,
                            workspace=ws, view_entries=15)
    read = make_reader(ws, inputs.entries, make_who(inputs.profiles))
    assert "SECRET" not in read(ReadEntry(type="read_entry", id=message.id))
    with pytest.raises(ActionError, match="does not exist"):
        make_validator(inputs.entries, ["a", "b", "c"])(
            Critique(type="critique", target=message.id, about="answer", points="p"))


def test_process2_view_counts_rounds_and_shows_own_think_notes_only(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    board.append(1, "a", "think", {"text": "FIRST-THOUGHT"}, visible_to=["a"])
    board.append(2, "a", "think", {"text": "SECOND-THOUGHT"}, visible_to=["a"])
    board.append(2, "b", "think", {"text": "B-THOUGHT"}, visible_to=["b"])
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=3, board=board, store=store, workspace=ws,
                            view_entries=15, max_rounds=12)
    view = build_view(inputs, budget_chars=50_000)
    assert "# Round 3 of 12" in view
    assert ("# Your private notes this run (only you can see these)\n## round 1\nFIRST-THOUGHT\n\n"
            "## round 2\nSECOND-THOUGHT") in view
    assert view.index("Your private notes this run") < view.index("# Community members")
    assert "B-THOUGHT" not in view
    assert "think" not in [e.type for e in inputs.entries]
    assert "# Round 3\n" in view_for("a", 3, store, board, ws)  # Process 1 header unchanged


def test_member_lines_show_scorer_points(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES ('r1', ?, 9, 'x', 't')", [("b",), ("b",), ("c",)])
    view = view_for("a", 2, store, board, ws)
    assert "- Bea (b) — Bea headline · no reviews yet · 2 Scorer points" in view
    assert "- Cyd (c) — Cyd headline · no reviews yet · 1 Scorer point\n" in view
    assert "- Alpha (a) — Alpha headline · no reviews yet · 0 Scorer points" in view


def test_rejected_calls_leave_the_open_calls_and_show_their_feedback(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    spent = board.append(2, "b", "call_consensus", {"synthesis": "Poll first", "refs": [], "credits": []})
    board.append(2, SYSTEM, "scorer_rejected", {"call_id": spent.id, "feedback": "No colour named."}, refs=[spent.id])
    open_call = board.append(3, "c", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": []})
    view = view_for("a", 3, store, board, ws)
    opens = view.split("# Open calls for consensus\n")[1].split("\n\n")[0]
    assert f"#{open_call.id} by Cyd (c)" in opens and f"#{spent.id} " not in opens
    assert f"# Calls the Scorer rejected\n- #{spent.id} by Bea (b): No colour named." in view


def test_render_entry_hides_harness_flags():
    from bikeshed.board import Entry
    from bikeshed.view import render_entry

    entry = Entry(7, "r1", 2, "a", "private_message", {"to": "b", "text": "hi", "pre_action": True}, [], "b", "t")
    text = render_entry(entry, lambda agent_id: agent_id)
    assert "text: hi" in text and "pre_action" not in text


def test_pre_action_entries_do_not_push_actions_out_of_the_board_window(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    first = board.append(2, "b", "propose", {"answer_summary": "TURN-ACTION-B", "method": "m"})
    for i in range(6):
        w = board.append(2, "c", "write_file", {"path": "p.md", "version": i + 1, "note": f"PRE-{i}", "pre_action": True})
        board.append(2, SYSTEM, "artifact_changed", {"agent": "c", "path": "p.md", "to_version": i + 1,
                                                    "pre_action": True}, refs=[w.id])
    board.append(2, "c", "question", {"target": first.id, "text": "TURN-ACTION-C"}, refs=[first.id])
    view = view_for("a", 3, store, board, ws, view_entries=2)
    assert "TURN-ACTION-B" in view and "TURN-ACTION-C" in view  # two turn actions, however many writes between
    assert "PRE-5" in view  # pre-actions inside the window are still shown


def test_under_the_stood_round_rule_a_call_shows_whether_it_has_stood(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    old = board.append(2, "b", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": []})
    new = board.append(3, "c", "call_consensus", {"synthesis": "Red", "refs": [], "credits": []})
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=3, board=board, store=store, workspace=ws,
                            view_entries=15, close_rule="stood_round")
    view = build_view(inputs, budget_chars=50_000)
    assert f"#{old.id} by" in view and "stood a round: yes" in view
    assert f"#{new.id} by" in view and "stood a round: no" in view
    assert "critiqued:" not in view


BRIEFS = assign(BriefSpec("SHARED-FACT", ("SHARED-RULE",), (("PRIV-ONE", ("CONS-ONE",)), ("PRIV-TWO", ()))),
                ["a", "b", "c"], seed=3)


def briefed_view(agent_id, store, board, ws, briefs):
    inputs = collect_inputs(agent_id=agent_id, goal="Pick a colour", round=2, board=board, store=store,
                            workspace=ws, view_entries=15, briefs=briefs)
    return build_view(inputs, budget_chars=50_000)


def test_view_shows_shared_brief_own_private_brief_and_setup(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    view = briefed_view(BRIEFS.private[0].agent_id, store, board, ws, BRIEFS)
    assert "# Shared brief (every member sees this)\nSHARED-FACT\n- SHARED-RULE" in view
    assert "# Your private brief (only you can see this)\nPRIV-ONE\n- CONS-ONE" in view
    assert SETUP in view and "p1.c1" not in view and "s.c1" not in view
    assert view.index("# Shared brief") < view.index("# Your private brief") < view.index(SETUP) \
        < view.index("# Current goal")


def test_an_agent_without_a_private_brief_is_told_so(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    leftover = next(a for a in "abc" if BRIEFS.for_agent(a) is None)
    view = briefed_view(leftover, store, board, ws, BRIEFS)
    assert SETUP_NONE in view and "# Your private brief" not in view
    assert "PRIV-ONE" not in view and "PRIV-TWO" not in view and "SHARED-FACT" in view


def test_a_shared_only_brief_has_no_setup_line(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    view = briefed_view("a", store, board, ws, assign(BriefSpec("SHARED-FACT"), ["a", "b", "c"], seed=3))
    assert "SHARED-FACT" in view and "private brief" not in view


def test_private_briefs_never_reach_another_agent(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    for agent in "abc":
        view = briefed_view(agent, store, board, ws, BRIEFS)
        for other in BRIEFS.private:
            if other.agent_id != agent:
                assert other.text not in view and all(c.text not in view for c in other.constraints)


def test_no_briefs_leaves_the_view_unchanged(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    assert briefed_view("a", store, board, ws, EMPTY) == view_for("a", 2, store, board, ws)


def test_a_control_sees_itself_as_new_while_others_see_its_standing(tmp_path, community, make_run):
    from bikeshed.reviews import add_review

    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'r1'")
    add_review(conn, run_id="r1", reviewer="a", reviewee="d", more_of="m", less_of="l", private_note="p",
               evidence=[1], overall=5, overall_reason="r")
    conn.execute("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                 "VALUES ('r1', 'd', 9, 'x', 't')")
    own = view_for("d", 2, store, board, ws)
    assert "- Dee (d) — Dee headline · no reviews yet · 0 Scorer points" in own
    others = view_for("a", 2, store, board, ws)
    assert "- Dee (d) — Dee headline · ★5.0 (1 review) · 1 Scorer point" in others
    inputs = collect_inputs(agent_id="d", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15)
    assert inputs.dimension_scores["d"] == [] and "d" not in inputs.points
