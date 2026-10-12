from observer_helpers import add_entry, add_run

from aiswarm.observer.metrics import closed_runs
from aiswarm.observer.process2 import (
    OrderStat, Participation, RecurringPair, combine_order, combine_participation, pm_network, poll_participation,
    recurring_pairs, speaking_order,
)


def pm(conn, run_id, sender, recipient, round=1):
    return add_entry(conn, run_id, sender, "private_message", {"to": recipient, "text": "psst"}, round=round,
                     visible_to=[sender, recipient], to=recipient)


def think(conn, run_id, agent, round):
    return add_entry(conn, run_id, agent, "think", {"text": "hm"}, round=round, visible_to=[agent])


def test_pm_network_counts_who_wrote_to_whom(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    pm(conn, "r1", "a", "b")
    pm(conn, "r1", "a", "b", round=2)
    pm(conn, "r1", "b", "a")
    add_entry(conn, "r1", "c", "propose", {"answer_summary": "x", "method": "m"})
    assert pm_network(conn, closed_runs(conn)[0]) == {("a", "b"): 2, ("b", "a"): 1}


def test_recurring_pairs_are_pairs_in_two_or_more_runs(community):
    store, conn = community
    for run_id in ("r1", "r2", "r3"):
        add_run(conn, run_id, process=2)
    pm(conn, "r1", "a", "b")
    pm(conn, "r2", "a", "b")
    pm(conn, "r2", "a", "b")
    pm(conn, "r3", "c", "a")
    assert recurring_pairs(conn, closed_runs(conn)) == [RecurringPair("a", "b", 2, 3)]


def test_poll_answers_come_after_the_react_turn(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    call = add_entry(conn, "r1", "a", "call_consensus", {"synthesis": "s", "credits": []}, round=2)
    think(conn, "r1", "b", 2)
    add_entry(conn, "r1", "b", "vote", {"target": call, "stance": "support", "reason": "r"}, refs=[call], round=2)
    add_entry(conn, "r1", "c", "pass", {}, round=2)
    add_entry(conn, "r1", "c", "vote", {"target": call, "stance": "object", "reason": "r"}, refs=[call], round=2)
    add_entry(conn, "r1", "a", "abstain", {"target": call, "reason": "r"}, refs=[call], round=2)
    conn.execute("INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) "
                 "VALUES ('r1', 3, 'b', 'poll:model_error', 'x', 't')")
    got = poll_participation(conn, closed_runs(conn)[0])
    assert got["a"] == Participation(abstained=1)
    assert got["b"] == Participation(failed=1, unprompted=1)  # its vote was its react turn
    assert got["c"] == Participation(objected=1)
    assert got["b"].polled == 1 and got["a"].polled == 1


def test_speaking_order_skips_the_blind_first_round(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    first = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"}, round=1)
    add_entry(conn, "r1", "b", "adopt", {"target": first, "reason": "r"}, refs=[first], round=1)
    add_entry(conn, "r1", "c", "pass", {}, round=1)
    for agent in ("c", "a", "b"):
        think(conn, "r1", agent, 2)
    lead = add_entry(conn, "r1", "c", "propose", {"answer_summary": "y", "method": "m"}, round=2)
    second = add_entry(conn, "r1", "a", "refine", {"target": lead, "change": "z"}, refs=[lead], round=2)
    add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "s", "credits": []}, refs=[lead], round=2)
    add_entry(conn, "r1", "a", "adopt", {"target": second, "reason": "mine"}, refs=[second], round=3)  # own entry
    # round 2: c (taken up by a's refine and b's call), a, b; round 3: a alone, at position 1
    assert speaking_order(conn, closed_runs(conn)[0]) == [OrderStat(1, 2, 1), OrderStat(2, 1, 0), OrderStat(3, 1, 0)]


def test_combining_runs_sums_by_agent_and_position():
    parts = [{"a": Participation(supported=1)}, {"a": Participation(abstained=2), "b": Participation(failed=1)}]
    assert combine_participation(parts) == {"a": Participation(supported=1, abstained=2), "b": Participation(failed=1)}
    assert combine_order([[OrderStat(1, 2, 1)], [OrderStat(1, 1, 0), OrderStat(2, 1, 1)]]) == [
        OrderStat(1, 3, 1), OrderStat(2, 1, 1)]


def test_a_failed_turn_keeps_its_place_but_is_not_counted(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "pass", {"failure": "model_error"}, round=2)  # spoke first, said nothing
    lead = add_entry(conn, "r1", "b", "propose", {"answer_summary": "y", "method": "m"}, round=2)
    add_entry(conn, "r1", "c", "adopt", {"target": lead, "reason": "r"}, refs=[lead], round=2)
    assert speaking_order(conn, closed_runs(conn)[0]) == [OrderStat(2, 1, 1), OrderStat(3, 1, 0)]
