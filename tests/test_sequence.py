import json

from bikeshed.sequence import Blocker, blocking, postrun_unfinished


def add(conn, run_id, status):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, 'g', ?, '[]', 't')",
                 (run_id, status))


def start_postrun(conn, runs_dir, run_id, *, finished):
    conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, status, updated_at) "
                 "VALUES (?, 'journal', 'a', 'done', 't')", (run_id,))
    events = runs_dir / run_id / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    names = ["postrun_started"] + (["postrun_finished"] if finished else [])
    events.write_text("".join(json.dumps({"event": name}) + "\n" for name in names))


def test_nothing_blocks_an_empty_community(community, tmp_path):
    _, conn = community
    assert blocking(conn, tmp_path / "runs") is None


def test_the_oldest_unclosed_run_blocks_and_says_how_to_clear_it(community, tmp_path):
    _, conn = community
    add(conn, "r1", "closed")
    add(conn, "r2", "paused")
    add(conn, "r3", "running")
    found = blocking(conn, tmp_path / "runs")
    assert found == Blocker("r2", "paused", ("bikeshed run --resume r2", "bikeshed abandon r2"))
    assert found.message() == "run r2 is paused; clear it first with `bikeshed run --resume r2` or `bikeshed abandon r2`"


def test_a_run_never_blocks_itself(community, tmp_path):
    _, conn = community
    add(conn, "r1", "paused")
    assert blocking(conn, tmp_path / "runs", run_id="r1") is None
    add(conn, "r2", "running")
    assert blocking(conn, tmp_path / "runs", run_id="r1").run_id == "r2"


def test_an_unfinished_postrun_blocks_runs_but_not_a_postrun(community, tmp_path):
    _, conn = community
    runs = tmp_path / "runs"
    add(conn, "r1", "closed")
    start_postrun(conn, runs, "r1", finished=False)
    assert blocking(conn, runs) == Blocker("r1", "closed with its post-run unfinished", ("bikeshed postrun r1",))
    assert blocking(conn, runs, run_id="r2").run_id == "r1"  # resuming another run
    assert blocking(conn, runs, run_id="r1", for_postrun=True) is None
    add(conn, "r2", "closed")
    assert blocking(conn, runs, run_id="r2", for_postrun=True) is None


def test_a_postrun_is_blocked_by_another_unclosed_run(community, tmp_path):
    _, conn = community
    add(conn, "r1", "closed")
    add(conn, "r2", "paused")
    assert blocking(conn, tmp_path / "runs", run_id="r1", for_postrun=True).run_id == "r2"


def test_finished_skipped_and_abandoned_postruns_block_nothing(community, tmp_path):
    _, conn = community
    runs = tmp_path / "runs"
    add(conn, "done", "closed")
    start_postrun(conn, runs, "done", finished=True)
    add(conn, "skipped", "closed")  # --no-postrun: its post-run never started
    add(conn, "gone", "closed")  # abandoned: no post-run
    assert blocking(conn, runs) is None
    assert not postrun_unfinished(conn, runs, "skipped")
