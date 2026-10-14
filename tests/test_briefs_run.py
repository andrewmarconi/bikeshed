import json

import pytest

from bikeshed.agent import SCORER_BRIEFS
from bikeshed.briefs import EMPTY, BriefSpec, assign
from bikeshed.briefs import load as load_briefs
from bikeshed.events import read_events
from bikeshed.models.fake import FakeModel
from bikeshed.scorer import briefed_request

SPEC = BriefSpec("Shared facts.", ("Use the list.",),
                 (("SECRET-ONE text.", ("SECRET-ONE rule.",)), ("SECRET-TWO text.", ())))


def test_start_stores_the_briefs(community, runner_factory):
    store, conn = community
    run_id = runner_factory(store, conn, {}).start("Plan a picnic.", ["a", "b", "c"], brief=SPEC)
    seed = conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    assert load_briefs(conn, run_id) == assign(SPEC, ["a", "b", "c"], seed)


def test_start_refuses_more_private_briefs_than_agents(community, runner_factory):
    store, conn = community
    with pytest.raises(ValueError, match="the goal has 2 private briefs but the run has 1 agent$"):
        runner_factory(store, conn, {}).start("G", ["a"], brief=SPEC)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_the_refusal_comes_before_control_agents_are_reset(community, runner_factory):
    store, conn = community
    store.create("d", "fake", "Seed temperament.", control=True)
    store.set_public("d", "Dee", "Dee headline", "I am Dee.")
    store.save_seed_public("d")
    store.write_private("d", "lessons.md", "LEARNED")
    with pytest.raises(ValueError, match="private briefs"):
        runner_factory(store, conn, {}).start("G", ["d"], brief=SPEC)
    assert store.private("d")["lessons.md"] == "LEARNED"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_a_run_without_a_brief_stores_none(community, runner_factory):
    store, conn = community
    run_id = runner_factory(store, conn, {}).start("G", ["a", "b"])
    assert load_briefs(conn, run_id) == EMPTY


@pytest.mark.parametrize("process", [1, 2])
async def test_every_model_request_keeps_private_briefs_private(community, runner_factory, process):
    store, conn = community
    clients = {a: FakeModel() for a in "abc"}  # every reply is a pass; we only read what each was sent
    runner = runner_factory(store, conn, clients, max_rounds=1)
    run_id = runner.start("Plan a picnic.", ["a", "b", "c"], brief=SPEC, process=process)
    await runner.run(run_id)
    briefs = load_briefs(conn, run_id)
    for agent, client in clients.items():
        sent = "\n".join([system for system, _, _ in client.calls]
                         + [m["content"] for _, messages, _ in client.calls for m in messages])
        assert "Shared facts." in sent and SCORER_BRIEFS in client.calls[0][0]
        for brief in briefs.private:
            for secret in (brief.text, *(c.text for c in brief.constraints)):
                assert (secret in sent) == (brief.agent_id == agent), (agent, secret)


AGENTS = ["a", "b", "c"]
CALLOUT_SPEC = BriefSpec("Shared.", ("Use the list.",), (("One.", ("C-one.",)), ("Two.", ("C-two.",))))
NAMES = {"a": "Alpha", "b": "Bea", "c": "Cyd"}


def clients_for(scripted, find):
    """a proposes, then calls in round 2; b critiques and c supports the call in round 3, so it passes."""
    def propose(v):
        return {"type": "propose", "answer_summary": "Blue", "method": "Gut"}

    def call(v):
        return {"type": "call_consensus", "synthesis": "Blue it is", "refs": find(v, "propose"),
                "credits": [{"agent_id": "a", "contribution": "the call"}]}

    def critique(v):
        return {"type": "critique", "target": find(v, "call_consensus")[-1], "about": "answer", "points": "Hm."}

    def support(v):
        return {"type": "vote", "target": find(v, "call_consensus")[-1], "stance": "support", "reason": "Fine."}

    return {"a": scripted({1: propose, 2: call}), "b": scripted({1: propose, 3: critique}),
            "c": scripted({1: propose, 3: support})}


def rejection(conn, run_id):
    row = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'scorer_rejected'", (run_id,)).fetchone()
    return json.loads(row[0])


async def test_a_conflict_names_the_holder_and_is_recorded(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    reply = json.dumps({"answers_goal": True, "feedback": "", "conflicts": ["p2.c1", "s.c1", "p2.c1"]})
    scorer = FakeModel([reply])
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=scorer, max_rounds=4)
    run_id = runner.start("Pick a colour", AGENTS, brief=CALLOUT_SPEC)
    briefs = load_briefs(conn, run_id)
    holder = briefs.private[1].agent_id
    result = await runner.run(run_id)
    assert result.reason == "budget"
    assert scorer.calls[0][1][0]["content"] == briefed_request("Pick a colour", "Blue it is", briefs)
    payload = rejection(conn, run_id)
    assert payload["feedback"] == (f"The synthesis conflicts with a constraint in {NAMES[holder]} ({holder})'s "
                                   "private brief. The synthesis conflicts with a constraint in the shared brief.")
    assert payload["named"] == [holder]
    rows = [tuple(r) for r in conn.execute(
        "SELECT call_id, constraint_id FROM scorer_conflicts WHERE run_id = ? ORDER BY id", (run_id,))]
    assert rows == [(payload["call_id"], "p2.c1"), (payload["call_id"], "s.c1")]
    feedback = (tmp_path / "runs" / run_id / "workspace" / "scorer_feedback.md").read_text()
    assert "C-two." not in feedback and NAMES[holder] in feedback
    ruled = next(e for e in read_events(tmp_path / "runs" / run_id / "events.jsonl") if e["event"] == "scorer_ruled")
    assert (ruled["accepted"], ruled["answers_goal"], ruled["conflicts"]) == (False, True, ["p2.c1", "s.c1"])


async def test_an_off_goal_ruling_shows_its_feedback_first(community, scripted, find, runner_factory):
    store, conn = community
    reply = json.dumps({"answers_goal": False, "feedback": "No colour is chosen.", "conflicts": ["p1.c1"]})
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel([reply]), max_rounds=4)
    run_id = runner.start("Pick a colour", AGENTS, brief=CALLOUT_SPEC)
    holder = load_briefs(conn, run_id).private[0].agent_id
    await runner.run(run_id)
    assert rejection(conn, run_id)["feedback"] == (
        f"No colour is chosen. The synthesis conflicts with a constraint in {NAMES[holder]} ({holder})'s private brief.")


async def test_a_briefed_call_without_conflicts_closes_the_run(community, scripted, find, runner_factory):
    store, conn = community
    reply = json.dumps({"answers_goal": True, "feedback": "", "conflicts": []})
    runner = runner_factory(store, conn, clients_for(scripted, find), scorer=FakeModel([reply]))
    run_id = runner.start("Pick a colour", AGENTS, brief=CALLOUT_SPEC)
    result = await runner.run(run_id)
    assert result.reason == "consensus"
    assert conn.execute("SELECT COUNT(*) FROM scorer_conflicts").fetchone()[0] == 0
