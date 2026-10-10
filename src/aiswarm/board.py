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


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"], run_id=row["run_id"], round=row["round"], author=row["author"], type=row["type"],
        payload=json.loads(row["payload"]), refs=json.loads(row["refs"]), to=row["to_"],
        created_at=row["created_at"],
    )


class Board:
    def __init__(self, conn: sqlite3.Connection, run_id: str):
        self._conn = conn
        self.run_id = run_id

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def append(
        self, round: int, author: str, type: str, payload: dict[str, Any],
        refs: Sequence[int] = (), to: str = "all",
    ) -> Entry:
        created = now_iso()
        cursor = self._conn.execute(
            "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, round, author, type, to, json.dumps(list(refs)),
             json.dumps(payload, ensure_ascii=False), created),
        )
        return Entry(cursor.lastrowid, self.run_id, round, author, type, payload, list(refs), to, created)

    def entries(self) -> list[Entry]:
        rows = self._conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (self.run_id,))
        return [_entry(row) for row in rows]

    def get(self, entry_id: int) -> Entry | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE run_id = ? AND id = ?", (self.run_id, entry_id)
        ).fetchone()
        return _entry(row) if row else None
