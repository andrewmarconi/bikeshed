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


import random  # noqa: E402
import re  # noqa: E402

from aiswarm.config import RunSettings  # noqa: E402
from aiswarm.models.fake import FakeModel  # noqa: E402
from aiswarm.runner import Runner  # noqa: E402


def _scripted(plan, **kwargs):
    """A FakeModel that answers per round: plan maps round -> (view -> action dict). Default: pass."""
    def respond(system, messages):
        view = messages[0]["content"]
        rnd = int(re.search(r"^# Round (\d+)", view, re.M).group(1))
        return json.dumps({"action": plan.get(rnd, lambda v: {"type": "pass"})(view)})

    return FakeModel(respond, **kwargs)


def _find(view, type_, author=None):
    pattern = re.compile(r"^#(\d+) \[r\d+\] .*?\(([a-z][a-z0-9-]*)\) " + re.escape(type_) + r"\b", re.M)
    return [int(m.group(1)) for m in pattern.finditer(view) if author is None or m.group(2) == author]


@pytest.fixture
def scripted():
    return _scripted


@pytest.fixture
def find():
    return _find


@pytest.fixture
def runner_factory(tmp_path):
    def make(store, conn, clients, rng=None, **settings):
        return Runner(settings=RunSettings(**settings), conn=conn, store=store, runs_dir=tmp_path / "runs",
                      clients=clients, rng=rng or random.Random(0))

    return make
