import json

import pytest

from bikeshed.config import RunSettings
from bikeshed.events import read_events
from bikeshed.runner import Runner

AGENTS = ["a", "b", "c"]


class NoShuffle:
    def shuffle(self, items):
        pass


def propose(answer):
    return lambda v: {"type": "propose", "answer_summary": answer, "method": "M."}


def rows(conn, run_id, type_):
    return [dict(r) for r in conn.execute(
        "SELECT id, round, author, to_, visible_to, payload FROM entries WHERE run_id = ? AND type = ? ORDER BY id",
        (run_id, type_))]


def views(client):
    return [messages[0]["content"] for _, messages, _ in client.calls]


async def test_think_notes_are_private_and_shown_back_to_their_author(community, process2, runner_factory):
    store, conn = community
    clients = {a: process2(react={1: propose(a.upper())},
                           think={1: lambda v, a=a: {"type": "note", "text": f"SECRET-NOTE-{a}"}}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    notes = rows(conn, run_id, "think")
    assert [(n["round"], n["author"]) for n in notes][:3] == [(1, "a"), (1, "b"), (1, "c")]
    assert all(json.loads(n["visible_to"]) == [n["author"]] for n in notes)
    for agent in AGENTS:
        seen = "".join(views(clients[agent]))
        assert f"SECRET-NOTE-{agent}" in seen
        assert all(f"SECRET-NOTE-{other}" not in seen for other in AGENTS if other != agent)
    round2 = [v for v in views(clients["a"]) if "# Round 2 of 2" in v]
    assert round2 and "## round 1\nSECRET-NOTE-a" in round2[0]


async def test_round_one_is_blind_and_later_rounds_speak_in_turn(community, process2, find, runner_factory):
    store, conn = community
    clients = {
        "a": process2(react={1: propose("OPENING-A"), 2: lambda v: {
            "type": "question", "target": find(v, "propose", "b")[0], "text": "A-ASKS-FIRST"}}),
        "b": process2(react={1: propose("OPENING-B")}),
        "c": process2(react={1: propose("OPENING-C")}),
    }
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    react_views = {a: [v for v in views(clients[a]) if "\n# Think\n" not in v and "\n# Poll\n" not in v]
                   for a in AGENTS}
    assert "OPENING-A" not in react_views["b"][0] and "OPENING-C" not in react_views["b"][0]  # round 1: blind
    assert "A-ASKS-FIRST" in react_views["b"][1] and "A-ASKS-FIRST" in react_views["c"][1]  # round 2: a spoke first


async def test_private_messages_reach_only_their_recipient(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "SECRET-DM"}
    clients = {"a": process2(react={1: propose("A"), 2: [dm, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    [message] = rows(conn, run_id, "private_message")
    assert (message["author"], message["to_"], json.loads(message["visible_to"])) == ("a", "b", ["a", "b"])
    assert json.loads(message["payload"]) == {"to": "b", "text": "SECRET-DM", "pre_action": True}
    assert any("SECRET-DM" in v for v in views(clients["b"]))
    assert not any("SECRET-DM" in v for v in views(clients["c"]))


async def test_a_private_message_to_oneself_becomes_an_invalid_action(community, process2, runner_factory):
    store, conn = community
    clients = {"a": process2(react={1: propose("A"), 2: lambda v: {"type": "private_message", "to": "a", "text": "x"}}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert rows(conn, run_id, "private_message") == []
    assert ("a", "invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ? AND round = 2", (run_id,))]


async def test_a_failed_think_is_recorded_and_the_agent_still_reacts(community, process2, runner_factory):
    store, conn = community
    clients = {"a": process2(react={1: propose("A")}, think={1: lambda v: {"type": "pass"}}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert [n["author"] for n in rows(conn, run_id, "think")] == ["b", "c"]
    assert ("a", "think:invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]
    assert "a" in [p["author"] for p in rows(conn, run_id, "propose")]


async def test_process2_stagnates_when_nobody_speaks(community, process2, runner_factory):
    store, conn = community
    clients = {a: process2(react={1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=6)
    result = await runner.run(runner.start("G", AGENTS, process=2))
    assert result.reason == "stagnation"


async def test_resume_discards_a_partial_round_and_replays_it(tmp_path, community, process2, find, runner_factory):
    store, conn = community

    def clients():
        return {a: process2(react={1: propose(a), 2: lambda v, a=a: {
            "type": "question", "target": find(v, "propose")[0], "text": f"Q-{a}"}}) for a in AGENTS}

    class CrashingRunner(Runner):
        def _post(self, board, ws, run_id, rnd, agent_id, outcome):
            if rnd == 2 and agent_id == "b":
                raise RuntimeError("crash")
            return super()._post(board, ws, run_id, rnd, agent_id, outcome)

    first = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2)
    crashing = CrashingRunner(settings=first.settings, conn=conn, store=store, runs_dir=tmp_path / "runs",
                              clients=clients(), rng=NoShuffle())
    run_id = crashing.start("G", AGENTS, process=2)
    with pytest.raises(RuntimeError, match="crash"):
        await crashing.run(run_id)
    assert [q["author"] for q in rows(conn, run_id, "question")] == ["a"]  # a spoke before the crash

    result = await runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).run(run_id)
    assert result.status == "closed"
    assert [q["author"] for q in rows(conn, run_id, "question")] == ["a", "b", "c"]
    assert len([n for n in rows(conn, run_id, "think") if n["round"] == 2]) == 3
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert [e["round"] for e in events if e["event"] == "round_discarded"] == [2]


def call_then_critique(find):
    return {
        "a": {1: propose("A"), 2: lambda v: {"type": "call_consensus", "synthesis": "A it is",
                                             "refs": find(v, "propose", "a"), "credits": []}},
        "b": {1: propose("B"), 2: lambda v: {"type": "critique", "target": find(v, "call_consensus")[0],
                                             "about": "answer", "points": "Hasty."}},
        "c": {1: propose("C")},
    }


async def test_the_poll_turns_stances_into_votes_and_reaches_consensus(community, process2, find, runner_factory):
    store, conn = community
    support = lambda v: {"type": "poll_answer", "stance": "support", "reason": "Fine."}
    plans = call_then_critique(find)
    clients = {a: process2(react=plans[a], poll={2: support}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=4)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    assert sorted(v["author"] for v in rows(conn, run_id, "vote")) == ["b", "c"]
    polled = [messages[0]["content"] for _, messages, _ in clients["a"].calls if "\n# Poll\n" in messages[0]["content"]]
    assert polled == []  # the caller is never polled


async def test_under_the_stood_round_rule_an_uncritiqued_call_closes_a_round_later(community, process2, find,
                                                                                    runner_factory):
    store, conn = community
    support = lambda v: {"type": "poll_answer", "stance": "support", "reason": "Fine."}
    call = lambda v: {"type": "call_consensus", "synthesis": "A it is", "refs": find(v, "propose", "a"), "credits": []}
    clients = {"a": process2(react={1: propose("A"), 2: call}),
               "b": process2(react={1: propose("B")}, poll={2: support}),
               "c": process2(react={1: propose("C")}, poll={2: support})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=4, close_rule="stood_round")
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    assert rows(conn, run_id, "critique") == []
    assert conn.execute("SELECT MAX(round) FROM rounds WHERE run_id = ?", (run_id,)).fetchone()[0] == 3


async def test_abstaining_is_recorded_and_counts_for_nothing(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)
    clients = {a: process2(react=plans[a]) for a in AGENTS}  # the default poll answer abstains
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert result.reason == "budget"
    abstained = rows(conn, run_id, "abstain")
    assert sorted(a["author"] for a in abstained) == ["b", "c"] and all(a["visible_to"] is None for a in abstained)
    assert rows(conn, run_id, "vote") == []


async def test_a_failed_poll_answer_records_a_failure_and_no_vote(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)
    clients = {"a": process2(react=plans["a"]), "b": process2(react=plans["b"]),
               "c": process2(react=plans["c"], poll={2: lambda v: {"type": "poll_answer", "stance": "maybe",
                                                                     "reason": "r"}})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert "c" not in [v["author"] for v in rows(conn, run_id, "vote") + rows(conn, run_id, "abstain")]
    assert ("c", "poll:invalid_action") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]


async def test_a_poll_outage_pauses_without_inventing_votes(community, process2, find, runner_factory):
    store, conn = community
    plans = call_then_critique(find)

    class PollDown:
        """Answers like the scripted model, except every poll request fails."""
        def __init__(self, inner):
            self.inner, self.context_window, self.calls = inner, inner.context_window, inner.calls

        async def chat(self, system, messages, json_schema=None):
            if "\n# Poll\n" in messages[0]["content"]:
                from bikeshed.models.base import ModelError
                raise ModelError("down")
            return await self.inner.chat(system, messages, json_schema)

    clients = {a: PollDown(process2(react=plans[a])) for a in AGENTS}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert result.status == "paused"
    assert rows(conn, run_id, "vote") == [] and rows(conn, run_id, "abstain") == []


class Down:
    """Answers like the wrapped model, except requests matching `when` fail with a model error."""
    def __init__(self, inner, when):
        self.inner, self.when, self.context_window, self.calls = inner, when, inner.context_window, inner.calls

    async def chat(self, system, messages, json_schema=None):
        if self.when(messages[0]["content"]):
            from bikeshed.models.base import ModelError
            raise ModelError("down")
        return await self.inner.chat(system, messages, json_schema)


def react_in(round_):
    return lambda c: f"# Round {round_} of" in c and "\n# Think\n" not in c and "\n# Poll\n" not in c


async def test_one_dead_agent_in_the_poll_does_not_stall_the_run(community, process2, find, runner_factory):
    store, conn = community
    plans = {"a": {1: propose("A"), 2: lambda v: {"type": "call_consensus", "synthesis": "A it is",
                                                  "refs": find(v, "propose", "a"), "credits": []}},
             "b": {1: propose("B"), 2: lambda v: {"type": "vote", "target": find(v, "call_consensus")[0],
                                                  "stance": "support", "reason": "Yes."}},
             "c": {1: propose("C")}}
    clients = {"a": process2(react=plans["a"]), "b": process2(react=plans["b"]),
               "c": Down(process2(react=plans["c"]), lambda c: "\n# Poll\n" in c)}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    result = await runner.run(run_id)
    assert result.status == "closed"
    assert ("c", "poll:model_error") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]


async def test_an_outage_that_starts_mid_turn_pauses_and_the_round_replays(tmp_path, community, process2, find,
                                                                         runner_factory):
    store, conn = community

    def plans():
        return {a: {1: propose(a), 2: lambda v, a=a: {"type": "question", "target": find(v, "propose")[0],
                                                      "text": f"Q-{a}"}} for a in AGENTS}

    p = plans()
    clients = {"a": process2(react=p["a"]), "b": Down(process2(react=p["b"]), react_in(2)),
               "c": Down(process2(react=p["c"]), react_in(2))}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    assert (await runner.run(run_id)).status == "paused"
    healthy = {a: process2(react=plan) for a, plan in plans().items()}
    result = await runner_factory(store, conn, healthy, rng=NoShuffle(), max_rounds=2).run(run_id)
    assert result.status == "closed"
    assert [q["author"] for q in rows(conn, run_id, "question")] == ["a", "b", "c"]
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert [e["round"] for e in events if e["event"] == "round_discarded"] == [2]


async def test_one_dead_agent_speaking_last_does_not_pause_the_run(community, process2, runner_factory):
    store, conn = community
    clients = {"a": process2(react={1: propose("A")}), "b": process2(react={1: propose("B")}),
               "c": Down(process2(react={1: propose("C")}), react_in(2))}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    result = await runner.run(runner.start("G", AGENTS, process=2))
    assert result.status == "closed"


async def test_the_round_one_think_request_does_not_also_ask_for_a_proposal(community, process2, runner_factory):
    store, conn = community
    clients = {a: process2(react={1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    await runner.run(runner.start("G", AGENTS, process=2))
    think = next(v for v in views(clients["a"]) if "\n# Think\n" in v)
    react = next(v for v in views(clients["a"]) if "\n# Think\n" not in v)
    assert "Propose your own position" not in think
    assert "Propose your own position" in react


async def test_process2_header_counts_rounds_and_process1_does_not(community, process2, scripted, runner_factory):
    store, conn = community
    p2 = {a: process2(react={1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, p2, max_rounds=1)
    await runner.run(runner.start("G", AGENTS, process=2))
    assert all("# Round 1 of 1" in v for v in views(p2["a"]))
    p1 = {a: scripted({1: propose(a)}) for a in AGENTS}
    runner = runner_factory(store, conn, p1, max_rounds=1)
    await runner.run(runner.start("G", AGENTS))
    assert all("# Round 1\n" in v and " of 1" not in v for v in views(p1["a"]))


async def test_the_poll_skips_a_call_the_scorer_rejected():
    from types import SimpleNamespace

    from bikeshed.board import Entry
    from bikeshed.phases import poll

    def entry(id_, author, type_, payload):
        return Entry(id_, "r1", 2, author, type_, payload, [], "all", "t")

    entries = [entry(5, "a", "call_consensus", {"synthesis": "S", "credits": []}),
               entry(6, "system", "scorer_rejected", {"call_id": 5, "feedback": "No answer."})]
    ctx = SimpleNamespace(board=SimpleNamespace(entries=lambda: entries), agent_ids=["a", "b", "c"])

    class NoPoll:
        async def _poll(self, *args):
            raise AssertionError("a spent call must not be polled")

    assert await poll(NoPoll(), ctx) is None


WRITE_PLAN = lambda v: {"type": "write_file", "path": "plan.md", "base_version": 0, "content": "PLAN", "note": "n"}


async def test_a_turn_writes_messages_and_then_acts(tmp_path, community, process2, find, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "c", "text": "see plan.md"}
    ask = lambda v: {"type": "question", "target": find(v, "propose", "b")[0], "text": "Why?"}
    clients = {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, dm, ask]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    turn = [(r["type"], json.loads(r["payload"]).get("pre_action")) for r in conn.execute(
        "SELECT type, payload FROM entries WHERE run_id = ? AND round = 2 AND type NOT IN ('think') ORDER BY id",
        (run_id,))][:4]
    assert turn == [("write_file", True), ("artifact_changed", True), ("private_message", True), ("question", None)]
    assert (tmp_path / "runs" / run_id / "workspace" / "plan.md").read_text() == "PLAN"
    replies = [m[-1]["content"] for _, m, _ in clients["a"].calls if len(m) > 1]
    assert "Write result:\nplan.md is now v1." in replies and "Write result:\nMessage sent to c." in replies
    steps = [e for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")
             if e["event"] == "agent_step_finished" and e["agent"] == "a" and e["round"] == 2]
    assert [(e.get("phase"), e["pre_actions"], e["calls"]) for e in steps] == [
        ("think", [], 1),
        (None, [{"type": "write_file", "ok": True}, {"type": "private_message", "ok": True}], 3),
    ]


async def test_a_conflicting_pre_action_write_does_not_cost_the_turn(community, process2, find, runner_factory):
    store, conn = community
    ask = lambda v: {"type": "question", "target": find(v, "propose", "a")[0], "text": "Why?"}
    clients = {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B"), 2: [WRITE_PLAN, ask]}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert [r["author"] for r in rows(conn, run_id, "question")] == ["b"]  # b's turn still acted
    [rejected] = rows(conn, run_id, "write_rejected")
    assert json.loads(rejected["payload"])["current_version"] == 1
    assert ("b", "write_conflict") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]
    replies = [m[-1]["content"] for _, m, _ in clients["b"].calls if len(m) > 1]
    assert "Write result:\nWrite rejected: plan.md changed since v0; it is now v1." in replies


async def test_round_one_writes_are_refused(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "early"}
    clients = {"a": process2(react={1: [dm, propose("A")]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert rows(conn, run_id, "private_message") == []
    assert "a" in [p["author"] for p in rows(conn, run_id, "propose")]


async def test_the_pre_action_flag_is_not_shown_to_agents(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "hello"}
    clients = {"a": process2(react={1: propose("A"), 2: [dm, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    await runner.run(runner.start("G", AGENTS, process=2))
    assert any("hello" in v for v in views(clients["b"]))
    assert not any("pre_action" in v for a in AGENTS for v in views(clients[a]))


async def test_resume_discards_pre_action_entries_of_a_partial_round(tmp_path, community, process2, runner_factory):
    store, conn = community

    def clients():
        return {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, lambda v: {"type": "pass"}]}),
                "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}

    class CrashingRunner(Runner):
        async def _step(self, agent_id, ctx):
            outcome = await super()._step(agent_id, ctx)
            if ctx.round == 2 and agent_id == "a":
                raise RuntimeError("crash")
            return outcome

    run_id = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).start("G", AGENTS, process=2)
    crashing = CrashingRunner(settings=RunSettings(max_rounds=2, process=2), conn=conn, store=store,
                              runs_dir=tmp_path / "runs", clients=clients(), rng=NoShuffle())
    with pytest.raises(RuntimeError):
        await crashing.run(run_id)
    assert len(rows(conn, run_id, "write_file")) == 1  # posted before the crash
    await runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).run(run_id)
    assert len(rows(conn, run_id, "write_file")) == 1  # the replayed round wrote it once, not twice


async def test_messages_followed_by_pass_still_stagnate(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "psst"}
    clients = {"a": process2(react={r: [dm, lambda v: {"type": "pass"}] for r in range(2, 7)} | {1: propose("A")}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=6)
    result = await runner.run(runner.start("G", AGENTS, process=2))
    assert result.reason == "stagnation"


async def test_process_3_runs_process_2_rounds_and_records_3(tmp_path, community, process2, runner_factory):
    from bikeshed.briefs import BriefSpec

    store, conn = community
    shape, steps, run_ids = {}, {}, {}
    for process in (2, 3):
        clients = {a: process2(react={1: propose(a.upper())}) for a in AGENTS}
        runner = runner_factory(store, conn, clients, max_rounds=2)
        run_ids[process] = runner.start("G", AGENTS, process=process, brief=BriefSpec("Shared facts."))
        await runner.run(run_ids[process])
        shape[process] = [(r["round"], r["author"], r["type"]) for r in conn.execute(
            "SELECT round, author, type FROM entries WHERE run_id = ? ORDER BY id", (run_ids[process],))]
        steps[process] = [(e["round"], e["agent"], e.get("phase"))
                          for e in read_events(tmp_path / "runs" / run_ids[process] / "events.jsonl")
                          if e["event"] == "agent_step_started"]
    assert shape[3] == shape[2] and steps[3] == steps[2]
    assert any(phase == "think" for _, _, phase in steps[3]) and any(t == "think" for _, _, t in shape[3])
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (run_ids[3],)).fetchone()[0] == 3

