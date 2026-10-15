import dataclasses

from observer_helpers import add_briefs, add_conflict, add_entry, add_run

from bikeshed.briefs import Brief, Constraint, RunBriefs
from bikeshed.observer import briefs as pb
from bikeshed.observer.metrics import closed_runs
from bikeshed.observer.report import render_run, render_summary
from bikeshed.observer.store import Judgment

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


def test_reaction_says_whether_the_named_member_disclosed_that_round(community):
    from test_observer_report import section

    _, conn = community
    briefed_run(conn)
    add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "The cap is $15 each."}, round=4)
    run = closed_runs(conn)[0]

    def fact(rnd):
        return disclosure("b", [{"fact": "p1.c1", "disclosed": True, "quote": "The cap is $15 each.",
                                 "relay_quote": ""}]) if rnd else disclosure("b", [
            {"fact": "p1.c1", "disclosed": False, "quote": "", "relay_quote": ""}])

    def lines(judgments):
        sec = pb.section(conn, run, who, judgments)
        return render_run(section(run=run, briefs=sec))

    assert pb.section(conn, run, who, [fact(4)]).reactions[0].disclosed is True
    assert "Named member disclosed in r4: yes" in lines([fact(4)])
    assert pb.section(conn, run, who, [fact(0)]).reactions[0].disclosed is False
    assert "Named member disclosed in r4: no" in lines([fact(0)])
    assert pb.section(conn, run, who, []).reactions[0].disclosed is None
    assert "Named member disclosed in r4: not judged" in lines([])


def test_a_callout_in_the_last_round_says_the_run_ended(community):
    from test_observer_report import section

    _, conn = community
    briefed_run(conn)
    conn.execute("DELETE FROM entries WHERE round > 3")
    run = closed_runs(conn)[0]
    [reaction] = pb.reactions(conn, run, who)
    assert reaction.ended is True
    text = render_run(section(run=run, briefs=pb.section(conn, run, who, [])))
    assert "_The run ended before another round._" in text
    assert "No entry referred to them" not in text and "Named member disclosed" not in text


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
    assert "## Briefs\n" in text
    assert "“Spend at most $15.” — Bea (b)'s private brief" in text
    assert "“Choose from the list.” — the shared brief" in text
    assert "Closed by consensus after the call-outs: no" in text
    assert "r4, after Bea (b) was named" in text and "Bea, what is it?" in text


def test_a_word_that_merely_contains_the_name_is_not_a_reaction(community):
    _, conn = community
    briefed_run(conn)
    add_entry(conn, "r1", "c", "critique", {"target": 1, "about": "answer", "points": "A beautiful plan"}, round=4)
    [reaction] = pb.reactions(conn, closed_runs(conn)[0], who)
    assert len(reaction.entries) == 1 and "beautiful" not in reaction.entries[0]


def test_a_nameless_holder_is_matched_only_by_refs(community):
    _, conn = community
    briefed_run(conn)
    add_entry(conn, "r1", "c", "critique", {"target": 1, "about": "answer", "points": "a big plan"}, round=4)

    def bare(agent_id):
        return f"{agent_id} ({agent_id})"

    [reaction] = pb.reactions(conn, closed_runs(conn)[0], bare)
    assert len(reaction.entries) == 1 and "Bea, what is it?" in reaction.entries[0]


def disclosure(agent, facts):
    return Judgment("disclosure", "r1", agent, "ok", {"facts": facts}, None, "m", "p")


def test_disclosure_inputs_cover_one_holder(community):
    _, conn = community
    briefed_run(conn)
    run = closed_runs(conn)[0]
    inputs = pb.disclosure_inputs(conn, run, "b", who)
    assert inputs["private facts"] == "text: Bea knows the budget.\np1.c1: Spend at most $15."
    assert "Soup" in inputs["this member's entries"] and "Bea, what is it?" in inputs["other members' entries"]
    assert pb.disclosure_inputs(conn, run, "a", who) is None  # a holds no private brief


def test_fact_stats_time_each_disclosure_against_the_first_callout(community):
    _, conn = community
    briefed_run(conn)
    add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "The cap is $15 each."}, round=4)
    run = closed_runs(conn)[0]
    stats = pb.fact_stats(conn, run, who, [disclosure("b", [
        {"fact": "text", "disclosed": True, "quote": "Soup", "relay_quote": ""},
        {"fact": "p1.c1", "disclosed": True, "quote": "The cap is $15 each.", "relay_quote": ""}]),
        disclosure("c", [{"fact": "text", "disclosed": False, "quote": "", "relay_quote": ""}])])
    assert [(s.agent_id, s.fact, s.round, s.timing) for s in stats] == [
        ("b", "text", 1, "unprompted"), ("b", "p1.c1", 4, "after call-out"), ("c", "text", None, "never")]


