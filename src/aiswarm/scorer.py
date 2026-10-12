"""The Scorer: an outside check that a passing call for consensus answers the goal. It never judges quality."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, model_validator

from aiswarm.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.prompts import render

FEEDBACK_MAX = 600


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
    return f"## Goal\n\n{goal.strip()}\n\n## Synthesis\n\n{synthesis.strip()}\n\nReply with JSON only."


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
