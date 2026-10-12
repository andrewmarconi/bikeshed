from observer_helpers import add_entry, add_run

from bikeshed.observer.metrics import closed_runs
from bikeshed.observer.scoring import (
    Award, RulingRow, SelfCredit, awards, combine_self_credit, rulings, self_credit,
)


def _points(conn, *rows):
    conn.executemany("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                     "VALUES (?, ?, ?, ?, 't')", rows)


def scored_run(conn):
    """r1: a's call is rejected, then b's call (crediting b twice, a, and a credit with no id) is accepted."""
    add_run(conn, "r1", ended="consensus")
    first = add_entry(conn, "r1", "a", "call_consensus",
                      {"synthesis": "Fruit", "credits": [{"agent_id": "c", "contribution": "c's idea"}]})
    add_entry(conn, "r1", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Kiwi", "credits": [
        {"agent_id": "b", "contribution": "me"}, {"agent_id": "b", "contribution": "me again"},
        {"agent_id": "a", "contribution": "kiwi"}, {"contribution": "nobody"}]})
    add_entry(conn, "r1", "system", "scorer_accepted", {"call_id": second}, refs=[second])
    add_entry(conn, "r1", "system", "run_closed", {"reason": "consensus", "call_id": second})
    _points(conn, ("r1", "b", second, "me"), ("r1", "a", second, "kiwi"))
    return first, second


def test_rulings_in_board_order(community):
    store, conn = community
    first, second = scored_run(conn)
    assert rulings(conn, "r1") == [RulingRow(first, "a", False, "Which fruit?"), RulingRow(second, "b", True, "")]


def test_awards_say_who_earned_what_for_which_call(community):
    store, conn = community
    first, second = scored_run(conn)
    assert awards(conn, "r1") == [Award("b", second, "me"), Award("a", second, "kiwi")]


def test_self_credit_counts_each_call_once(community):
    store, conn = community
    scored_run(conn)
    got = self_credit(conn, closed_runs(conn)[0])
    assert got == {"a": SelfCredit(1, 0), "b": SelfCredit(1, 1), "c": SelfCredit(0, 0)}


def test_combine_self_credit():
    assert combine_self_credit([{"b": SelfCredit(1, 1)}, {"a": SelfCredit(2, 0), "b": SelfCredit(1, 0)}]) == \
        {"a": SelfCredit(2, 0), "b": SelfCredit(2, 1)}


def test_a_run_without_rulings_and_an_older_store(community):
    store, conn = community
    add_run(conn, "r1")
    conn.execute("DROP TABLE scorer_points")  # a store from before the Scorer
    assert rulings(conn, "r1") == [] and awards(conn, "r1") == []
