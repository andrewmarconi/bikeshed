from dataclasses import replace

from aiswarm.board import SYSTEM, Board, Entry
from aiswarm.view import build_view, collect_inputs, make_who, render_entry
from aiswarm.workspace import Workspace


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
    from aiswarm.reviews import add_review

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
    assert "# Methods from earlier runs\n- #9 in run old" in view
    assert view.index("# Methods from earlier runs") < view.index("# Workspace files")
    assert "# Methods from earlier runs" not in view_for("a", 2, store, board, ws)


def test_member_lines_show_dimension_scores(tmp_path, community, make_run):
    from aiswarm.reviews import Reputation

    store, conn, board, ws = setup(tmp_path, community, make_run)
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15)
    inputs = replace(inputs, dimension_scores={"b": [("Care", Reputation(4.5, 2)), ("Candour", Reputation(None, 0))]})
    view = build_view(inputs, budget_chars=50_000)
    assert "- Bea (b) — Bea headline · no reviews yet · Care 4.5\n" in view


def test_control_agents_see_no_earlier_journals_and_never_their_status(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    store.write_journal_file("d", "20260101-000000-aaaa", "JOURNAL-D-OLD\n")
    store.write_journal_file("a", "20260101-000000-aaaa", "JOURNAL-A-OLD\n")
    view = view_for("d", 2, store, board, ws)
    assert "JOURNAL-D-OLD" not in view and "control" not in view.lower()
    assert "JOURNAL-A-OLD" in view_for("a", 2, store, board, ws)
