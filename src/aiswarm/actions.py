"""Agent actions: the only things an agent can do in a step."""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Text = Annotated[str, Field(min_length=1)]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Propose(_Action):
    type: Literal["propose"]
    answer_summary: Text
    method: Text


class Critique(_Action):
    type: Literal["critique"]
    target: int
    about: Literal["answer", "method"]
    points: Text


class Question(_Action):
    type: Literal["question"]
    target: int
    text: Text


class Adopt(_Action):
    type: Literal["adopt"]
    target: int
    reason: Text


class Refine(_Action):
    type: Literal["refine"]
    target: int
    change: Text
    reason: Text


class WriteFile(_Action):
    type: Literal["write_file"]
    path: Text
    base_version: int = Field(ge=0)
    content: str
    note: Text


class Credit(_Action):
    agent_id: Text
    contribution: Text


class CallConsensus(_Action):
    type: Literal["call_consensus"]
    synthesis: Text
    refs: list[int]
    credits: list[Credit]


class Vote(_Action):
    type: Literal["vote"]
    target: int
    stance: Literal["support", "object"]
    reason: Text


class Pass(_Action):
    type: Literal["pass"]


class ReadFile(_Action):
    type: Literal["read_file"]
    path: Text


class ListFiles(_Action):
    type: Literal["list_files"]


class ReadEntry(_Action):
    type: Literal["read_entry"]
    id: int


PostingAction = Propose | Critique | Question | Adopt | Refine | WriteFile | CallConsensus | Vote | Pass
READ_ACTIONS = (ReadFile, ListFiles, ReadEntry)

StepAction = Annotated[
    Propose | Critique | Question | Adopt | Refine | WriteFile | CallConsensus | Vote | Pass
    | ReadFile | ListFiles | ReadEntry,
    Field(discriminator="type"),
]


class _Envelope(_Action):
    action: StepAction


class ActionError(ValueError):
    """The model's reply was not a valid action."""


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from a reply, tolerating a code fence or surrounding prose."""
    raw = text.strip()
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start == -1:
        raise ActionError("No JSON object found in the reply.")
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise ActionError(f"The reply is not valid JSON: {exc.msg}.") from exc
    return value


def parse_action(text: str) -> Any:
    data = extract_json(text)
    if isinstance(data, dict) and "action" not in data and "type" in data:
        data = {"action": data}
    try:
        return _Envelope.model_validate(data).action
    except ValidationError as exc:
        raise ActionError(summarise_validation(exc)) from exc


def summarise_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()[:5]
    )


def target_refs(action: Any) -> list[int]:
    if isinstance(action, CallConsensus):
        return list(action.refs)
    target = getattr(action, "target", None)
    return [target] if target is not None else []


_DROP = {
    "discriminator", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minItems", "maxItems", "title", "default",
}


def simplify_schema(node: Any) -> Any:
    """Reduce a pydantic JSON schema to the subset strict structured-output backends accept.

    Pydantic still validates the full constraints after parsing.
    """
    if isinstance(node, list):
        return [simplify_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "$defs"):
            out[key] = {name: simplify_schema(sub) for name, sub in value.items()}
        elif key in _DROP:
            continue
        elif key == "oneOf":
            out["anyOf"] = simplify_schema(value)
        elif key == "const":
            out["enum"] = [value]
        else:
            out[key] = simplify_schema(value)
    return out


def step_schema() -> dict[str, Any]:
    return simplify_schema(_Envelope.model_json_schema())
