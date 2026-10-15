"""observer.db: the Observer's own state (judgment cache, codebook). Never read by any agent."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bikeshed.db import now_iso

MIGRATIONS: list[str] = [
    """
    CREATE TABLE judgments (
        key TEXT PRIMARY KEY,
        analysis TEXT NOT NULL,
        run_id TEXT NOT NULL,
        agent_id TEXT,
        prompt_sha256 TEXT NOT NULL,
        model_alias TEXT NOT NULL,
        model_reported TEXT,
        status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX judgments_by_run ON judgments (run_id, analysis);
    CREATE TABLE codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL
    );
    CREATE TABLE labels (
        label TEXT PRIMARY KEY,
        code_id INTEGER NOT NULL REFERENCES codes(id)
    );
    """,
]


@dataclass(frozen=True)
class Judgment:
    analysis: str
    run_id: str
    agent_id: str | None
    status: str  # "ok" | "failed"
    result: dict[str, Any] | None
    error: str | None
    model_reported: str | None
    prompt_sha256: str


def _judgment(row: sqlite3.Row) -> Judgment:
    return Judgment(row["analysis"], row["run_id"], row["agent_id"], row["status"],
                    json.loads(row["result"]) if row["result"] else None, row["error"], row["model_reported"],
                    row["prompt_sha256"])


class ObserverStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            self.conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")

    def cached(self, key: str, *, include_failed: bool = False) -> Judgment | None:
        sql = "SELECT * FROM judgments WHERE key = ?" + ("" if include_failed else " AND status = 'ok'")
        row = self.conn.execute(sql, (key,)).fetchone()
        return _judgment(row) if row else None

    def save(self, key: str, judgment: Judgment, model_alias: str) -> None:
        """Store a judgment as the only one for its unit (analysis, run, agent); older inputs or prompts are dropped."""
        self.conn.execute(
            "DELETE FROM judgments WHERE analysis = ? AND run_id = ? AND agent_id IS ? AND key != ?",
            (judgment.analysis, judgment.run_id, judgment.agent_id, key),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO judgments (key, analysis, run_id, agent_id, prompt_sha256, model_alias, "
            "model_reported, status, result, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, judgment.analysis, judgment.run_id, judgment.agent_id, judgment.prompt_sha256, model_alias,
             judgment.model_reported, judgment.status,
             None if judgment.result is None else json.dumps(judgment.result, ensure_ascii=False),
             judgment.error, now_iso()),
        )

    def drop(self, analysis: str, run_id: str, agent_id: str | None) -> None:
        """Forget a unit's judgment once it has nothing left to judge, so an old one is not reported."""
        self.conn.execute("DELETE FROM judgments WHERE analysis = ? AND run_id = ? AND agent_id IS ?",
                          (analysis, run_id, agent_id))

    def judgments(self, run_id: str, analysis: str | None = None) -> list[Judgment]:
        sql, args = "SELECT * FROM judgments WHERE run_id = ?", [run_id]
        if analysis is not None:
            sql, args = sql + " AND analysis = ?", [*args, analysis]
        return [_judgment(r) for r in self.conn.execute(sql + " ORDER BY created_at, key", args)]

    def failed(self) -> list[Judgment]:
        rows = self.conn.execute("SELECT * FROM judgments WHERE status = 'failed' ORDER BY run_id, key")
        return [_judgment(r) for r in rows]

    def code_for(self, label: str) -> str | None:
        row = self.conn.execute(
            "SELECT c.name FROM labels l JOIN codes c ON c.id = l.code_id WHERE l.label = ?", (label,)).fetchone()
        return row["name"] if row else None

    def codes(self) -> list[tuple[str, str]]:
        rows = self.conn.execute("SELECT name, description FROM codes ORDER BY name")
        return [(r["name"], r["description"]) for r in rows]

    def assign(self, label: str, code: str, description: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO codes (name, description) VALUES (?, ?)", (code, description))
        code_id = self.conn.execute("SELECT id FROM codes WHERE name = ?", (code,)).fetchone()["id"]
        self.conn.execute("INSERT OR REPLACE INTO labels (label, code_id) VALUES (?, ?)", (label, code_id))
