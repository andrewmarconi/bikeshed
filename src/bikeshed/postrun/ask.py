"""One structured post-run request: a JSON reply validated by pydantic, with one correction."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from bikeshed.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from bikeshed.models.base import Message, ModelClient


class ReplyError(ValueError):
    """The reply could not be accepted, even after one correction."""


@dataclass(frozen=True)
class Answer[T]:
    reply: T
    reasoning: str | None


async def ask[T: BaseModel](
    client: ModelClient, system: str, request: str, model: type[T], check: Callable[[T], None] | None = None,
) -> Answer[T]:
    messages: list[Message] = [{"role": "user", "content": request}]
    schema = simplify_schema(model.model_json_schema())
    reasoning: list[str] = []
    problem = ""
    for _ in range(2):
        result = await client.chat(system, messages, schema)
        if result.reasoning:
            reasoning.append(result.reasoning)
        try:
            reply = model.model_validate(extract_json(result.text))
            if check:
                check(reply)
            return Answer(reply, "\n\n".join(reasoning) or None)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except (ActionError, ReplyError) as exc:
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": correction_request(problem)},
        ]
    raise ReplyError(problem)
