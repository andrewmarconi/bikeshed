"""SQLite storage: connection, migrations, transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'closed')),
        ended_reason TEXT,
        agent_ids TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE rounds (
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        workspace_commit TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (run_id, round)
    );
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        author TEXT NOT NULL,
        type TEXT NOT NULL,
        to_ TEXT NOT NULL DEFAULT 'all',
        refs TEXT NOT NULL DEFAULT '[]',
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX entries_by_run ON entries (run_id, id);
    CREATE TABLE files (
        run_id TEXT NOT NULL REFERENCES runs(id),
        path TEXT NOT NULL,
        version INTEGER NOT NULL,
        author TEXT NOT NULL,
        git_commit TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, path, version)
    );
    CREATE TABLE failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        agent_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE traces (
        entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
        run_id TEXT NOT NULL REFERENCES runs(id),
        agent_id TEXT NOT NULL,
        model TEXT,
        reasoning TEXT NOT NULL
    );
    """,
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
