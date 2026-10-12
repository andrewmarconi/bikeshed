import json

import pytest

from aiswarm.board import SYSTEM, Board
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


async def test_agents_can_read_peer_reviews_but_not_private_notes(tmp_path, community, runner_factory):
    from aiswarm.reviews import add_review

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    add_review(conn, run_id="old", reviewer="c", reviewee="b", more_of="PUBLIC-MORE", less_of="l",
               private_note="SECRET-NOTE", evidence=[1], overall=5, overall_reason="r")
    reader = FakeModel([
        json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}}),
        json.dumps({"action": {"type": "read_reviews", "agent_id": "b"}}),
        json.dumps({"action": {"type": "pass"}}),
    ])
    clients = {"a": reader, "b": FakeModel(), "c": FakeModel()}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    await runner.run(runner.start("Pick a colour", ["a", "b", "c"]))
    read_result = reader.calls[2][1][-1]["content"]
    assert read_result.startswith("Read result:\nReview")
    assert "PUBLIC-MORE" in read_result and "SECRET-NOTE" not in read_result


async def test_runs_show_relevant_methods_from_earlier_runs(tmp_path, community, runner_factory, make_run):
    from aiswarm.events import read_events
    from aiswarm.library import embed_missing, record_methods
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    make_run(conn, "old", ("a", "b"))
    old = Board(conn, "old")
    old.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    old.append(1, "b", "propose", {"answer_summary": "x", "method": "LIB-METHOD pick colour by vote"})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'old'")
    record_methods(conn, "old", "Pick a colour", old.entries())
    embedder = FakeEmbedder()
    await embed_missing(conn, embedder)
    seen = FakeModel([json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}})])
    runner = runner_factory(store, conn, {"a": seen}, max_rounds=1)
    runner.embedder = embedder
    run_id = runner.start("Pick a colour", ["a"])
    await runner.run(run_id)
    assert "# Methods from earlier runs" in seen.calls[0][1][0]["content"]
    assert "LIB-METHOD" in seen.calls[0][1][0]["content"]
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert any(e["event"] == "library_retrieved" for e in events)


async def test_a_run_proceeds_without_the_library_when_embeddings_fail(tmp_path, community, runner_factory):
    from aiswarm.events import read_events
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO entries (run_id, round, author, type, payload, created_at) VALUES ('old', 1, 'b', 'propose', '{}', 't')")
    conn.execute("INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, embedding, created_at) "
                 "VALUES ('old', 1, 'b', 'propose', 'g', 't', '{\"adopted_by\": [], \"critiques\": 0, \"refines\": 0, "
                 "\"questions\": 0, \"in_winning\": false}', x'0000803f', 't')")
    seen = FakeModel([json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}})])
    runner = runner_factory(store, conn, {"a": seen}, max_rounds=1)
    runner.embedder = FakeEmbedder(fail_times=5)
    run_id = runner.start("Pick a colour", ["a"])
    result = await runner.run(run_id)
    assert result.status == "closed"
    assert "# Methods from earlier runs" not in seen.calls[0][1][0]["content"]
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert any(e["event"] == "library_unavailable" for e in events)


async def test_an_empty_embeddings_response_never_fails_the_run(tmp_path, community, runner_factory):
    from aiswarm.events import read_events

    class Empty:
        async def embed(self, texts):
            return []

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO entries (run_id, round, author, type, payload, created_at) VALUES ('old', 1, 'b', 'propose', '{}', 't')")
    conn.execute("INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, embedding, created_at) "
                 "VALUES ('old', 1, 'b', 'propose', 'g', 't', '{\"adopted_by\": [], \"critiques\": 0, \"refines\": 0, "
                 "\"questions\": 0, \"in_winning\": false}', x'0000803f', 't')")
    seen = FakeModel([json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}})])
    runner = runner_factory(store, conn, {"a": seen}, max_rounds=1)
    runner.embedder = Empty()
    run_id = runner.start("Pick a colour", ["a"])
    assert (await runner.run(run_id)).status == "closed"
    assert any(e["event"] == "library_unavailable" for e in read_events(tmp_path / "runs" / run_id / "events.jsonl"))


from aiswarm.config import RunSettings  # noqa: E402


