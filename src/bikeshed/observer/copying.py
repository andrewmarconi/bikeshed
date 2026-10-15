"""Text that reappears: copying between members, credit for copied text, library reuse and norm overlap.
Plain code over the record; no model, no writes."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bikeshed.board import Board, Entry
from bikeshed.db import has_table
from bikeshed.events import read_events
from bikeshed.gitrepo import GitRepo
from bikeshed.observer.metrics import RunInfo
from bikeshed.observer.overlap import match, share

NORM_FLOOR = 0.25  # norms are short and reworded: list any proposal sharing this much with an earlier adopted norm
TEXT_FIELDS = ("answer_summary", "method", "points", "text", "reason", "change", "synthesis", "content")


@dataclass(frozen=True)
class Match:
    copier: str
    entry_id: int
    entry_type: str
    field: str
    source_author: str
    source_id: int
    kind: str          # exact | partial
    same_round: bool   # in a blind phase a same-round match is convergence, not copying


def file_texts(conn: sqlite3.Connection, run_id: str, runs_dir: Path | None) -> dict[tuple[str, int], str]:
    """Each workspace file version's text, read from the run's workspace repo at the commit `files` records.
    A write_file entry holds only the path and version; its text lives in the workspace. Reads only."""
    root = runs_dir / run_id / "workspace" if runs_dir is not None else None
    if root is None or not (root / ".git").exists():
        return {}
    repo = GitRepo(root)  # not GitRepo.init: never create anything
    out = {}
    for row in conn.execute("SELECT path, version, git_commit FROM files WHERE run_id = ?", (run_id,)):
        if (text := repo.show_commit(row["git_commit"], row["path"])) is not None:
            out[(row["path"], row["version"])] = text
    return out


def _texts(entry: Entry, files: dict[tuple[str, int], str] | None = None) -> list[tuple[str, str]]:
    found = [(f, entry.payload[f]) for f in TEXT_FIELDS
             if isinstance(entry.payload.get(f), str) and entry.payload[f].strip()]
    if entry.type == "write_file" and files and (text := files.get((entry.payload.get("path"),
                                                                     entry.payload.get("version")))):
        found.append(("content", text))
    return found


def _copier(entry: Entry, members: set[str]) -> bool:
    """Public entries and sent private messages by members; never think notes or failed turns."""
    if entry.author not in members or entry.type in ("think", "pass"):
        return False
    return entry.visible_to is None or entry.type == "private_message"


def copies(conn: sqlite3.Connection, run: RunInfo, runs_dir: Path | None = None) -> list[Match]:
    """Without `runs_dir`, file writes have no text to compare."""
    entries = Board(conn, run.id).entries()
    members = set(run.agent_ids)
    files = file_texts(conn, run.id, runs_dir)
    texts = {e.id: _texts(e, files) for e in entries}
    out = []
    for i, entry in enumerate(entries):
        if not _copier(entry, members):
            continue
        sources = [s for s in entries[:i] if s.author in members and s.author != entry.author and s.type != "think"
                   and (s.visible_to is None or entry.author in s.visible_to) and texts[s.id]]
        for field, text in texts[entry.id]:
            for source in sources:
                kinds = {match(text, src) for _, src in texts[source.id]} - {None}
                if kinds:
                    out.append(Match(entry.author, entry.id, entry.type, field, source.author, source.id,
                                     "exact" if "exact" in kinds else "partial", source.round == entry.round))
    return out


@dataclass(frozen=True)
class CallCredit:
    call_id: int
    caller: str
    copied: tuple[str, ...]      # authors whose earlier text the synthesis matches
    credited: tuple[str, ...]
    uncredited: tuple[str, ...]  # copied, not credited
    unmatched: tuple[str, ...]   # credited, but none of their text is in the synthesis (not an error: ideas move)


def credit_accuracy(conn: sqlite3.Connection, run: RunInfo, found: list[Match]) -> list[CallCredit]:
    out = []
    for call in Board(conn, run.id).entries():
        if call.type != "call_consensus" or call.author not in run.agent_ids:
            continue
        copied = {m.source_author for m in found if m.entry_id == call.id and m.field == "synthesis"}
        credited = {c["agent_id"] for c in call.payload.get("credits", []) if c.get("agent_id")}
        out.append(CallCredit(call.id, call.author, tuple(sorted(copied)), tuple(sorted(credited)),
                              tuple(sorted(copied - credited)), tuple(sorted(credited - copied - {call.author}))))
    return out


@dataclass(frozen=True)
class Reuse:
    method_id: int
    method_run: str
    method_author: str
    shown_to: tuple[str, ...]
    reused_by: tuple[tuple[str, int, str], ...]  # (agent, entry id, kind)


def library_reuse(conn: sqlite3.Connection, run: RunInfo, runs_dir: Path) -> list[Reuse] | None:
    """Which library methods each member was shown, and whether its proposals or refines reuse them.
    None when the run's events don't record a retrieval."""
    path = runs_dir / run.id / "events.jsonl"
    events = read_events(path) if path.exists() else []
    retrieved = [e for e in events if e.get("event") == "library_retrieved"]
    if not retrieved:
        return [] if any(e.get("event") == "library_unavailable" for e in events) else None
    shown: dict[str, set[int]] = {agent: set() for agent in run.agent_ids}
    for event in retrieved:  # a resumed run retrieves again; a member was shown everything it was ever given
        own = event.get("control_methods", {})
        for agent in run.agent_ids:
            shown[agent] |= set(own.get(agent, event.get("methods", [])))
    ids = sorted(set().union(*shown.values()))
    if not ids or not has_table(conn, "methods"):
        return []
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT id, run_id, author, text FROM methods WHERE id IN ({', '.join('?' * len(ids))})", ids)}
    entries = [e for e in Board(conn, run.id).entries() if e.visible_to is None and e.type in ("propose", "refine")]
    out = []
    for method_id in ids:
        row = rows.get(method_id)
        if row is None:  # gone from this store
            continue
        to = tuple(a for a in run.agent_ids if method_id in shown[a])
        reused = []
        for e in entries:
            text = e.payload.get("method" if e.type == "propose" else "change", "")
            if e.author in to and isinstance(text, str) and (kind := match(text, row["text"])):
                reused.append((e.author, e.id, kind))
        out.append(Reuse(method_id, row["run_id"], row["author"], to, tuple(reused)))
    return out


@dataclass(frozen=True)
class NormOverlap:
    proposal_id: int
    run_id: str
    author: str
    norm_id: int
    norm_text: str
    share: float  # of the proposal's word sequences found in the norm; 1.0 when it is all there
    status: str


def norm_overlaps(conn: sqlite3.Connection, runs: list[RunInfo]) -> list[NormOverlap]:
    """Norm and amendment proposals sharing at least NORM_FLOOR of their text with a norm adopted before they were
    made, with the share shown rather than a yes or no. A norm is adopted in its run's post-run, so "before" means its
    run ended (post-run included) before the proposal was created."""
    if not has_table(conn, "norm_proposals"):
        return []
    ends = {r.id: datetime.fromisoformat(r.end) for r in runs}
    rows = conn.execute("SELECT * FROM norm_proposals WHERE kind IN ('norm', 'amendment') ORDER BY id").fetchall()
    adopted = [r for r in rows if r["status"] == "adopted"]
    out = []
    for p in rows:
        made = datetime.fromisoformat(p["created_at"])
        best = None
        for n in adopted:
            if n["id"] == p["id"] or n["run_id"] not in ends or not ends[n["run_id"]] < made:
                continue
            got = share(p["text"], n["text"])
            if got >= NORM_FLOOR and (best is None or got > best[1]):
                best = (n, got)
        if best:
            out.append(NormOverlap(p["id"], p["run_id"], p["author"], best[0]["id"], best[0]["text"], best[1],
                                   p["status"]))
    return out
