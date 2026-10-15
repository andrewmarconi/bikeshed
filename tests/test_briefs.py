import sqlite3

import pytest

from bikeshed.briefs import (
    EMPTY, Brief, BriefSpec, Constraint, RunBriefs, assign, callout, load, load_conflicts, quotes_constraint, reveal, save,
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
    assert not quotes_constraint("use ONLY the pantry", a)  # shared constraints are visible to everyone
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


def test_reveal_lists_every_brief_and_each_callout():
    briefs = RunBriefs(Brief(0, None, "Shared.", (Constraint("s.c1", "Use the list."),)),
                       (Brief(1, "b", "One.", (Constraint("p1.c1", "C-one."),)), Brief(2, "a", "Two.")))
    who = {"a": "Alpha (a)", "b": "Bea (b)"}.get
    assert reveal(briefs, [(12, "p1.c1"), (12, "s.c1")], who) == (
        "## The briefs this run\n\n### Shared brief\nShared.\n- Use the list.\n\n"
        "### Bea (b)'s private brief\nOne.\n- C-one.\n\n### Alpha (a)'s private brief\nTwo.\n\n"
        "### Scorer call-outs\n- call #12: \"C-one.\" (Bea (b)'s private brief)\n"
        "- call #12: \"Use the list.\" (the shared brief)")
    assert reveal(EMPTY, [], who) == ""


FIVE = BriefSpec("Shared.", (), tuple((f"Brief {i}.", ()) for i in range(1, 6)))
SIX_AGENTS = ["a", "b", "c", "d", "e", "f"]


def holders(briefs):
    return {b.agent_id for b in briefs.private}


def test_protected_members_always_hold_a_brief_when_there_are_too_few():
    unprotected_misses = 0
    for seed in range(200):
        briefs = assign(FIVE, SIX_AGENTS, seed, protected=("e", "f"))
        assert {"e", "f"} <= holders(briefs), seed
        unprotected_misses += not {"e", "f"} <= holders(assign(FIVE, SIX_AGENTS, seed))
    assert unprotected_misses > 0  # the plain shuffle does leave them out; the guard is what fixes it


def test_protection_changes_nothing_when_everyone_holds_a_brief():
    six = BriefSpec("Shared.", (), tuple((f"Brief {i}.", ()) for i in range(1, 7)))
    for seed in range(50):
        assert assign(six, SIX_AGENTS, seed, protected=("e", "f")) == assign(six, SIX_AGENTS, seed)


def test_protection_keeps_the_assignment_when_protected_members_already_hold_briefs():
    for seed in range(200):
        plain = assign(FIVE, SIX_AGENTS, seed)
        if {"e", "f"} <= holders(plain):
            assert assign(FIVE, SIX_AGENTS, seed, protected=("e", "f")) == plain
