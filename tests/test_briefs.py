import sqlite3

import pytest

from bikeshed.briefs import (
    EMPTY, Brief, BriefSpec, Constraint, RunBriefs, assign, callout, load, load_conflicts, quotes_constraint, save,
)

SPEC = BriefSpec("Saturday.", ("Use only the pantry.",),
                 (("You packed it.", ("At most 6.",)), ("Sam comes.", ("No nuts.", "No dairy.")), ("Rain likely.", ())))


def test_assign_numbers_constraints_and_seeds_the_order():
    a = assign(SPEC, ["a", "b", "c", "d"], seed=7)
    assert a.shared == Brief(0, None, "Saturday.", (Constraint("s.c1", "Use only the pantry."),))
    assert [b.slot for b in a.private] == [1, 2, 3]
    assert [c.id for c in a.private[1].constraints] == ["p2.c1", "p2.c2"]
    assert len({b.agent_id for b in a.private}) == 3
    assert a == assign(SPEC, ["a", "b", "c", "d"], seed=7)
    assert any(assign(SPEC, ["a", "b", "c", "d"], seed=s) != a for s in range(1, 20))


def test_assign_refuses_more_briefs_than_agents():
    with pytest.raises(ValueError, match="the goal has 3 private briefs but the run has 2 agents"):
        assign(SPEC, ["a", "b"], seed=1)


def test_holder_for_agent_and_constraint_order():
    a = assign(SPEC, ["a", "b", "c"], seed=7)
    second = a.private[1]
    assert a.for_agent(second.agent_id) == second
    assert a.holder("p2.c2") == second.agent_id and a.holder("s.c1") is None
    assert [c.id for c in a.constraints] == ["s.c1", "p1.c1", "p2.c1", "p2.c2"]
    assert not a.empty and EMPTY.empty and RunBriefs(Brief(0, None, "x")).constraints == ()


def test_save_and_load_round_trip(community, make_run):
    _, conn = community
    make_run(conn, "r1", ("a", "b", "c"))
    a = assign(BriefSpec("Ünïcode “quotes”.", (), (("x", ("🥜 none",)),)), ["a", "b", "c"], seed=7)
    save(conn, "r1", a)
    assert load(conn, "r1") == a
    assert load(conn, "other") == EMPTY


def test_conflicts_load_in_insert_order(community, make_run):
    _, conn = community
    make_run(conn, "r1")
    conn.executemany("INSERT INTO scorer_conflicts (run_id, call_id, constraint_id) VALUES ('r1', ?, ?)",
                     [(9, "p1.c1"), (9, "s.c1"), (14, "p1.c1")])
    assert load_conflicts(conn, "r1") == [(9, "p1.c1"), (9, "s.c1"), (14, "p1.c1")]


def test_readers_of_an_older_store_get_no_briefs():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert load(conn, "r1") == EMPTY and load_conflicts(conn, "r1") == []


def test_quotes_constraint_ignores_case_and_punctuation():
    a = assign(BriefSpec("x", ("Use only the pantry.",), (("t", ("Sam has a nut allergy.",)),)), ["a"], seed=1)
    assert quotes_constraint("Remember: SAM has a nut-allergy!", a)
    assert quotes_constraint("use ONLY the pantry", a)
    assert not quotes_constraint("It does not say which day.", a)


def test_callout_names_each_holder_once_in_member_order():
    briefs = RunBriefs(Brief(0, None, "S", (Constraint("s.c1", "Rule."),)),
                       (Brief(1, "c", "x", (Constraint("p1.c1", "C1."), Constraint("p1.c2", "C2."))),
                        Brief(2, "a", "y", (Constraint("p2.c1", "C3."),))))
    who = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get
    text, named = callout("No dish is named.", ["p1.c2", "s.c1", "p2.c1", "p1.c1"], briefs, ["a", "b", "c"], who)
    assert named == ["a", "c"]
    assert text == ("No dish is named. "
                    "The synthesis conflicts with a constraint in Alpha (a)'s private brief. "
                    "The synthesis conflicts with a constraint in Cyd (c)'s private brief. "
                    "The synthesis conflicts with a constraint in the shared brief.")
    assert callout(None, ["s.c1"], briefs, ["a", "b", "c"], who) == (
        "The synthesis conflicts with a constraint in the shared brief.", [])
