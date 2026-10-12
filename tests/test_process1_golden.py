"""Process 1, end to end, against a recording made before the runner was split into phases.

It covers every model request (system prompt, messages, schema), board entry, event, file, failure and the report.
Regenerate only when Process 1 is meant to change:
    AISWARM_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py
"""

import json
import os
from pathlib import Path

from aiswarm.events import read_events

GOLDEN = Path(__file__).parent / "golden" / "process1_run.json"
AGENTS = ["a", "b", "c"]


def _plans(find):
    def propose(answer):
        return lambda v: {"type": "propose", "answer_summary": answer, "method": f"Thought about {answer}."}

    def write(v):
        return {"type": "write_file", "path": "draft.md", "base_version": 0, "content": "Draft.", "note": "start"}

    return {
        "a": {1: propose("Blue"), 2: write,
              3: lambda v: {"type": "call_consensus", "synthesis": "Blue, drafted in draft.md.",
                            "refs": find(v, "propose", "a"),
                            "credits": [{"agent_id": "b", "contribution": "the draft"}]}},
        "b": {1: propose("Green"), 2: write,
              4: lambda v: {"type": "critique", "target": find(v, "call_consensus")[0], "about": "answer",
                            "points": "Green deserved more thought."}},
        "c": {1: propose("Red"),
              2: lambda v: {"type": "critique", "target": find(v, "propose", "a")[0], "about": "method",
                            "points": "Too quick."},
              3: lambda v: {"type": "bogus"},
              4: lambda v: {"type": "vote", "target": find(v, "call_consensus")[0], "stance": "support",
                            "reason": "Fine."}},
    }


def _record(conn, run_dir, run_id, clients, result):
    def rows(sql):
        return [dict(r) for r in conn.execute(sql, (run_id,))]

    data = {
        "result": [result.status, result.reason, result.call_id],
        "entries": rows("SELECT id, round, author, type, to_, refs, payload FROM entries WHERE run_id = ? ORDER BY id"),
        "files": rows("SELECT path, version, author FROM files WHERE run_id = ? ORDER BY path, version"),
        "failures": rows("SELECT round, agent_id, kind, detail FROM failures WHERE run_id = ? ORDER BY id"),
        "rounds": [r["round"] for r in rows("SELECT round FROM rounds WHERE run_id = ? ORDER BY round")],
        "events": [{k: v for k, v in e.items() if k != "ts"} for e in read_events(run_dir / "events.jsonl")],
        "report": {p.name: p.read_text() for p in sorted((run_dir / "report").iterdir())},
        "workspace": {p.name: p.read_text() for p in sorted((run_dir / "workspace").iterdir()) if p.is_file()},
        "calls": {agent: [{"system": s, "messages": m, "schema": schema} for s, m, schema in client.calls]
                  for agent, client in sorted(clients.items())},
    }
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False).replace(run_id, "<run>") + "\n"


async def test_process1_matches_the_recording(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {agent: scripted(plan) for agent, plan in _plans(find).items()}
    runner = runner_factory(store, conn, clients)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    record = _record(conn, tmp_path / "runs" / run_id, run_id, clients, result)
    if os.environ.get("AISWARM_WRITE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(record)
    assert record == GOLDEN.read_text()
