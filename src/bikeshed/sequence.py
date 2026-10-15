"""Runs and post-run cycles happen one at a time, so one run's after-run changes never land inside another run."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def postrun_unfinished(conn: sqlite3.Connection, runs_dir: Path, run_id: str) -> bool:
    """A closed run whose post-run cycle has started but not finished (running, paused, or interrupted)."""
    started = conn.execute(
        "SELECT 1 FROM postrun_steps WHERE run_id = ?1 UNION ALL "
        "SELECT 1 FROM run_provenance WHERE run_id = ?1 AND phase = 'postrun' LIMIT 1", (run_id,)).fetchone()
    if not started:
        return False
    events = runs_dir / run_id / "events.jsonl"
    if not events.exists():
        return False  # cannot tell; treat as finished rather than hide the run forever
    last = None
    for line in events.read_text().splitlines():
        try:
            name = json.loads(line).get("event", "")
        except json.JSONDecodeError:
            continue
        if name.startswith("postrun_"):
            last = name
    return last is not None and last != "postrun_finished"


@dataclass(frozen=True)
class Blocker:
    run_id: str
    status: str
    commands: tuple[str, ...]  # any one of them clears the block

    def message(self) -> str:
        options = " or ".join(f"`{c}`" for c in self.commands)
        return f"run {self.run_id} is {self.status}; clear it first with {options}"


def blocking(conn: sqlite3.Connection, runs_dir: Path, *, run_id: str | None = None,
             for_postrun: bool = False) -> Blocker | None:
    """The oldest run, other than `run_id`, that must finish before a run (or, with for_postrun, a post-run) goes on."""
    for row in conn.execute("SELECT id, status FROM runs ORDER BY rowid").fetchall():
        other = row["id"]
        if other == run_id:
            continue
        if row["status"] in ("running", "paused"):
            return Blocker(other, row["status"], (f"bikeshed run --resume {other}", f"bikeshed abandon {other}"))
        if not for_postrun and postrun_unfinished(conn, runs_dir, other):
            return Blocker(other, "closed with its post-run unfinished", (f"bikeshed postrun {other}",))
    return None
