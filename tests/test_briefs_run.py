import pytest

from bikeshed.agent import SCORER_BRIEFS
from bikeshed.briefs import EMPTY, BriefSpec, assign
from bikeshed.briefs import load as load_briefs
from bikeshed.models.fake import FakeModel

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
