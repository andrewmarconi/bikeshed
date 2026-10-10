"""Named peer reviews, the reviewee's responses, and reputation computed from them."""

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from aiswarm.db import now_iso

Who = Callable[[str], str]


@dataclass(frozen=True)
class Review:
    id: int
    run_id: str
    reviewer: str
    reviewee: str
    is_self: bool
    more_of: str
    less_of: str
    private_note: str
    evidence: list[int]
    overall: int
    overall_reason: str


@dataclass(frozen=True)
class Response:
    review_id: int
    stance: str
    reason: str


@dataclass(frozen=True)
class Reputation:
    score: float | None
    count: int

    def label(self) -> str:
        if not self.count or self.score is None:
            return "no reviews yet"
        return f"★{self.score:.1f} ({self.count} review{'' if self.count == 1 else 's'})"


def _review(row: sqlite3.Row) -> Review:
    return Review(
        id=row["id"], run_id=row["run_id"], reviewer=row["reviewer"], reviewee=row["reviewee"],
        is_self=bool(row["is_self"]), more_of=row["more_of"], less_of=row["less_of"],
        private_note=row["private_note"], evidence=json.loads(row["evidence"]), overall=row["overall"],
        overall_reason=row["overall_reason"],
    )


def add_review(
    conn: sqlite3.Connection, *, run_id: str, reviewer: str, reviewee: str, more_of: str, less_of: str,
    private_note: str, evidence: Sequence[int], overall: int, overall_reason: str,
) -> Review:
    row = conn.execute(
        "INSERT INTO reviews (run_id, reviewer, reviewee, is_self, more_of, less_of, private_note, evidence, "
        "overall, overall_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id, reviewer, reviewee) DO UPDATE SET more_of = excluded.more_of, "
        "less_of = excluded.less_of, private_note = excluded.private_note, evidence = excluded.evidence, "
        "overall = excluded.overall, overall_reason = excluded.overall_reason, created_at = excluded.created_at "
        "RETURNING *",
        (run_id, reviewer, reviewee, int(reviewer == reviewee), more_of, less_of, private_note,
         json.dumps(list(evidence)), overall, overall_reason, now_iso()),
    ).fetchone()
    return _review(row)


def add_response(conn: sqlite3.Connection, review_id: int, stance: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO review_responses (review_id, stance, reason, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (review_id) DO UPDATE SET stance = excluded.stance, reason = excluded.reason, "
        "created_at = excluded.created_at",
        (review_id, stance, reason, now_iso()),
    )


def reviews_of(
    conn: sqlite3.Connection, agent_id: str, run_id: str | None = None, include_self: bool = False
) -> list[Review]:
    sql = "SELECT * FROM reviews WHERE reviewee = ?"
    params: list[object] = [agent_id]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    if not include_self:
        sql += " AND is_self = 0"
    return [_review(row) for row in conn.execute(sql + " ORDER BY id DESC", params)]


def responses_for(conn: sqlite3.Connection, review_ids: Sequence[int]) -> dict[int, Response]:
    if not review_ids:
        return {}
    marks = ", ".join("?" for _ in review_ids)
    rows = conn.execute(f"SELECT * FROM review_responses WHERE review_id IN ({marks})", list(review_ids))
    return {row["review_id"]: Response(row["review_id"], row["stance"], row["reason"]) for row in rows}


def reputation(conn: sqlite3.Connection, agent_id: str, half_life: float) -> Reputation:
    """Recency-weighted mean of peer ratings; age is the number of closed runs after the reviewed one."""
    rows = conn.execute(
        "SELECT r.overall, (SELECT COUNT(*) FROM runs later WHERE later.status = 'closed' "
        "AND (later.created_at, later.id) > (u.created_at, u.id)) AS age "
        "FROM reviews r JOIN runs u ON u.id = r.run_id WHERE r.reviewee = ? AND r.is_self = 0",
        (agent_id,),
    ).fetchall()
    if not rows:
        return Reputation(None, 0)
    weights = [0.5 ** (row["age"] / half_life) for row in rows]
    score = sum(w * row["overall"] for w, row in zip(weights, rows)) / sum(weights)
    return Reputation(score, len(rows))


def render_review(review: Review, who: Who, *, private: bool, response: Response | None = None) -> str:
    kind = "Self-review" if review.is_self else "Review"
    lines = [
        f"{kind} {review.id} of {who(review.reviewee)} by {who(review.reviewer)} after run {review.run_id}",
        f"  more of: {review.more_of}",
        f"  less of: {review.less_of}",
    ]
    if private:
        lines.append(f"  private note: {review.private_note}")
    lines += [
        f"  evidence: {', '.join(f'#{i}' for i in review.evidence)}",
        f"  overall: {review.overall}/5 — {review.overall_reason}",
    ]
    if response:
        lines.append(f"  response: {response.stance} — {response.reason}")
    return "\n".join(lines)


def render_public_reviews(conn: sqlite3.Connection, agent_id: str, who: Who, max_chars: int = 8_000) -> str:
    """What any member may read about another: peer reviews and responses, never private notes."""
    reviews = reviews_of(conn, agent_id)
    if not reviews:
        return f"No reviews of {who(agent_id)} yet."
    responses = responses_for(conn, [r.id for r in reviews])
    text = "\n\n".join(render_review(r, who, private=False, response=responses.get(r.id)) for r in reviews)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[truncated: {len(text)} characters in total]"
    return text
