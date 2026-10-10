import json

import pytest

from aiswarm.events import read_events
from aiswarm.models.fake import FakeModel
from aiswarm.runner import Runner

AGENTS = ["a", "b", "c"]


def propose(view):
    return {"type": "propose", "answer_summary": "Blue", "method": "Asked myself."}


def entry_types(conn, run_id):
    return [r["type"] for r in conn.execute("SELECT type FROM entries WHERE run_id = ? ORDER BY id", (run_id,))]


class NoShuffle:
    def shuffle(self, items):
        pass


async def test_run_reaches_consensus(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {
        "a": scripted({1: propose, 2: lambda v: {
            "type": "call_consensus", "synthesis": "Blue it is", "refs": find(v, "propose"),
            "credits": [{"agent_id": "b", "contribution": "framing"}]}}),
        "b": scripted({1: propose, 3: lambda v: {
            "type": "critique", "target": find(v, "call_consensus")[0], "about": "answer", "points": "Too quick."}}),
        "c": scripted({1: propose, 3: lambda v: {
            "type": "vote", "target": find(v, "call_consensus")[0], "stance": "support", "reason": "Fine."}}),
    }
    runner = runner_factory(store, conn, clients)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    run_dir = tmp_path / "runs" / run_id
    answer = (run_dir / "report" / "answer.md").read_text()
    assert "Blue it is" in answer and "- Bea (b): framing" in answer
    events = read_events(run_dir / "events.jsonl")
    assert events[0]["event"] == "run_started" and events[-1]["event"] == "run_closed"
    assert conn.execute("SELECT COUNT(*) FROM rounds WHERE run_id = ?", (run_id,)).fetchone()[0] == 3
    assert entry_types(conn, run_id)[-1] == "run_closed"
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "closed"


async def test_stagnation_closes_run(community, scripted, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS})
    result = await runner.run(runner.start("G", AGENTS))
    assert result.reason == "stagnation"


async def test_budget_closes_run_with_competing_positions(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {
        "a": scripted({1: propose, 2: lambda v: {"type": "question", "target": find(v, "propose")[0], "text": "Why?"}}),
        "b": scripted({1: propose}),
        "c": scripted({1: propose}),
    }
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS)
    result = await runner.run(run_id)
    assert result.reason == "budget"
    assert "## Competing positions" in (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()


async def test_same_round_writes_one_wins_one_rejected(tmp_path, community, scripted, runner_factory):
    store, conn = community
    write = lambda v: {"type": "write_file", "path": "draft.md", "base_version": 0, "content": "x", "note": "start"}
    clients = {"a": scripted({1: propose, 2: write}), "b": scripted({1: propose, 2: write}), "c": scripted({1: propose})}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS)
    await runner.run(run_id)
    types = entry_types(conn, run_id)
    assert types.count("write_file") == 1
    assert types.count("artifact_changed") == 1
    assert types.count("write_rejected") == 1
    rejected = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'write_rejected'", (run_id,)).fetchone()
    assert json.loads(rejected[0])["current_version"] == 1
    assert (tmp_path / "runs" / run_id / "workspace" / "draft.md").read_text() == "x"
    assert conn.execute("SELECT kind FROM failures WHERE run_id = ?", (run_id,)).fetchone()[0] == "write_conflict"


async def test_outage_pauses_then_resume_completes(community, scripted, runner_factory):
    store, conn = community
    down = {a: FakeModel(fail_times=10**6) for a in AGENTS}
    run_id = runner_factory(store, conn, down).start("G", AGENTS)
    paused = await runner_factory(store, conn, down).run(run_id)
    assert paused.status == "paused"
    assert entry_types(conn, run_id) == ["goal"]
    result = await runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS}).run(run_id)
    assert (result.status, result.reason) == ("closed", "stagnation")


