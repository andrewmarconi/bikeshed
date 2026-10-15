from observer_helpers import add_entry, add_run

from bikeshed.observer.copying import copies, credit_accuracy
from bikeshed.observer.metrics import closed_runs

REASON = "it meets every constraint and nobody has objected to the plan so far"


def run(conn):
    return closed_runs(conn)[0]


def test_a_pasted_vote_reason_is_exact_and_tagged_by_round(community):
    _, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "b", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=2)
    same = add_entry(conn, "r1", "a", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=2)
    later = add_entry(conn, "r1", "c", "vote", {"target": 1, "stance": "support", "reason": REASON}, round=3)
    found = {(m.entry_id, m.source_author): m for m in copies(conn, run(conn))}
    assert found[(same, "b")].kind == "exact" and found[(same, "b")].same_round
    assert found[(later, "b")].field == "reason" and not found[(later, "b")].same_round
    assert found[(later, "a")].kind == "exact"


def test_a_received_private_message_is_a_source_but_others_messages_are_not(community):
    _, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": REASON}, visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "c", "private_message", {"to": "b", "text": REASON + " again"}, visible_to=["c", "b"], to="b")
    pasted = add_entry(conn, "r1", "a", "propose", {"answer_summary": REASON, "method": "m"}, round=2)
    sources = {m.source_author for m in copies(conn, run(conn)) if m.entry_id == pasted}
    assert sources == {"b"}


def test_think_notes_own_text_and_failures_are_left_out(community):
    _, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "b", "think", {"text": REASON}, visible_to=["b"])
    add_entry(conn, "r1", "a", "propose", {"answer_summary": REASON, "method": "m"})
    add_entry(conn, "r1", "a", "critique", {"target": 1, "about": "answer", "points": REASON}, round=2)
    add_entry(conn, "r1", "c", "think", {"text": REASON}, round=2, visible_to=["c"])
    add_entry(conn, "r1", "c", "pass", {"failure": "model_error"}, round=2)
    assert copies(conn, run(conn)) == []


def test_credit_accuracy_lists_uncredited_sources_and_credits_without_text(community):
    _, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Picnic", "method": REASON})
    call = add_entry(conn, "r1", "a", "call_consensus", {
        "synthesis": "Picnic: " + REASON, "refs": [], "credits": [
            {"agent_id": "c", "contribution": "ideas"}, {"agent_id": "a", "contribution": "synthesis"}]}, round=2)
    (check,) = credit_accuracy(conn, run(conn), copies(conn, run(conn)))
    assert check.call_id == call and check.caller == "a"
    assert check.copied == ("b",) and check.credited == ("a", "c")
    assert check.uncredited == ("b",) and check.unmatched == ("c",)  # the caller is never "without matching text"
