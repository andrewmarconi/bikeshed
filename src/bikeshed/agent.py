"""One agent step: view in, one validated posting action out."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from bikeshed.actions import (
    READ_ACTIONS, STEP, WRITE_ACTIONS, ActionError, CallConsensus, ListFiles, Pass, PrivateMessage, Propose, ReadFile, ReadReviews,
    Vote, WriteFile, parse_action, step_schema,
)
from bikeshed.board import Entry
from bikeshed.models.base import Message, ModelClient, ModelError
from bikeshed.prompts import render
from bikeshed.scorer import FEEDBACK_FILE
from bikeshed.view import Who, render_entry
from bikeshed.workspace import InvalidPath, Workspace, normalise_path

Reader = Callable[[Any], str]
Validator = Callable[[Any], None]
Writer = Callable[[Any], str]


@dataclass(frozen=True)
class StepOutcome:
    action: Any
    failure: str | None = None
    detail: str = ""
    reasoning: str | None = None
    model: str | None = None
    pre_actions: tuple[dict[str, Any], ...] = ()  # reads and writes before the action; ok is False if refused
    calls: int = 0  # model calls the step took, corrections included


def system_prompt(
    display: str, temperament: str, max_reads: int, template: str = "agent_system.md", max_writes: int = 0
) -> str:
    return render(template, display=display, temperament=temperament.strip(), max_reads=max_reads,
                  max_writes=max_writes)


async def take_step(
    *, client: ModelClient, system: str, view: str, round: int, max_reads: int,
    reader: Reader, validate: Validator, shape: type[BaseModel] = STEP, opening_rule: bool = True,
    writer: Writer | None = None, max_writes: int = 0,
) -> StepOutcome:
    messages: list[Message] = [{"role": "user", "content": view}]
    schema = step_schema(shape)
    reads = 0
    writes = 0
    corrected = False
    reasoning: list[str] = []
    model: str | None = None
    pre_actions: list[dict[str, Any]] = []
    calls = 0

    def outcome(action: Any, failure: str | None = None, detail: str = "") -> StepOutcome:
        return StepOutcome(action, failure, detail, "\n\n".join(reasoning) or None, model, tuple(pre_actions), calls)

    while True:
        calls += 1
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return outcome(Pass(type="pass"), "model_error", str(exc))
        model = result.model or model
        if result.reasoning:
            reasoning.append(result.reasoning)
        try:
            action = parse_action(result.text, shape)
            if isinstance(action, READ_ACTIONS) or (writer is not None and isinstance(action, WRITE_ACTIONS)):
                pre_actions.append({"type": action.type, "ok": False})  # set to True once it is carried out
            if isinstance(action, READ_ACTIONS):
                if reads >= max_reads:
                    raise ActionError(f"You have used all {max_reads} reads; choose a posting action.")
                reads += 1
                pre_actions[-1]["ok"] = True
                messages += [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": "Read result:\n" + reader(action)},
                ]
                continue
            if writer is not None and isinstance(action, WRITE_ACTIONS):
                if opening_rule and round == 1:
                    raise ActionError("In the opening round your action must be `propose`.")
                if writes >= max_writes:
                    raise ActionError(f"You have used all {max_writes} writes; choose a posting action.")
                validate(action)
                writes += 1
                pre_actions[-1]["ok"] = True
                messages += [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": "Write result:\n" + writer(action)},
                ]
                continue
            if opening_rule and round == 1 and not isinstance(action, Propose):
                raise ActionError("In the opening round your action must be `propose`.")
            validate(action)
            return outcome(action)
        except ActionError as exc:
            if corrected:
                return outcome(Pass(type="pass"), "invalid_action", str(exc))
            corrected = True
            messages += [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": f"That was not a valid action: {exc}\nReply again with exactly one valid action."},
            ]


def make_validator(entries: Sequence[Entry], agent_ids: Sequence[str], agent_id: str | None = None) -> Validator:
    by_id = {e.id: e for e in entries}

    def validate(action: Any) -> None:
        target = getattr(action, "target", None)
        if target is not None and target not in by_id:
            raise ActionError(f"Entry #{target} does not exist.")
        if isinstance(action, Vote) and by_id[action.target].type != "call_consensus":
            raise ActionError(f"Entry #{action.target} is not a call_consensus; you can only vote on those.")
        if isinstance(action, CallConsensus):
            missing = [ref for ref in action.refs if ref not in by_id]
            if missing:
                raise ActionError(f"Entry #{missing[0]} does not exist.")
            unknown = [c.agent_id for c in action.credits if c.agent_id not in agent_ids]
            if unknown:
                raise ActionError(f"Unknown agent ids in credits: {unknown}. Members are {list(agent_ids)}.")
        if isinstance(action, WriteFile):
            try:
                path = normalise_path(action.path)
            except InvalidPath as exc:
                raise ActionError(str(exc)) from exc
            if path.casefold() == FEEDBACK_FILE:
                raise ActionError(f"{FEEDBACK_FILE} is written only by the Scorer; choose another path.")
        if isinstance(action, PrivateMessage):
            others = [a for a in agent_ids if a != agent_id]
            if action.to not in others:
                raise ActionError(f"private_message.to must be another member: {others}")

    return validate


def make_reader(
    workspace: Workspace, entries: Sequence[Entry], who: Who, max_chars: int = 8_000,
    reviews: Callable[[str], str] | None = None,
) -> Reader:
    by_id = {e.id: e for e in entries}

    def read(action: Any) -> str:
        if isinstance(action, ListFiles):
            return "\n".join(f"{path} (v{version})" for path, version in workspace.list_files()) or "(no files yet)"
        if isinstance(action, ReadFile):
            try:
                content, version = workspace.read(action.path)
            except FileNotFoundError:
                return f"No such file: {action.path}"
            except InvalidPath as exc:
                return str(exc)
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n[truncated: {len(content)} characters in total]"
            return f"{normalise_path(action.path)} (v{version}):\n{content}"
        if isinstance(action, ReadReviews):
            return reviews(action.agent_id) if reviews else "Reviews are not available."
        entry = by_id.get(action.id)
        return render_entry(entry, who) if entry else f"No entry #{action.id} is visible to you."

    return read
