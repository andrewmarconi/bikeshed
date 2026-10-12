import json

from bikeshed.board import Board, Entry
from bikeshed.events import read_events
from bikeshed.models.fake import FakeModel
from bikeshed.scorer import scorer_request

AGENTS = ["a", "b", "c"]
ACCEPT = json.dumps({"accepted": True, "feedback": ""})
REJECT = json.dumps({"accepted": False, "feedback": "It describes a poll, not a colour."})


def propose(view):
    return {"type": "propose", "answer_summary": "Blue", "method": "Gut"}


def clients_for(scripted, find, second_call=False):
    """a calls in round 2 (and, with second_call, again in round 4); b critiques and c supports the newest call."""
    def call(v):
        return {"type": "call_consensus", "synthesis": "Blue it is", "refs": find(v, "propose"),
                "credits": [{"agent_id": "a", "contribution": "the call"}, {"agent_id": "b", "contribution": "framing"}]}

    def critique(v):
        return {"type": "critique", "target": find(v, "call_consensus")[-1], "about": "answer", "points": "Hm."}

    def support(v):
        return {"type": "vote", "target": find(v, "call_consensus")[-1], "stance": "support", "reason": "Fine."}

    a = {1: propose, 2: call, **({4: call} if second_call else {})}
    b = {1: propose, 3: critique, **({5: critique} if second_call else {})}
    c = {1: propose, 3: support, **({5: support} if second_call else {})}
    return {"a": scripted(a), "b": scripted(b), "c": scripted(c)}


def points_rows(conn, run_id):
    return sorted(tuple(r) for r in conn.execute(
        "SELECT agent_id, call_id, contribution FROM scorer_points WHERE run_id = ?", (run_id,)))


def board_types(conn, run_id):
    return [r["type"] for r in conn.execute("SELECT type FROM entries WHERE run_id = ? ORDER BY id", (run_id,))]


async def test_an_accepted_call_closes_the_run_and_awards_points(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    scorer = FakeModel([ACCEPT])
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=scorer)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    call_id = result.call_id
    assert points_rows(conn, run_id) == [("a", call_id, "the call"), ("b", call_id, "framing")]
    assert board_types(conn, run_id)[-2:] == ["scorer_accepted", "run_closed"]
    assert scorer.calls[0][1][0]["content"] == scorer_request("Pick a colour", "Blue it is")
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    ruled = next(e for e in events if e["event"] == "scorer_ruled")
    assert (ruled["call"], ruled["accepted"], ruled["model"]) == (call_id, True, "fake")
    answer = (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()
    assert f"## Scorer\n\n- call #{call_id}: accepted" in answer


async def test_a_rejected_call_is_spent_and_the_run_ends_on_budget(tmp_path, community, scripted, find,
                                                                    runner_factory):
    store, conn = community
    scorer = FakeModel([REJECT])
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=scorer, max_rounds=4)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "budget")
    assert len(scorer.calls) == 1  # the spent call is never submitted again
    assert points_rows(conn, run_id) == []
    rejected = conn.execute("SELECT round, payload FROM entries WHERE run_id = ? AND type = 'scorer_rejected'",
                            (run_id,)).fetchone()
    assert rejected["round"] == 3 and json.loads(rejected["payload"])["feedback"] == "It describes a poll, not a colour."
    feedback = (tmp_path / "runs" / run_id / "workspace" / "scorer_feedback.md").read_text()
    assert "It describes a poll, not a colour." in feedback
    assert conn.execute("SELECT author FROM files WHERE run_id = ? AND path = 'scorer_feedback.md'",
                        (run_id,)).fetchone()[0] == "system"
    assert "rejected — It describes a poll, not a colour." in \
        (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()


async def test_a_rejection_in_the_last_round_closes_on_budget(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel([REJECT]), max_rounds=3)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "budget")
    assert board_types(conn, run_id)[-2:] == ["scorer_rejected", "run_closed"]


async def test_a_later_call_can_be_accepted(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    scorer = FakeModel([REJECT, ACCEPT])
    runner = runner_factory(store, conn, clients_for(scripted, find, second_call=True), scorer=scorer, max_rounds=8)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    calls = [r["id"] for r in conn.execute(
        "SELECT id FROM entries WHERE run_id = ? AND type = 'call_consensus' ORDER BY id", (run_id,))]
    assert (result.status, result.reason, result.call_id) == ("closed", "consensus", calls[1])
    assert {row[1] for row in points_rows(conn, run_id)} == {calls[1]}


async def test_no_ruling_pauses_and_resume_asks_again_before_any_round(tmp_path, community, scripted, find,
                                                                       runner_factory):
    store, conn = community
    clients = clients_for(scripted, find)
    paused = await runner_factory(store, conn, clients, scorer=FakeModel([ACCEPT], fail_times=1)).run(
        runner_factory(store, conn, clients).start("Pick a colour", AGENTS))
    assert paused.status == "paused"
    run_id = paused.run_id
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert events[-1]["event"] == "run_paused" and events[-1]["detail"].startswith("scorer: model error")
    resumed = await runner_factory(store, conn, clients, scorer=FakeModel([ACCEPT])).run(run_id)
    assert (resumed.status, resumed.reason) == ("closed", "consensus")
    after = read_events(tmp_path / "runs" / run_id / "events.jsonl")[len(events):]
    assert not any(e["event"] == "round_started" for e in after)
    assert len(points_rows(conn, run_id)) == 2  # awarded once


async def test_an_invalid_ruling_twice_pauses(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel(["nope", "nope"]))
    result = await runner.run(runner.start("Pick a colour", AGENTS))
    assert result.status == "paused"
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0] == "paused"


def test_points_go_once_to_each_credited_member(tmp_path, community, runner_factory, make_run):
    store, conn = community
    make_run(conn, "r1", AGENTS)
    board = Board(conn, "r1")
    call = Entry(9, "r1", 3, "a", "call_consensus", {"synthesis": "S", "credits": [
        {"agent_id": "a", "contribution": "me"}, {"agent_id": "a", "contribution": "me again"},
        {"agent_id": "zz", "contribution": "ghost"}, {"agent_id": "c", "contribution": "data"}]}, [], "all", "t")
    runner_factory(store, conn, {})._accept(board, 3, call, AGENTS)
    assert points_rows(conn, "r1") == [("a", 9, "me"), ("c", 9, "data")]
    from bikeshed.scorer import points
    assert points(conn) == {"a": 1, "c": 1}
