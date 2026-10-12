"""The phases of a round. A Process is the ordered list of phases each round runs; the Runner does the rest."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiswarm.board import Board
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


def phases(process: int, round: int) -> list[Phase]:
    if process == 1:
        return [simultaneous_act]
    raise ValueError(f"unknown process {process}")