async def test_crash_mid_round_rolls_back_and_resumes(tmp_path, community, scripted, runner_factory):
    store, conn = community
    write = lambda v: {"type": "write_file", "path": "x.md", "base_version": 0, "content": "x", "note": "n"}

    def clients():
        return {"a": scripted({1: propose, 2: write}), "b": scripted({1: propose}), "c": scripted({1: propose})}

    class CrashingRunner(Runner):
        def _post(self, board, ws, run_id, rnd, agent_id, outcome):
            if rnd == 2 and agent_id == "b":
                raise RuntimeError("crash")
            return super()._post(board, ws, run_id, rnd, agent_id, outcome)

    first = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=3)
    crashing = CrashingRunner(settings=first.settings, conn=conn, store=store, runs_dir=tmp_path / "runs",
                              clients=clients(), rng=NoShuffle())
    run_id = crashing.start("G", AGENTS)
    with pytest.raises(RuntimeError, match="crash"):
        await crashing.run(run_id)
    assert all(r["round"] <= 1 for r in conn.execute("SELECT round FROM entries WHERE run_id = ?", (run_id,)))
    assert conn.execute("SELECT COUNT(*) FROM files WHERE run_id = ?", (run_id,)).fetchone()[0] == 0

    result = await runner_factory(store, conn, clients(), max_rounds=3).run(run_id)
    assert result.status == "closed"
    assert entry_types(conn, run_id).count("write_file") == 1
    assert (tmp_path / "runs" / run_id / "workspace" / "x.md").read_text() == "x"


async def test_reasoning_goes_to_traces_and_never_to_views(community, scripted, runner_factory):
    store, conn = community
    store.write_private("b", "temperament.md", "TEMPERAMENT-B-SECRET")
    clients = {a: scripted({1: propose}, reasoning=f"TRACE-{a}") for a in AGENTS}
    run_id = runner_factory(store, conn, clients).start("G", AGENTS)
    await runner_factory(store, conn, clients).run(run_id)
    assert conn.execute("SELECT COUNT(*) FROM traces WHERE run_id = ?", (run_id,)).fetchone()[0] >= 3
    for agent_id, client in clients.items():
        for system, messages, _ in client.calls:
            seen = system + "".join(m["content"] for m in messages)
            assert "TRACE-" not in seen
            if agent_id != "b":
                assert "TEMPERAMENT-B-SECRET" not in seen


def test_start_rejects_unknown_agents(community, runner_factory):
    store, conn = community
    with pytest.raises(ValueError, match="zed"):
        runner_factory(store, conn, {}).start("G", ["a", "zed"])


async def test_workspace_errors_become_rejected_writes(tmp_path, community, scripted, runner_factory):
    store, conn = community
    clients = {
        "a": scripted({1: propose, 2: lambda v: {"type": "write_file", "path": "notes", "base_version": 0, "content": "x", "note": "n"},
                       3: lambda v: {"type": "write_file", "path": "notes/x.md", "base_version": 0, "content": "y", "note": "n"}}),
        "b": scripted({1: propose, 2: lambda v: {"type": "write_file", "path": "dir/f.md", "base_version": 0, "content": "x", "note": "n"},
                       3: lambda v: {"type": "write_file", "path": "dir", "base_version": 0, "content": "y", "note": "n"}}),
        "c": scripted({1: propose}),
    }
    run_id = runner_factory(store, conn, clients, max_rounds=3).start("G", AGENTS)
    result = await runner_factory(store, conn, clients, max_rounds=3).run(run_id)
    assert result.status == "closed"
    kinds = [r[0] for r in conn.execute("SELECT kind FROM failures WHERE run_id = ?", (run_id,))]
    assert kinds.count("write_error") == 2
    assert entry_types(conn, run_id).count("write_rejected") == 2


async def test_unexpected_step_exception_becomes_failed_pass(community, scripted, runner_factory):
    store, conn = community

    class Broken:
        context_window = 32768

        async def chat(self, system, messages, json_schema=None):
            raise IndexError("choices[0] on empty list")

    clients = {"a": Broken(), "b": scripted({1: propose}), "c": scripted({1: propose})}
    run_id = runner_factory(store, conn, clients).start("G", AGENTS)
    result = await runner_factory(store, conn, clients).run(run_id)
    assert result.status == "closed"
    assert "step_error" in [r[0] for r in conn.execute("SELECT kind FROM failures WHERE run_id = ?", (run_id,))]
