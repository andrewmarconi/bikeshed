"""Text that reappears: copying between members, credit for copied text, library reuse and norm overlap.
Plain code over the record; no model, no writes."""

import sqlite3
from dataclasses import dataclass

from bikeshed.board import Board, Entry
from bikeshed.observer.metrics import RunInfo
from bikeshed.observer.overlap import match

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


def _texts(entry: Entry) -> list[tuple[str, str]]:
    return [(f, entry.payload[f]) for f in TEXT_FIELDS
            if isinstance(entry.payload.get(f), str) and entry.payload[f].strip()]


def _copier(entry: Entry, members: set[str]) -> bool:
    """Public entries and sent private messages by members; never think notes or failed turns."""
    if entry.author not in members or entry.type in ("think", "pass"):
        return False
    return entry.visible_to is None or entry.type == "private_message"


def copies(conn: sqlite3.Connection, run: RunInfo) -> list[Match]:
    entries = Board(conn, run.id).entries()
    members = set(run.agent_ids)
    texts = {e.id: _texts(e) for e in entries}
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
