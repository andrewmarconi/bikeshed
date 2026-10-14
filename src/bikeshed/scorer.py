"""The Scorer: an outside check that a passing call for consensus answers the goal. It never judges quality."""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError, model_validator

from bikeshed.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from bikeshed.briefs import RunBriefs, quotes_constraint
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


class BriefedRuling(BaseModel):
    answers_goal: bool
    feedback: str = Field("", max_length=FEEDBACK_MAX)
    conflicts: list[str] = Field(default_factory=list)  # constraint ids; the harness names their holders

    @property
    def accepted(self) -> bool:
        return self.answers_goal and not self.conflicts

    @model_validator(mode="after")
    def _feedback_off_goal(self) -> "BriefedRuling":
        if not self.answers_goal and not self.feedback.strip():
            raise ValueError("when the synthesis does not answer the goal, say what is missing in feedback")
        return self


@dataclass(frozen=True)
class ScoreResult:
    ruling: Ruling | BriefedRuling | None  # None: no valid ruling, so the run pauses; nothing is guessed
    detail: str | None = None
    model: str | None = None


def scorer_request(goal: str, synthesis: str) -> str:
    """Everything the Scorer sees: the goal and the synthesis. No names, credits, votes, board or workspace."""
    return (f"## Goal\n\n{goal.strip()}\n\n## Synthesis\n\n<synthesis>\n{synthesis.strip()}\n</synthesis>\n\n"
            "Reply with JSON only.")


async def _rule(client: ModelClient, system: str, request: str, shape: type[BaseModel],
                check: Callable[[BaseModel], str | None] | None = None) -> ScoreResult:
    messages: list[Message] = [{"role": "user", "content": request}]
    schema = simplify_schema(shape.model_json_schema())
    problem, model = "no reply", None
    for _ in range(2):
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return ScoreResult(None, f"model error: {exc}", model)
        model = result.model or model
        try:
            ruling = shape.model_validate(extract_json(result.text))
            problem = check(ruling) if check else None
            if problem is None:
                return ScoreResult(ruling, None, model)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except ActionError as exc:
            problem = str(exc)
        messages += [{"role": "assistant", "content": result.text},
                     {"role": "user", "content": correction_request(problem)}]
    return ScoreResult(None, f"invalid ruling: {problem}", model)


async def score(client: ModelClient, goal: str, synthesis: str) -> ScoreResult:
    return await _rule(client, render("scorer.md"), scorer_request(goal, synthesis), Ruling)


def briefed_request(goal: str, synthesis: str, briefs: RunBriefs) -> str:
    """The goal, the shared text, every constraint by id, and the synthesis. No names, no private text."""
    parts = [f"## Goal\n\n{goal.strip()}"]
    if briefs.shared is not None and briefs.shared.text.strip():
        parts.append(f"## Shared brief\n\n{briefs.shared.text.strip()}")
    parts.append("## Constraints\n\n" + ("\n".join(f"{c.id}: {c.text}" for c in briefs.constraints) or "(none)"))
    parts.append(f"## Synthesis\n\n<synthesis>\n{synthesis.strip()}\n</synthesis>")
    return "\n\n".join(parts) + "\n\nReply with JSON only."


def _briefed_problem(ruling: BriefedRuling, briefs: RunBriefs) -> str | None:
    known = {c.id for c in briefs.constraints}
    if unknown := [c for c in ruling.conflicts if c not in known]:
        return f"these are not constraint ids: {', '.join(unknown)}; use only ids from the Constraints section"
    if quotes_constraint(ruling.feedback, briefs):
        return "feedback must not quote or restate a constraint; say only what is missing for it to answer the goal"
    return None


async def score_briefed(client: ModelClient, goal: str, synthesis: str, briefs: RunBriefs) -> ScoreResult:
    return await _rule(client, render("scorer_briefed.md"), briefed_request(goal, synthesis, briefs), BriefedRuling,
                       lambda ruling: _briefed_problem(ruling, briefs))


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