"""Read-only sources for the watcher: the run's events file and the community store."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiswarm.config import Config
from aiswarm.db import connect_readonly
from aiswarm.profiles import ProfileError, ProfileStore
from aiswarm.watch.state import RunState


class EventTail:
    """Reads new complete lines of an events.jsonl file; a partial last line waits for the next read."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.bad = 0

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        chunk = data[: end + 1]
        self.offset += len(chunk)
        events = []
        for line in chunk.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.bad += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                self.bad += 1
        return events


@dataclass
class Watcher:
    state: RunState
    tail: EventTail
    conn: sqlite3.Connection
    store: ProfileStore
    half_life: float

    def poll(self) -> None:
        for event in self.tail.read():
            self.state.apply(event)
        self.state.bad_lines = self.tail.bad
        try:
            self.state.refresh(self.conn, self.store, self.half_life)
        except (sqlite3.Error, ValueError, OSError):
            # A locked database, or a profile file caught mid-write (TOML/pydantic errors are ValueErrors):
            # keep what we have and try again next tick.
            pass


def _max_rounds(conn: sqlite3.Connection, run_id: str, default: int) -> int:
    row = conn.execute("SELECT config FROM run_provenance WHERE run_id = ? AND phase = 'start' ORDER BY id LIMIT 1",
                       (run_id,)).fetchone()
    try:
        return int(json.loads(row["config"])["run"]["max_rounds"]) if row else default
    except (KeyError, TypeError, ValueError):
        return default


def open_watcher(cfg: Config, run_id: str | None) -> Watcher:
    try:
        store = ProfileStore(cfg.community_dir, create=False)
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    conn = connect_readonly(cfg.db_path)
    if run_id is None:
        row = conn.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError("no runs yet; start one with `aiswarm run`")
    else:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"no run {run_id!r}")
    state = RunState(row["id"], row["goal"], row["goal_id"] or "ad hoc", row["goal_category"] or "ad hoc",
                     _max_rounds(conn, row["id"], cfg.run.max_rounds))
    return Watcher(state, EventTail(cfg.runs_dir / row["id"] / "events.jsonl"), conn, store,
                   cfg.run.reputation_half_life_runs)
