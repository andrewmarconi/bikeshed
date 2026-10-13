from bikeshed.watch.source import EventTail


def test_reads_only_new_complete_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    tail = EventTail(path)
    assert tail.read() == []  # no file yet
    path.write_text('{"event": "a"}\n{"event": "b"}\n{"event": "c"')
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.read() == []
    with path.open("a") as f:
        f.write(', "x": 1}\n{"event": "d"}\n')
    assert [e["event"] for e in tail.read()] == ["c", "d"]
    assert tail.bad == 0


def test_broken_lines_are_skipped_and_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event": "a"}\nnot json\n[1, 2]\n\n{"event": "b"}\n')
    tail = EventTail(path)
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.bad == 2


import json  # noqa: E402

import pytest  # noqa: E402
from observer_helpers import add_entry, add_run  # noqa: E402

from bikeshed.config import Config  # noqa: E402
from bikeshed.watch.source import open_watcher  # noqa: E402


def cfg_for(tmp_path):
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    return cfg


def test_open_watcher_errors(tmp_path, community):
    cfg = cfg_for(tmp_path)
    with pytest.raises(ValueError, match="no runs yet"):
        open_watcher(cfg, None)
    store, conn = community
    add_run(conn, "r1")
    with pytest.raises(ValueError, match="no run 'nope'"):
        open_watcher(cfg, "nope")
    cfg.community_dir = tmp_path / "elsewhere"
    with pytest.raises(ValueError):
        open_watcher(cfg, None)


def test_watcher_waits_then_follows_events_and_entries(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1", status="running", goal_id="best-fruit", category="unsettled")
    add_run(conn, "r2", status="running")
    watcher = open_watcher(cfg, "r1")
    watcher.poll()
    assert watcher.state.phase == "waiting" and watcher.state.label == "best-fruit"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    watcher.poll()
    assert watcher.state.phase == "rounds" and watcher.state.round == 1
    assert len(watcher.state.feed) == 1
    assert open_watcher(cfg, None).state.run_id == "r2"  # latest by default


def test_max_rounds_comes_from_the_runs_start_provenance(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    conn.execute("INSERT INTO run_provenance (run_id, phase, config, prompts, models, servers, fingerprint, "
                 "captured_at) VALUES ('r1', 'start', ?, '{}', '{}', '{}', 'f', 't')",
                 (json.dumps({"run": {"max_rounds": 6}}),))
    assert open_watcher(cfg, "r1").state.max_rounds == 6


def test_a_half_written_profile_or_odd_event_never_crashes_the_watcher(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"))
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    watcher = open_watcher(cfg, "r1")
    watcher.poll()
    assert len(watcher.state.feed) == 1
    profile = cfg.community_dir / "agents" / "a" / "public.toml"
    good = profile.read_text()
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"event": "agent_step_started"}\n{"event": "action_posted", "type": "pass"}\n')
    add_entry(conn, "r1", "b", "pass", {})
    for broken in ('name = "Al', ""):  # mid-write, then truncated
        profile.write_text(broken)
        watcher.state.profiles_stale = True
        watcher.poll()
        assert len(watcher.state.feed) == 1  # previous state kept
    profile.write_text(good)
    watcher.poll()
    assert len(watcher.state.feed) == 2


def test_open_watcher_knows_the_process(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2", process=2)
    assert open_watcher(cfg, "r1").state.process == 1
    assert open_watcher(cfg, "r2").state.process == 2


def test_open_watcher_reads_a_store_from_before_process_2(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    conn.execute("ALTER TABLE runs DROP COLUMN process")
    assert open_watcher(cfg, "r1").state.process == 1
