"""Private journals: one file per agent per run, with a before-reviews and an after-reviews section."""

import sqlite3
from typing import Literal

from bikeshed.db import now_iso
from bikeshed.profiles import ProfileStore

JOURNAL_MAX = 6_000
Section = Literal["before", "after"]
_TITLES = {"before": "Before reviews", "after": "After reviews"}


def write_journal(
    conn: sqlite3.Connection, store: ProfileStore, agent_id: str, run_id: str, section: Section, text: str
) -> None:
    conn.execute(
        "INSERT INTO journals (run_id, agent_id, section, text, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id, agent_id, section) DO UPDATE SET text = excluded.text, created_at = excluded.created_at",
        (run_id, agent_id, section, text, now_iso()),
    )
    sections = {
        row["section"]: row["text"]
        for row in conn.execute("SELECT section, text FROM journals WHERE run_id = ? AND agent_id = ?", (run_id, agent_id))
    }
    parts = [f"# Run {run_id}"]
    for key, title in _TITLES.items():
        if key in sections:
            parts += ["", f"## {title}", "", sections[key].strip()]
    store.write_journal_file(agent_id, run_id, "\n".join(parts) + "\n")


def journal_section(conn: sqlite3.Connection, agent_id: str, run_id: str, section: Section) -> str | None:
    row = conn.execute(
        "SELECT text FROM journals WHERE run_id = ? AND agent_id = ? AND section = ?", (run_id, agent_id, section)
    ).fetchone()
    return row["text"] if row else None
