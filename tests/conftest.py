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
    def make(store, conn, clients, rng=None, scorer=None, **settings):
        return Runner(settings=RunSettings(**settings), conn=conn, store=store, runs_dir=tmp_path / "runs",
                      clients=clients, rng=rng or random.Random(0), scorer=scorer)

    return make


def _postrun_reply(request):
    """A valid default reply for a post-run request, or None if the request is not one."""
    marker = re.match(r"# Post-run: (\w+)", request)
    if not marker:
        return None
    step = marker.group(1)
    if step == "journal":
        return json.dumps({"journal": "Thoughts."})
    if step == "review":
        ids = [int(i) for i in re.findall(r"^#(\d+) \[r", request, re.M)]
        ratings = [{"dimension": name, "rating": 3, "reason": "Fine."}
                   for name in re.findall(r'^- "(.+?)": ', request, re.M)]
        reply = {"more_of": "More.", "less_of": "Less.", "private_note": "Between us.",
                 "evidence": ids[:1] or [1], "overall": 4, "overall_reason": "Solid."}
        if ratings:  # only when the request lists rating dimensions (Task 9)
            reply["dimension_ratings"] = ratings
        return json.dumps(reply)
    if step == "responses":
        ids = [int(i) for i in re.findall(r"^Review (\d+) of", request, re.M)]
        return json.dumps({"responses": [{"review_id": i, "stance": "accept", "reason": "Fair."} for i in ids]})
    if step == "update":
        return json.dumps({"journal": "After reviews.", "lessons": "Listen first.", "self_description": None,
                           "temperament": None, "peer_notes": None, "public_profile": None, "proposal": None})
    if step == "norms_critique":
        ids = [int(i) for i in re.findall(r"^Proposal (\d+) by", request, re.M)]
        return json.dumps({"critiques": [{"proposal_id": i, "points": "Consider edge cases."} for i in ids]})
    if step == "norms_vote":
        ids = [int(i) for i in re.findall(r"^Proposal (\d+) by", request, re.M)]
        return json.dumps({"votes": [{"proposal_id": i, "stance": "support", "reason": "Agreed."} for i in ids]})
    if step == "narrative":
        return json.dumps({"narrative": "It went the way the account says."})
    raise ValueError(f"no default reply for post-run step {step!r}")


@pytest.fixture
def postrun_reply():
    return _postrun_reply


def _process2(react=None, think=None, poll=None, **kwargs):
    """A FakeModel for Process 2. Each plan maps round -> (request text -> action dict).

    Defaults: think writes "note rN", react passes, poll abstains.
    """
    def respond(system, messages):
        content = messages[0]["content"]
        rnd = int(re.search(r"^# Round (\d+)", content, re.M).group(1))
        if "\n# Think\n" in content:
            plan, default = think or {}, lambda v: {"type": "note", "text": f"note r{rnd}"}
        elif "\n# Poll\n" in content:
            plan, default = poll or {}, lambda v: {"type": "poll_answer", "stance": "abstain", "reason": "Unsure."}
        else:
            plan, default = react or {}, lambda v: {"type": "pass"}
        return json.dumps({"action": plan.get(rnd, default)(content)})

    return FakeModel(respond, **kwargs)


@pytest.fixture
def process2():
    return _process2
