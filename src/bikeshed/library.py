"""The Method Library: every proposed or refined method, in its author's words, with how it was received."""

import json
import math
import sqlite3
from array import array
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from bikeshed.board import SYSTEM, Entry
from bikeshed.db import now_iso
from bikeshed.models.base import Embedder, ModelError

Who = Callable[[str], str]


@dataclass(frozen=True)
class Reception:
    adopted_by: list[str]
    critiques: int
    refines: int
    questions: int
    in_winning: bool

    def label(self, who: Who) -> str:
        adopted = ", ".join(who(a) for a in self.adopted_by) or "no one"
        parts = [f"adopted by {adopted}", f"critiqued {self.critiques}×", f"refined {self.refines}×",
                 f"questioned {self.questions}×"]
        if self.in_winning:
            parts.append("cited by the winning call")
        return " · ".join(parts)


@dataclass(frozen=True)
class Method:
    id: int
    run_id: str
    entry_id: int
    author: str
    kind: str
    goal: str
    text: str
    reception: Reception


def reception_of(entry: Entry, entries: Sequence[Entry]) -> Reception:
    closed = next((e for e in reversed(entries) if e.type == "run_closed"), None)
    call_id = closed.payload.get("call_id") if closed else None
    winning = next((e for e in entries if e.id == call_id), None) if call_id else None
    others = [e for e in entries if e.payload.get("target") == entry.id and e.author not in (entry.author, SYSTEM)]
    return Reception(
        adopted_by=sorted({e.author for e in others if e.type == "adopt"}),
        critiques=sum(e.type == "critique" for e in others),
        refines=sum(e.type == "refine" for e in others),
        questions=sum(e.type == "question" for e in others),
        in_winning=winning is not None and entry.id in winning.refs,
    )


def record_methods(conn: sqlite3.Connection, run_id: str, goal: str, entries: Sequence[Entry]) -> int:
    count = 0
    for entry in entries:
        if entry.type not in ("propose", "refine") or entry.author == SYSTEM:
            continue
        text = entry.payload.get("method" if entry.type == "propose" else "change", "")
        conn.execute(
            "INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, entry_id) DO UPDATE SET "
            "text = excluded.text, reception = excluded.reception",
            (run_id, entry.id, entry.author, entry.type, goal, text,
             json.dumps(asdict(reception_of(entry, entries))), now_iso()),
        )
        count += 1
    return count


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


def _method(row: sqlite3.Row) -> Method:
    return Method(id=row["id"], run_id=row["run_id"], entry_id=row["entry_id"], author=row["author"],
                  kind=row["kind"], goal=row["goal"], text=row["text"], reception=Reception(**json.loads(row["reception"])))


async def _embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors = await embedder.embed(texts)
    if len(vectors) != len(texts):
        raise ModelError(f"embeddings server returned {len(vectors)} vectors for {len(texts)} texts")
    return vectors


async def embed_missing(conn: sqlite3.Connection, embedder: Embedder, batch: int = 16) -> int:
    """Embed methods that have no vector yet. A rejected batch is retried row by row so one bad row
    cannot block the rest; it stays unembedded. Raises only if nothing could be embedded at all."""
    rows = conn.execute("SELECT id, text FROM methods WHERE embedding IS NULL ORDER BY id").fetchall()
    embedded = 0
    last_error: ModelError | None = None
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        try:
            pairs = list(zip(chunk, await _embed(embedder, [row["text"] for row in chunk])))
        except ModelError as exc:
            last_error, pairs = exc, []
            for row in chunk if len(chunk) > 1 else []:
                try:
                    pairs.append((row, (await _embed(embedder, [row["text"]]))[0]))
                except ModelError as single:
                    last_error = single
        for row, vector in pairs:
            conn.execute("UPDATE methods SET embedding = ? WHERE id = ?", (_pack(vector), row["id"]))
        embedded += len(pairs)
    if rows and not embedded and last_error is not None:
        raise last_error
    return embedded


async def retrieve(
    conn: sqlite3.Connection, embedder: Embedder, goal: str, top_k: int | None, exclude_run: str | None = None,
) -> list[tuple[Method, float]]:
    if top_k is not None and top_k <= 0:
        return []
    rows = conn.execute(
        "SELECT * FROM methods WHERE embedding IS NOT NULL AND run_id IS NOT ?", (exclude_run,)
    ).fetchall()
    if not rows:
        return []
    [query] = await _embed(embedder, [goal])
    scored = [(_method(row), _cosine(query, _unpack(row["embedding"]))) for row in rows]
    ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
    return ranked if top_k is None else ranked[:top_k]


def render_methods(methods: Sequence[Method], who: Who, max_chars: int = 600) -> list[str]:
    def clip(text: str) -> str:
        return text if len(text) <= max_chars else text[:max_chars] + "…"

    return [
        f"- #{m.entry_id} in run {m.run_id} by {who(m.author)} ({m.kind}); goal: {clip(m.goal)}\n"
        f"  method: {clip(m.text)}\n  reception: {m.reception.label(who)}"
        for m in methods
    ]
