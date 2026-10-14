import pytest

from bikeshed.briefs import EMPTY, BriefSpec, assign
from bikeshed.briefs import load as load_briefs

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
