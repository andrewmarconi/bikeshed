import hashlib
import json
from importlib.resources import files

import pytest

import aiswarm.provenance as provenance
from aiswarm.config import Config
from aiswarm.gitrepo import GitRepo
from aiswarm.provenance import capture, code_state, probe_server, prompt_snapshot, provenance_rows


def make_config(**run):
    return Config.model_validate({
        "models": {
            "local": {"kind": "openai", "model": "m", "base_url": "http://127.0.0.1:9/v1", "api_key": "SECRET-KEY"},
            "claude": {"kind": "claude_cli", "model": "haiku"},
        },
        "run": run,
    })


async def fake_probe(backend):
    return {"models": [backend.model], "health": {"status": "healthy"}, "error": None}


@pytest.fixture
def run_row(tmp_path, community, make_run):
    store, conn = community
    run_id = make_run(conn, "r1", ("a", "b"))
    conn.execute("UPDATE runs SET seed = 42 WHERE id = 'r1'")
    return conn, run_id


async def test_capture_stores_a_row_without_api_keys(run_row):
    conn, run_id = run_row
    prov = await capture(conn, run_id, "start", make_config(), {"a": "local", "b": "claude"}, probe=fake_probe)
    assert prov.seed == 42 and prov.phase == "start"
    assert prov.models["a"]["backend"] == "local" and prov.models["b"]["model"] == "haiku"
    assert prov.servers == {"http://127.0.0.1:9/v1": {"models": ["m"], "health": {"status": "healthy"}, "error": None}}
    row = dict(conn.execute("SELECT * FROM run_provenance").fetchone())
    assert "SECRET-KEY" not in json.dumps(row)
    assert [p.phase for p in provenance_rows(conn, run_id)] == ["start"]


def test_prompt_snapshot_hashes_every_template():
    snapshot = prompt_snapshot()
    names = sorted(p.name for p in files("aiswarm.prompts").iterdir() if p.name.endswith(".md"))
    assert sorted(snapshot) == names
    text = files("aiswarm.prompts").joinpath("journal_request.md").read_text()
    assert snapshot["journal_request.md"] == {"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}


async def test_fingerprint_is_stable_and_tracks_prompt_changes(run_row, monkeypatch):
    conn, run_id = run_row
    cfg, backends = make_config(), {"a": "local"}
    first = await capture(conn, run_id, "start", cfg, backends, probe=fake_probe)
    second = await capture(conn, run_id, "resume", cfg, backends, probe=fake_probe)
    assert first.fingerprint == second.fingerprint
    changed = prompt_snapshot()
    changed["journal_request.md"] = {"sha256": "different", "text": "x"}
    monkeypatch.setattr(provenance, "prompt_snapshot", lambda: changed)
    third = await capture(conn, run_id, "postrun", cfg, backends, probe=fake_probe)
    assert third.fingerprint != first.fingerprint


async def test_a_failing_probe_is_recorded_not_raised(run_row):
    conn, run_id = run_row

    async def broken(backend):
        raise RuntimeError("boom")

    prov = await capture(conn, run_id, "start", make_config(), {"a": "local"}, probe=broken)
    assert prov.servers["http://127.0.0.1:9/v1"]["error"] == "RuntimeError: boom"


async def test_probe_server_reports_an_unreachable_server():
    result = await probe_server(make_config().models["local"])
    assert result["models"] is None and result["error"]


def test_code_state_outside_git(tmp_path):
    assert code_state(tmp_path) == (None, None)


def test_code_state_in_a_checkout(tmp_path):
    repo = GitRepo.init(tmp_path / "code")
    (repo.path / "x.py").write_text("x = 1\n")
    sha = repo.commit(["x.py"], "m", author="dev")
    assert code_state(repo.path) == (sha, False)
    (repo.path / "y.py").write_text("y = 1\n")
    assert code_state(repo.path) == (sha, True)


from aiswarm.provenance import provenance_changes  # noqa: E402


async def test_changes_name_what_differs_from_the_previous_run(community, make_run, monkeypatch):
    store, conn = community
    make_run(conn, "r1", ("a",))
    make_run(conn, "r2", ("a",))
    await capture(conn, "r1", "start", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    assert provenance_changes(conn, "r1") == []
    changed = prompt_snapshot()
    changed["journal_request.md"] = {"sha256": "different", "text": "x"}
    monkeypatch.setattr(provenance, "prompt_snapshot", lambda: changed)
    await capture(conn, "r2", "start", make_config(max_rounds=4), {"a": "local", "b": "claude"}, probe=fake_probe)
    changes = provenance_changes(conn, "r2")
    assert "since run r1: prompts journal_request.md" in changes
    assert "since run r1: config run.max_rounds" in changes
    assert "since run r1: models for b" in changes


async def test_changes_flag_a_harness_change_within_a_run(community, make_run):
    store, conn = community
    make_run(conn, "r1", ("a",))
    await capture(conn, "r1", "start", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    await capture(conn, "r1", "resume", make_config(max_rounds=12), {"a": "local"}, probe=fake_probe)
    assert provenance_changes(conn, "r1") == []
    await capture(conn, "r1", "postrun", make_config(max_rounds=3), {"a": "local"}, probe=fake_probe)
    [line] = provenance_changes(conn, "r1")
    assert line.startswith("at postrun (") and line.endswith("config run.max_rounds")


def test_changes_for_a_run_without_provenance_are_empty(community, make_run):
    store, conn = community
    make_run(conn, "old", ("a",))
    assert provenance_changes(conn, "old") == []
