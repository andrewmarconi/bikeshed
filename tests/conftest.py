import json

import pytest


@pytest.fixture
def make_run():
    def _make_run(conn, run_id="r1", agent_ids=("a", "b")):
        conn.execute(
            "INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'running', ?, 't')",
            (run_id, "goal", json.dumps(list(agent_ids))),
        )
        return run_id

    return _make_run


from aiswarm.db import connect  # noqa: E402
from aiswarm.profiles import ProfileStore  # noqa: E402


@pytest.fixture
def community(tmp_path):
    store = ProfileStore(tmp_path / "community")
    for agent_id, name in [("a", "Alpha"), ("b", "Bea"), ("c", "Cyd")]:
        store.create(agent_id, "fake", f"Temperament of {agent_id}.")
        store.set_public(agent_id, name, f"{name} headline", f"I am {name}.")
    conn = connect(tmp_path / "community" / "community.db")
    return store, conn
