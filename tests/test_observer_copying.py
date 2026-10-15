import json

from observer_helpers import add_entry, add_run

from bikeshed.observer.copying import copies, credit_accuracy, library_reuse, norm_overlaps
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


def _method(conn, method_id, run_id, author, text):
    entry = add_entry(conn, run_id, author, "propose", {"answer_summary": "m", "method": text})  # methods ref an entry
    conn.execute("INSERT INTO methods (id, run_id, entry_id, author, kind, goal, text, reception, created_at) "
                 "VALUES (?, ?, ?, ?, 'propose', 'g', ?, '{}', 't')", (method_id, run_id, entry, author, text))


def _events(tmp_path, run_id, *events):
    path = tmp_path / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return tmp_path / "runs"


def test_library_reuse_finds_a_shown_method_in_a_proposal(community, tmp_path):
    _, conn = community
    add_run(conn, "old", created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r1", agents=("a", "b"))
    _method(conn, 7, "old", "c", REASON)
    _method(conn, 8, "old", "c", "something else entirely different from anything posted here today")
    reused = add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": REASON})
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [7, 8]})
    got = {r.method_id: r for r in library_reuse(conn, closed_runs(conn)[1], runs_dir)}
    assert got[7].shown_to == ("a", "b") and got[7].reused_by == (("a", reused, "exact"),)
    assert got[8].reused_by == () and got[7].method_author == "c" and got[7].method_run == "old"


def test_a_control_is_checked_against_its_own_list(community, tmp_path):
    _, conn = community
    add_run(conn, "r1", agents=("a", "e"))
    _method(conn, 7, "r1", "c", REASON)
    add_entry(conn, "r1", "e", "propose", {"answer_summary": "x", "method": REASON})
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [7], "control_methods": {"e": []}})
    (only,) = library_reuse(conn, closed_runs(conn)[0], runs_dir)
    assert only.shown_to == ("a",) and only.reused_by == ()


def test_library_reuse_unions_resumed_retrievals_and_skips_unknown_methods(community, tmp_path):
    _, conn = community
    add_run(conn, "r1", agents=("a",))
    _method(conn, 7, "r1", "c", REASON)
    runs_dir = _events(tmp_path, "r1", {"event": "library_retrieved", "methods": [99]},
                       {"event": "library_retrieved", "methods": [7]})
    assert [r.method_id for r in library_reuse(conn, closed_runs(conn)[0], runs_dir)] == [7]


def test_library_reuse_not_recorded_without_events(community, tmp_path):
    _, conn = community
    add_run(conn, "r1")
    assert library_reuse(conn, closed_runs(conn)[0], tmp_path / "runs") is None
    runs_dir = _events(tmp_path, "r1", {"event": "run_started"})
    assert library_reuse(conn, closed_runs(conn)[0], runs_dir) is None
    runs_dir = _events(tmp_path, "r1", {"event": "library_unavailable", "detail": "down"})
    assert library_reuse(conn, closed_runs(conn)[0], runs_dir) == []


NORM = "members should cite the entry they are responding to whenever they disagree with it"


def _proposal(conn, run_id, author, text, status, created, kind="norm"):
    return conn.execute(
        "INSERT INTO norm_proposals (run_id, author, kind, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, author, kind, text, status, created)).lastrowid


def test_a_proposal_restating_an_earlier_adopted_norm_is_listed_with_its_outcome(community):
    _, conn = community
    add_run(conn, "r1", created="2000-01-01T00:00:00+00:00")
    add_run(conn, "r2", created="2000-02-01T00:00:00+00:00")
    adopted = _proposal(conn, "r1", "a", NORM, "adopted", "2000-01-01T00:00:00+00:00")
    again = _proposal(conn, "r2", "b", "Norm: " + NORM, "rejected", "2000-02-01T00:00:00+00:00")
    _proposal(conn, "r2", "c", "rate clarity", "open", "2000-02-01T00:00:00+00:00", kind="dimension")
    (o,) = norm_overlaps(conn, closed_runs(conn))
    # the proposal is longer than the norm, so most but not all of it comes from the norm: partial
    assert (o.proposal_id, o.norm_id, o.kind, o.status, o.author) == (again, adopted, "partial", "rejected", "b")


def test_a_norm_adopted_after_the_proposal_does_not_count(community):
    _, conn = community
    add_run(conn, "r1", created="2000-02-01T00:00:00+00:00")
    add_run(conn, "r2", created="2000-01-01T00:00:00+00:00")
    _proposal(conn, "r1", "a", NORM, "adopted", "2000-02-01T00:00:00+00:00")
    _proposal(conn, "r2", "b", NORM, "rejected", "2000-01-01T00:00:00+00:00")
    assert [o for o in norm_overlaps(conn, closed_runs(conn)) if o.author == "b"] == []