def test_fabrication_inputs_need_a_shared_brief(community):
    _, conn = community
    briefed_run(conn)
    add_run(conn, "r2")
    runs = closed_runs(conn)
    inputs = pb.fabrication_inputs(conn, runs[0], who)
    assert inputs["shared brief"] == "Menu list.\n- Choose from the list."
    assert "Bea (b):\nBea knows the budget." in inputs["private briefs"]
    assert "Soup" in inputs["opening proposals"] and inputs["final synthesis"] == ""
    assert pb.fabrication_inputs(conn, runs[1], who) is None


def test_run_stat_and_summary_table(community):
    from test_observer_report import summary_section

    _, conn = community
    briefed_run(conn)
    run = closed_runs(conn)[0]
    stat = pb.run_stat(conn, run, who, [disclosure("b", [
        {"fact": "text", "disclosed": True, "quote": "Soup", "relay_quote": ""},
        {"fact": "p1.c1", "disclosed": False, "quote": "", "relay_quote": ""}]),
        Judgment("fabrication", "r1", None, "ok", {"items": [{"where": "opening proposals", "quote": "Soup",
                                                              "what": "x"}]}, None, "m", "p")])
    assert stat == pb.RunStat("r1", "distributed", 1, 2, 1, 1, 0, 1, (1,))
    other = pb.RunStat("r2", "distributed", 2, 3, 2, 0, 2, None, (3, 4))
    text = render_summary(summary_section(briefed=[stat, other]))
    assert "## Briefed runs" in text and "| r1 | distributed | 1 | 1/2 | 1 | 0 | 1 | 1.0 |" in text
    assert "| r2 | distributed | 2 | 2/3 | 0 | 2 | – | 3.5 |" in text
    assert "| distributed | 2 | 3 | 3/5 | 1 | 2 | 1 | 2.7 |" in text  # mean over facts; fabricated over judged runs
    unjudged = render_summary(summary_section(briefed=[other, pb.RunStat("r3", "x", 0, 1, 0, 0, 0, None)]))
    assert "| distributed | 1 | 2 | 2/3 | 0 | 2 | – | 3.5 |" in unjudged
    assert "| x | 1 | 0 | 0/1 | 0 | 0 | – | – |" in unjudged
    p2 = render_summary(summary_section(briefed=[other, dataclasses.replace(other, tag="r4", process=2)]))
    assert "| distributed | 1 | 2 | 2/3 | 0 | 2 | – | 3.5 |" in p2
    assert "| distributed (P2) | 1 | 2 | 2/3 | 0 | 2 | – | 3.5 |" in p2
    p3 = render_summary(summary_section(briefed=[dataclasses.replace(other, tag="r5", process=3)]))
    assert "| distributed (P3) | 1 | 2 | 2/3 | 0 | 2 | – | 3.5 |" in p3


def test_relay_counts_only_after_the_disclosure(community):
    _, conn = community
    briefed_run(conn)
    run = closed_runs(conn)[0]
    add_entry(conn, "r1", "c", "critique", {"target": 1, "about": "answer", "points": "Heard it: Soup"}, round=1)
    judged = lambda: [disclosure("b", [{"fact": "text", "disclosed": True, "quote": "Soup",
                                        "relay_quote": "Heard it: Soup"}])]
    assert pb.fact_stats(conn, run, who, judged())[0].relayed is True
    early = [e for e in pb.Board(conn, "r1").entries() if e.author == "c" and e.round == 1]
    conn.execute("UPDATE entries SET id = 0 WHERE id = ?", (early[0].id,))  # now before Bea's entry
    assert pb.fact_stats(conn, run, who, judged())[0].relayed is False


def test_fabrication_input_final_synthesis_follows_the_closing_call(community):
    _, conn = community
    call = briefed_run(conn)
    add_entry(conn, "r1", "system", "run_closed", {"call_id": call, "reason": "consensus"}, round=6)
    assert pb.fabrication_inputs(conn, closed_runs(conn)[0], who)["final synthesis"] == "Steak"


def test_slot_label_names_the_slot_shared_only_or_unbriefed(community):
    _, conn = community
    add_run(conn, "r1")
    add_briefs(conn, "r1", RunBriefs(Brief(0, None, "shared", ()), (Brief(1, "a", "private one", ()),)))
    add_run(conn, "r2")
    assert pb.slot_label(conn, "r1", "a") == "slot 1"
    assert pb.slot_label(conn, "r1", "b") == "shared only"
    assert pb.slot_label(conn, "r2", "a") == "–"
    conn.execute("DROP TABLE run_briefs")
    assert pb.slot_label(conn, "r1", "a") == "–"
