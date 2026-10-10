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