async def test_seed_is_recorded_and_fixes_post_order(tmp_path, community, scripted):
    store, conn = community

    async def order(seed):
        runner = Runner(settings=RunSettings(max_rounds=1), conn=conn, store=store, runs_dir=tmp_path / "runs",
                        clients={a: scripted({1: propose}) for a in AGENTS})
        run_id = runner.start("G", AGENTS)
        assert conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()[0] is not None
        conn.execute("UPDATE runs SET seed = ? WHERE id = ?", (seed, run_id))
        await runner.run(run_id)
        rows = conn.execute("SELECT author FROM entries WHERE run_id = ? AND type = 'propose' ORDER BY id", (run_id,))
        return [r["author"] for r in rows]

    assert await order(7) == await order(7)


def add_control(store, agent_id="d", name="Dee"):
    store.create(agent_id, "fake", "Seed temperament.", control=True)
    store.set_public(agent_id, name, f"{name} headline", f"I am {name}.")
    store.save_seed_public(agent_id)


async def test_start_resets_control_agents_but_resume_does_not(tmp_path, community, scripted, runner_factory):
    store, conn = community
    add_control(store)
    store.write_private("d", "lessons.md", "LEARNED")
    agents = [*AGENTS, "d"]
    runner = runner_factory(store, conn, {a: scripted({}) for a in agents}, max_rounds=1)
    run_id = runner.start("G", agents)
    assert store.private("d")["lessons.md"] == ""
    store.write_private("d", "lessons.md", "AFTER-START")
    await runner.run(run_id)
    assert store.private("d")["lessons.md"] == "AFTER-START"
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[:2] == ["run_started", "control_reset"]


def test_start_refuses_a_control_agent_without_a_seed_profile(community, scripted, runner_factory):
    store, conn = community
    store.create("d", "fake", "Seed.", control=True)
    store.set_public("d", "Dee", "H", "I am Dee.")
    runner = runner_factory(store, conn, {})
    with pytest.raises(ValueError, match="agents introduce d"):
        runner.start("G", ["a", "d"])
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


async def test_start_records_goal_metadata_and_never_shows_it_to_agents(community, scripted, runner_factory):
    store, conn = community
    clients = {a: scripted({1: propose}) for a in AGENTS}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("Pick one pizza.", AGENTS, goal_id="benchmark", goal_category="trade-off", is_benchmark=True)
    await runner.run(run_id)
    row = conn.execute("SELECT goal_id, goal_category, is_benchmark FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert tuple(row) == ("benchmark", "trade-off", 1)
    sent = json.dumps([call[:2] for client in clients.values() for call in client.calls])
    assert "trade-off" not in sent and "benchmark" not in sent.lower()


def test_a_failed_control_reset_leaves_no_run_behind(community, runner_factory, monkeypatch):
    from aiswarm.gitrepo import GitError
    store, conn = community
    add_control(store)

    def broken(agent_id):
        raise GitError("disk trouble")

    monkeypatch.setattr(store, "reset_to_seed", broken)
    runner = runner_factory(store, conn, {})
    with pytest.raises(GitError):
        runner.start("G", [*AGENTS, "d"], goal_id="best-fruit")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


async def test_start_records_the_process(community, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, {})
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (runner.start("G", AGENTS),)).fetchone()[0] == 1
    assert conn.execute("SELECT process FROM runs WHERE id = ?",
                        (runner.start("G", AGENTS, process=2),)).fetchone()[0] == 2
    with pytest.raises(ValueError, match="process must be 1 or 2"):
        runner.start("G", AGENTS, process=3)


async def test_resume_discards_what_an_interrupted_round_left_behind(tmp_path, community, scripted, runner_factory):
    store, conn = community
    run_id = runner_factory(store, conn, {}).start("G", AGENTS)
    board = Board(conn, run_id)
    stray = board.append(1, "a", "propose", {"answer_summary": "Half", "method": "M"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, 'a', 'm', 'r')",
                 (stray.id, run_id))
    conn.execute("INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) "
                 "VALUES (?, 1, 'b', 'invalid_action', 'x', 't')", (run_id,))
    result = await runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS}).run(run_id)
    assert result.status == "closed"
    assert entry_types(conn, run_id).count("propose") == 3
    assert conn.execute("SELECT COUNT(*) FROM failures WHERE run_id = ?", (run_id,)).fetchone()[0] == 0
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert {"event": "round_discarded", "round": 1, "entries": 1}.items() <= next(
        e for e in events if e["event"] == "round_discarded").items()
