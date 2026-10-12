"""The Scorer: an outside check that a passing call for consensus answers the goal. It never judges quality."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError, model_validator

from bikeshed.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from bikeshed.models.base import Message, ModelClient, ModelError
from bikeshed.prompts import render

if TYPE_CHECKING:
    from bikeshed.board import Entry
    from bikeshed.view import Who

FEEDBACK_MAX = 600
FEEDBACK_FILE = "scorer_feedback.md"  # written only by the harness, from the Scorer's rejections


class Ruling(BaseModel):
    accepted: bool
    feedback: str = Field("", max_length=FEEDBACK_MAX)

    @model_validator(mode="after")
    def _feedback_on_rejection(self) -> "Ruling":
        if not self.accepted and not self.feedback.strip():
            raise ValueError("a rejection needs feedback: what is missing for the synthesis to answer the goal")
        return self


@dataclass(frozen=True)
class ScoreResult:
    ruling: Ruling | None  # None: no valid ruling, so the run pauses; nothing is guessed
    detail: str | None = None
    model: str | None = None


def scorer_request(goal: str, synthesis: str) -> str:
    """Everything the Scorer sees: the goal and the synthesis. No names, credits, votes, board or workspace."""
    return (f"## Goal\n\n{goal.strip()}\n\n## Synthesis\n\n<synthesis>\n{synthesis.strip()}\n</synthesis>\n\n"
            "Reply with JSON only.")


async def score(client: ModelClient, goal: str, synthesis: str) -> ScoreResult:
    system = render("scorer.md")
    messages: list[Message] = [{"role": "user", "content": scorer_request(goal, synthesis)}]
    schema = simplify_schema(Ruling.model_json_schema())
    problem, model = "no reply", None
    for _ in range(2):
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return ScoreResult(None, f"model error: {exc}", model)
        model = result.model or model
        try:
            return ScoreResult(Ruling.model_validate(extract_json(result.text)), None, model)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except ActionError as exc:
            problem = str(exc)
        messages += [{"role": "assistant", "content": result.text},
                     {"role": "user", "content": correction_request(problem)}]
    return ScoreResult(None, f"invalid ruling: {problem}", model)


def points(conn: sqlite3.Connection) -> dict[str, int]:
    """Lifetime Scorer points per agent, counted from the rows (never stored as a total)."""
    return {r[0]: r[1] for r in conn.execute("SELECT agent_id, COUNT(*) FROM scorer_points GROUP BY agent_id")}


def run_summary(conn: sqlite3.Connection, run_id: str, entries: Sequence["Entry"], who: "Who") -> str:
    """The Scorer's rulings in one run and who earned points, for the post-run journal. Empty if it never ruled."""
    authors = {e.id: e.author for e in entries}
    rulings = [e for e in entries if e.type in ("scorer_accepted", "scorer_rejected")]
    if not rulings:
        return ""
    lines = ["## The Scorer this run"]
    for e in rulings:
        call = e.payload.get("call_id")
        verdict = "accepted" if e.type == "scorer_accepted" else f"rejected — {e.payload.get('feedback', '')}"
        lines.append(f"- call #{call} by {who(authors.get(call, '?'))}: {verdict}")
    earned = conn.execute("SELECT agent_id, COUNT(*) FROM scorer_points WHERE run_id = ? GROUP BY agent_id "
                          "ORDER BY agent_id", (run_id,)).fetchall()
    if earned:
        lines.append("Scorer points earned: " + ", ".join(f"{who(r[0])} {r[1]}" for r in earned))
    return "\n".join(lines)