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
    """
    CREATE TABLE reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        reviewer TEXT NOT NULL,
        reviewee TEXT NOT NULL,
        is_self INTEGER NOT NULL,
        more_of TEXT NOT NULL,
        less_of TEXT NOT NULL,
        private_note TEXT NOT NULL,
        evidence TEXT NOT NULL,
        overall INTEGER NOT NULL CHECK (overall BETWEEN 1 AND 5),
        overall_reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, reviewer, reviewee)
    );
    CREATE TABLE review_responses (
        review_id INTEGER PRIMARY KEY REFERENCES reviews(id),
        stance TEXT NOT NULL CHECK (stance IN ('accept', 'dispute')),
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE journals (
        run_id TEXT NOT NULL REFERENCES runs(id),
        agent_id TEXT NOT NULL,
        section TEXT NOT NULL CHECK (section IN ('before', 'after')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, agent_id, section)
    );
    CREATE TABLE postrun_steps (
        run_id TEXT NOT NULL REFERENCES runs(id),
        step TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        subject TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN ('done', 'failed')),
        detail TEXT NOT NULL DEFAULT '',
        reasoning TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, step, agent_id, subject)
    );
    """,
    """
    CREATE TABLE methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        entry_id INTEGER NOT NULL REFERENCES entries(id),
        author TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('propose', 'refine')),
        goal TEXT NOT NULL,
        text TEXT NOT NULL,
        reception TEXT NOT NULL,
        embedding BLOB,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, entry_id)
    );
    CREATE TABLE norm_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        author TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('norm', 'amendment', 'dimension')),
        text TEXT NOT NULL DEFAULT '',
        target INTEGER,
        name TEXT NOT NULL DEFAULT '',
        question TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'adopted', 'rejected')),
        created_at TEXT NOT NULL,
        UNIQUE (run_id, author)
    );
    CREATE TABLE norm_critiques (
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        author TEXT NOT NULL,
        points TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (proposal_id, author)
    );
    CREATE TABLE norm_votes (
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        author TEXT NOT NULL,
        stance TEXT NOT NULL CHECK (stance IN ('support', 'object')),
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (proposal_id, author)
    );
    CREATE TABLE norms (
        number INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        updated_at TEXT NOT NULL
    );
    CREATE TABLE dimensions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        question TEXT NOT NULL,
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        adopted_run TEXT NOT NULL REFERENCES runs(id)
    );
    CREATE TABLE review_ratings (
        review_id INTEGER NOT NULL REFERENCES reviews(id),
        dimension_id INTEGER NOT NULL REFERENCES dimensions(id),
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        reason TEXT NOT NULL,
        PRIMARY KEY (review_id, dimension_id)
    );
    """,
    """
    ALTER TABLE runs ADD COLUMN seed INTEGER;
    ALTER TABLE runs ADD COLUMN goal_id TEXT;
    ALTER TABLE runs ADD COLUMN goal_category TEXT;
    ALTER TABLE runs ADD COLUMN is_benchmark INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE run_provenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        phase TEXT NOT NULL CHECK (phase IN ('start', 'resume', 'postrun')),
        code_commit TEXT,
        code_dirty INTEGER,
        config TEXT NOT NULL,
        prompts TEXT NOT NULL,
        models TEXT NOT NULL,
        servers TEXT NOT NULL,
        seed INTEGER,
        fingerprint TEXT NOT NULL,
        captured_at TEXT NOT NULL
    );
    CREATE INDEX run_provenance_by_run ON run_provenance (run_id, id);
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


def connect_readonly(path: Path) -> sqlite3.Connection:
    """A connection that cannot write: for observers of the community, never for the runner."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")  # fail now, not later, if the file is missing
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
