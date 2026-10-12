"""The board: an append-only log of typed, attributed entries for one run."""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aiswarm.db import now_iso

SYSTEM = "system"


@dataclass(frozen=True)
class Entry:
    id: int
    run_id: str
    round: int
    author: str
    type: str
    payload: dict[str, Any]
    refs: list[int]
    to: str
    created_at: str
    visible_to: tuple[str, ...] | None = None  # None: every member; otherwise only these agents may see it


def _entry(row: sqlite3.Row) -> Entry:
    hidden = row["visible_to"] if "visible_to" in row.keys() else None  # read-only readers may face an older schema
    return Entry(
        id=row["id"], run_id=row["run_id"], round=row["round"], author=row["author"], type=row["type"],
        payload=json.loads(row["payload"]), refs=json.loads(row["refs"]), to=row["to_"],
        created_at=row["created_at"], visible_to=tuple(json.loads(hidden)) if hidden else None,
    )


def visible(entries: Sequence[Entry], agent_id: str) -> list[Entry]:
    """What one agent may see: public entries, and private ones that name it."""
    return [e for e in entries if e.visible_to is None or agent_id in e.visible_to]


def public(entries: Sequence[Entry]) -> list[Entry]:
    return [e for e in entries if e.visible_to is None]


class Board:
    def __init__(self, conn: sqlite3.Connection, run_id: str):
        self._conn = conn
        self.run_id = run_id

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def append(
        self, round: int, author: str, type: str, payload: dict[str, Any],
        refs: Sequence[int] = (), to: str = "all", visible_to: Sequence[str] | None = None,
    ) -> Entry:
        created = now_iso()
        hidden = tuple(visible_to) if visible_to is not None else None
        cursor = self._conn.execute(
            "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at, visible_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, round, author, type, to, json.dumps(list(refs)),
             json.dumps(payload, ensure_ascii=False), created, json.dumps(list(hidden)) if hidden else None),
        )
        return Entry(cursor.lastrowid, self.run_id, round, author, type, payload, list(refs), to, created, hidden)

    def entries(self) -> list[Entry]:
        rows = self._conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (self.run_id,))
        return [_entry(row) for row in rows]

    def entries_for(self, agent_id: str) -> list[Entry]:
        """Every agent-facing read goes through here (or `visible`); only the harness and observers read all."""
        return visible(self.entries(), agent_id)

    def get(self, entry_id: int) -> Entry | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE run_id = ? AND id = ?", (self.run_id, entry_id)
        ).fetchone()
        return _entry(row) if row else None
