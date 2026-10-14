from observer_helpers import add_briefs, add_conflict, add_entry, add_run

from bikeshed.briefs import Brief, Constraint, RunBriefs
from bikeshed.observer import briefs as pb
from bikeshed.observer.metrics import closed_runs
from bikeshed.observer.report import render_run

BRIEFS = RunBriefs(Brief(0, None, "Menu list.", (Constraint("s.c1", "Choose from the list."),)),
                   (Brief(1, "b", "Bea knows the budget.", (Constraint("p1.c1", "Spend at most $15."),)),
                    Brief(2, "c", "Cyd saw the weather.")))


def who(agent_id):
    return {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get(agent_id, agent_id)


def briefed_run(conn, ended="budget"):
    add_run(conn, "r1", category="distributed", ended=ended)
    add_briefs(conn, "r1", BRIEFS)
    call = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "Steak", "refs": [], "credits": []}, round=2)
    bea = add_entry(conn, "r1", "b", "propose", {"answer_summary": "Soup", "method": "m"}, round=1)
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": call, "feedback": "…", "named": ["b"]}, round=3)
    add_conflict(conn, "r1", call, "p1.c1")
    add_conflict(conn, "r1", call, "s.c1")
    add_entry(conn, "r1", "a", "critique", {"target": bea, "about": "answer", "points": "Bea, what is it?"},
              refs=[bea], round=4)
    add_entry(conn, "r1", "c", "propose", {"answer_summary": "Pasta", "method": "m"}, round=4)
    add_entry(conn, "r1", "a", "critique", {"target": bea, "about": "answer", "points": "later"}, refs=[bea], round=5)
    return call


def test_callouts_join_conflicts_to_constraints_and_holders(community):
    _, conn = community
    call = briefed_run(conn)
    assert pb.callouts(conn, "r1") == [pb.Callout(call, 3, "p1.c1", "Spend at most $15.", "b"),
                                       pb.Callout(call, 3, "s.c1", "Choose from the list.", None)]


def test_reactions_are_next_round_entries_that_refer_to_the_named_member(community):
    _, conn = community
    briefed_run(conn)
    [reaction] = pb.reactions(conn, closed_runs(conn)[0], who)
    assert (reaction.round, reaction.named) == (4, "b")
    assert len(reaction.entries) == 1 and "Bea, what is it?" in reaction.entries[0]


def test_unbriefed_runs_have_no_section(community):
    _, conn = community
    add_run(conn, "r2")
    assert pb.section(conn, closed_runs(conn)[0], who, []) is None


def test_section_records_whether_the_run_closed_by_consensus(community):
    _, conn = community
    briefed_run(conn, ended="consensus")
    assert pb.section(conn, closed_runs(conn)[0], who, []).closed_by_consensus is True


def test_the_run_report_shows_callouts_and_reactions(community):
    from test_observer_report import section

    _, conn = community
    briefed_run(conn)
    run = closed_runs(conn)[0]
    text = render_run(section(run=run, briefs=pb.section(conn, run, who, [])))
    assert "## Briefs (Process 4)" in text
    assert "“Spend at most $15.” — Bea (b)'s private brief" in text
    assert "“Choose from the list.” — the shared brief" in text
    assert "Closed by consensus after the call-outs: no" in text
    assert "r4, after Bea (b) was named" in text and "Bea, what is it?" in text
