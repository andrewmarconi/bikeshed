"""One agent step: view in, one validated posting action out."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from aiswarm.actions import (
    READ_ACTIONS, ActionError, CallConsensus, ListFiles, Pass, Propose, ReadFile, Vote, WriteFile,
    parse_action, step_schema,
)
from aiswarm.board import Entry
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.prompts import render
from aiswarm.view import Who, render_entry
from aiswarm.workspace import InvalidPath, Workspace, normalise_path

Reader = Callable[[Any], str]
Validator = Callable[[Any], None]


@dataclass(frozen=True)
class StepOutcome:
    action: Any
    failure: str | None = None
    detail: str = ""
    reasoning: str | None = None
    model: str | None = None


def system_prompt(display: str, temperament: str, max_reads: int) -> str:
    return render("agent_system.md", display=display, temperament=temperament.strip(), max_reads=max_reads)


async def take_step(
    *, client: ModelClient, system: str, view: str, round: int, max_reads: int,
    reader: Reader, validate: Validator,
) -> StepOutcome:
    messages: list[Message] = [{"role": "user", "content": view}]
    schema = step_schema()
    reads = 0
    corrected = False
    reasoning: list[str] = []
    model: str | None = None

    def outcome(action: Any, failure: str | None = None, detail: str = "") -> StepOutcome:
        return StepOutcome(action, failure, detail, "\n\n".join(reasoning) or None, model)

    while True:
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return outcome(Pass(type="pass"), "model_error", str(exc))
        model = result.model or model
        if result.reasoning:
            reasoning.append(result.reasoning)
        try:
            action = parse_action(result.text)
            if isinstance(action, READ_ACTIONS):
                if reads >= max_reads:
                    raise ActionError(f"You have used all {max_reads} reads; choose a posting action.")
                reads += 1
                messages += [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": "Read result:\n" + reader(action)},
                ]
                continue
            if round == 1 and not isinstance(action, Propose):
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


def make_validator(entries: Sequence[Entry], agent_ids: Sequence[str]) -> Validator:
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
                normalise_path(action.path)
            except InvalidPath as exc:
                raise ActionError(str(exc)) from exc

    return validate


def make_reader(workspace: Workspace, entries: Sequence[Entry], who: Who) -> Reader:
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
            return f"{normalise_path(action.path)} (v{version}):\n{content}"
        entry = by_id.get(action.id)
        return render_entry(entry, who) if entry else f"No entry #{action.id} is visible to you."

    return read
