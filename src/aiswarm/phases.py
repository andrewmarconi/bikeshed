"""The phases of a round. A Process is the ordered list of phases each round runs; the Runner does the rest."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiswarm.board import Board
from aiswarm.consensus import call_status, spent_calls
from aiswarm.db import transaction
from aiswarm.events import EventLog
from aiswarm.workspace import Workspace

if TYPE_CHECKING:
    from aiswarm.runner import Runner


@dataclass(frozen=True)
class RoundContext:
    run_id: str
    round: int
    goal: str
    agent_ids: list[str]
    seed: int | None
    process: int
    board: Board
    ws: Workspace
    events: EventLog
    library: list[str]

    def rng(self, runner: "Runner", salt: str = "") -> Any:
        """Seeded per run and round (tests inject one), so a resumed run replays the same order."""
        return runner.rng or random.Random(f"{self.seed}-{self.round}{salt}")


Phase = Callable[["Runner", RoundContext], Awaitable[str | None]]  # returns a pause detail, or None to carry on


def _outage(failed: int, members: int) -> bool:
    """Model errors from two or more members together (or from all) look like a server outage worth pausing for.

    A single dead backend costs only that agent its turns, as in Process 1; it must not stall the run.
    """
    return failed >= min(2, members)


async def simultaneous_act(runner: "Runner", ctx: RoundContext) -> str | None:
    """Every agent acts on the same view; the actions are posted together in a shuffled order."""
    outcomes = await asyncio.gather(*(runner._step(a, ctx) for a in ctx.agent_ids))
    if all(o.failure == "model_error" for o in outcomes):
        return outcomes[0].detail
    order = list(zip(ctx.agent_ids, outcomes))
    ctx.rng(runner).shuffle(order)
    with transaction(runner.conn):
        posted = [runner._post(ctx.board, ctx.ws, ctx.run_id, ctx.round, a, o) for a, o in order]
    for event in posted:
        ctx.events.emit("action_posted", **event)
    return None


async def think(runner: "Runner", ctx: RoundContext) -> str | None:
    """Every agent writes a private note to itself, all at once; the notes are posted after all have finished."""
    outcomes = await asyncio.gather(*(runner._think(a, ctx) for a in ctx.agent_ids))
    if all(o.failure == "model_error" for o in outcomes):
        return outcomes[0].detail
    with transaction(runner.conn):
        posted = [runner._post_note(ctx, a, o) for a, o in zip(ctx.agent_ids, outcomes)]
    for event in posted:
        if event:
            ctx.events.emit("action_posted", **event)
    return None


async def sequential_act(runner: "Runner", ctx: RoundContext) -> str | None:
    """Agents speak one at a time in a seeded order; each sees what was said before its turn."""
    order = list(ctx.agent_ids)
    ctx.rng(runner).shuffle(order)
    streak: list[str] = []  # model errors since the last turn that reached its model
    for agent_id in order:
        outcome = await runner._step(agent_id, ctx)
        streak = [*streak, outcome.detail] if outcome.failure == "model_error" else []
        with transaction(runner.conn):
            event = runner._post(ctx.board, ctx.ws, ctx.run_id, ctx.round, agent_id, outcome)
        ctx.events.emit("action_posted", **event)
    # The turns ended in an outage: pause, and the resumed run discards and replays this round.
    return streak[0] if streak and _outage(len(streak), len(order)) else None


async def poll(runner: "Runner", ctx: RoundContext) -> str | None:
    """Members who have not yet voted on the latest call are asked for their stance, all at once."""
    entries = ctx.board.entries()
    calls = [e for e in entries if e.type == "call_consensus"]
    if not calls:
        return None
    call = calls[-1]
    if call.id in spent_calls(entries):
        return None  # the Scorer rejected it; there is nothing to poll on
    status = call_status(entries, call.id)
    asked = [a for a in ctx.agent_ids if a not in status.supporters | status.objectors]
    if not asked:
        return None
    outcomes = await asyncio.gather(*(runner._poll(a, ctx, call) for a in asked))
    if all(o.failure == "model_error" for o in outcomes) and _outage(len(asked), len(ctx.agent_ids)):
        return outcomes[0].detail
    order = list(zip(asked, outcomes))
    ctx.rng(runner, "-poll").shuffle(order)
    with transaction(runner.conn):
        posted = [runner._post_poll(ctx, a, o, call) for a, o in order]
    for event in posted:
        if event:
            ctx.events.emit("action_posted", **event)
    return None


def phases(process: int, round: int) -> list[Phase]:
    if process == 1:
        return [simultaneous_act]
    if process == 2:
        return [think, simultaneous_act if round == 1 else sequential_act, poll]
    raise ValueError(f"unknown process {process}")
