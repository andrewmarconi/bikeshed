import base64
import csv
import json
import sqlite3

import pytest
from observer_helpers import add_entry, add_review, add_run

from bikeshed.config import Config
from bikeshed.export import export
from test_observer_isolation import _fingerprint


def _cfg(tmp_path):
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    cfg.observer.dir = tmp_path / "observer"
    return cfg


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _seed(conn):
    add_run(conn, "r1", agents=("a", "b", "c"), created="2026-10-01T00:00:00+00:00", ended="consensus",
            goal="Pick a fruit.", process=2)
    add_run(conn, "r2", created="2026-10-05T00:00:00+00:00")
    add_entry(conn, "r1", "a", "think", {"text": "I prefer apples."}, visible_to=["a"])
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Apples", "method": "taste"})
    call = add_entry(conn, "r1", "b", "call_consensus", {"synthesis": "Apples", "refs": [], "credits": []}, round=2)
    add_entry(conn, "r1", "a", "vote", {"target": call, "stance": "support", "reason": "same"}, round=2)
    add_entry(conn, "r1", "c", "vote", {"target": call, "stance": "support", "reason": "same"}, round=2)
    add_entry(conn, "r1", "b", "vote", {"target": call, "stance": "object", "reason": "other"}, round=2)
    add_entry(conn, "r1", "c", "critique", {"target": call, "about": "answer", "points": "too sweet"}, round=2)
    add_entry(conn, "r2", "a", "propose", {"answer_summary": "Pears", "method": "m"})
    add_review(conn, "r1", "a", "b", 4)
    add_review(conn, "r1", "b", "a", 2)
    add_review(conn, "r1", "a", "a", 5)
    conn.execute("INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) "
                 "VALUES ('r1', 2, 'c', 'invalid_action', 'too long', 't')")
    conn.execute("INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, embedding, created_at) "
                 "VALUES ('r1', 2, 'a', 'propose', 'g', 'taste', '{}', ?, 't')", (b"\x00\x01\xff",))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def test_every_table_is_exported_with_all_its_rows(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%'")]
    manifest = json.loads((out / "manifest.json").read_text())
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        path = out / "community" / f"{table}.jsonl"
        assert path.exists(), table
        assert len(path.read_text().splitlines()) == count, table
        assert manifest["community"]["tables"][table] == count
    assert "CREATE TABLE entries" in (out / "community" / "schema.sql").read_text()


def test_blobs_are_base64_encoded(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    (method,) = _rows(out / "community" / "methods.jsonl")
    assert method["embedding"] == {"base64": base64.b64encode(b"\x00\x01\xff").decode()}


def test_runs_csv_summarises_each_run(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    rows = {r["run_id"]: r for r in csv.DictReader((out / "runs.csv").open())}
    r1 = rows["r1"]
    assert r1["goal"] == "Pick a fruit."
    assert r1["votes"] == "3" and r1["non_support_votes"] == "1" and r1["unique_vote_reasons"] == "2"
    assert r1["critiques"] == "1" and r1["calls"] == "1" and r1["proposals"] == "1"
    assert r1["failures"] == "1" and r1["think_notes"] == "1"
    assert r1["peer_reviews"] == "2" and r1["mean_peer_rating"] == "3.0" and r1["mean_self_rating"] == "5.0"
    assert set(rows) == {"r1", "r2"}


def _provenance(conn, run_id, phase, model, max_rounds, commit="abc"):
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, ?, 0, ?, '{}', ?, '{}', 1, 'f', 't')",
        (run_id, phase, commit, json.dumps({"run": {"max_rounds": max_rounds, "close_rule": "critiqued"}}),
         json.dumps({"a": {"backend": "x", "model": model}})))


def test_runs_csv_lists_every_model_and_the_last_round_cap(tmp_path, community):
    _, conn = community
    _seed(conn)
    _provenance(conn, "r1", "start", "deepseek", 12, commit="start-commit")
    _provenance(conn, "r1", "resume", "qwen", 8, commit="resume-commit")
    _provenance(conn, "r1", "postrun", "narrator-model", 8)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = export(_cfg(tmp_path), tmp_path / "out")
    rows = {r["run_id"]: r for r in csv.DictReader((out / "runs.csv").open())}
    assert rows["r1"]["models"] == "deepseek;qwen" and rows["r1"]["max_rounds"] == "8"
    assert rows["r1"]["code_commit"] == "start-commit"
    assert rows["r2"]["models"] == "" and rows["r2"]["max_rounds"] == "" and rows["r2"]["code_commit"] == ""
    assert "- models: deepseek;qwen;" in (out / "runs" / "r1" / "transcript.md").read_text()


def test_each_run_gets_a_transcript_and_parsed_entries(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    transcript = (out / "runs" / "r1" / "transcript.md").read_text()
    assert "Pick a fruit." in transcript
    assert "private to a" in transcript and "I prefer apples." in transcript
    assert "too sweet" in transcript and "invalid_action" in transcript
    entries = _rows(out / "runs" / "r1" / "entries.jsonl")
    assert entries[0]["payload"] == {"text": "I prefer apples."} and entries[0]["visible_to"] == ["a"]
    assert all(e["run_id"] == "r1" for e in entries)


def test_run_files_on_disk_are_copied(tmp_path, community):
    _, conn = community
    _seed(conn)
    cfg = _cfg(tmp_path)
    (cfg.runs_dir / "r1" / "report").mkdir(parents=True)
    (cfg.runs_dir / "r1" / "events.jsonl").write_text('{"e": 1}\n')
    (cfg.runs_dir / "r1" / "report" / "answer.md").write_text("Apples.")
    out = export(cfg, tmp_path / "out")
    assert (out / "runs" / "r1" / "events.jsonl").read_text() == '{"e": 1}\n'
    assert (out / "runs" / "r1" / "report" / "answer.md").read_text() == "Apples."


def test_filtering_by_run_limits_run_scoped_tables(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out", run_ids=["r2"])
    assert [r["id"] for r in _rows(out / "community" / "runs.jsonl")] == ["r2"]
    assert {r["run_id"] for r in _rows(out / "community" / "entries.jsonl")} == {"r2"}
    assert not (out / "runs" / "r1").exists()


def test_filtering_by_date(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out", since="2026-10-03")
    assert [r["id"] for r in _rows(out / "community" / "runs.jsonl")] == ["r2"]


def test_profile_history_and_current_files_are_exported(tmp_path, community):
    store, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    history = _rows(out / "community" / "profile_history.jsonl")
    temperaments = [h for h in history if h["path"] == "agents/a/temperament.md"]
    assert temperaments and temperaments[0]["content"].strip() == "Temperament of a."
    assert {"commit", "date", "subject"} <= set(temperaments[0])
    assert (out / "community" / "files" / "agents" / "a" / "temperament.md").read_text().strip() == "Temperament of a."


def test_observer_db_is_exported_when_present(tmp_path, community):
    _, conn = community
    _seed(conn)
    cfg = _cfg(tmp_path)
    cfg.observer.dir.mkdir()
    obs = sqlite3.connect(cfg.observer.dir / "observer.db")
    obs.execute("CREATE TABLE judgments (key TEXT, result TEXT)")
    obs.execute("INSERT INTO judgments VALUES ('k', '{\"x\": 1}')")
    obs.commit()
    obs.close()
    out = export(cfg, tmp_path / "out")
    assert _rows(out / "observer" / "judgments.jsonl") == [{"key": "k", "result": '{"x": 1}'}]


def test_no_observer_db_is_fine(tmp_path, community):
    _, conn = community
    _seed(conn)
    out = export(_cfg(tmp_path), tmp_path / "out")
    assert not (out / "observer").exists()
    assert json.loads((out / "manifest.json").read_text())["observer"] is None


def test_export_leaves_the_community_untouched(tmp_path, community):
    _, conn = community
    _seed(conn)
    cfg = _cfg(tmp_path)
    before = _fingerprint(cfg.community_dir)
    export(cfg, tmp_path / "out")
    assert _fingerprint(cfg.community_dir) == before


@pytest.mark.parametrize("inside", ["community", "runs"])
def test_refuses_to_write_inside_the_community_or_runs(tmp_path, community, inside):
    _, conn = community
    _seed(conn)
    cfg = _cfg(tmp_path)
    (tmp_path / "runs").mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="inside"):
        export(cfg, tmp_path / inside / "export")


def test_refuses_a_non_empty_output_directory(tmp_path, community):
    _, conn = community
    _seed(conn)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        export(_cfg(tmp_path), tmp_path / "out")


def test_no_agent_path_module_imports_the_export():
    from test_observer_isolation import AGENT_PATH, SRC, imports

    for rel in AGENT_PATH:
        assert "bikeshed.export" not in imports(SRC / rel), rel
