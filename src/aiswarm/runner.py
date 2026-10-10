"""The Runner: plain code that ticks rounds, stores posts, and applies stop rules. It never steers."""

import asyncio
import json
import random
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiswarm.actions import Pass, WriteFile, target_refs
from aiswarm.agent import StepOutcome, make_reader, make_validator, system_prompt, take_step
from aiswarm.board import SYSTEM, Board
from aiswarm.config import RunSettings
from aiswarm.consensus import StopDecision, stop_decision
from aiswarm.db import now_iso, transaction
from aiswarm.events import EventLog
from aiswarm.library import render_methods, retrieve
from aiswarm.models.base import Embedder, ModelClient, ModelError
from aiswarm.profiles import ProfileStore
from aiswarm.report import write_report
from aiswarm.reviews import render_public_reviews
from aiswarm.view import build_view, collect_inputs, make_who
from aiswarm.gitrepo import GitError
from aiswarm.workspace import InvalidPath, Workspace, WriteConflict


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    reason: str | None = None
    call_id: int | None = None


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Runner:
    def __init__(
        self, *, settings: RunSettings, conn: sqlite3.Connection, store: ProfileStore, runs_dir: Path,
        clients: Mapping[str, ModelClient], sink: Callable[[dict[str, Any]], None] | None = None,
        rng: Any = None, embedder: Embedder | None = None, library_top_k: int = 3,
    ):
        self.settings = settings
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink
        self.rng = rng or random.Random()
        self.embedder = embedder
        self.library_top_k = library_top_k

    def _events(self, run_id: str) -> EventLog:
        return EventLog(self.runs_dir / run_id / "events.jsonl", sink=self.sink)

    def _workspace(self, run_id: str) -> Workspace:
        return Workspace(self.conn, run_id, self.runs_dir / run_id / "workspace")

    def start(self, goal: str, agent_ids: Sequence[str]) -> str:
        if not agent_ids:
            raise ValueError("a run needs at least one agent")
        unknown = [a for a in agent_ids if a not in self.store.ids()]
        if unknown:
            raise ValueError(f"unknown agents: {unknown}")
        run_id = new_run_id()
        (self.runs_dir / run_id).mkdir(parents=True)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'running', ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso()),
            )
            Board(self.conn, run_id).append(0, SYSTEM, "goal", {"goal": goal})
        self._workspace(run_id)
        self._events(run_id).emit("run_started", run_id=run_id, goal=goal, agents=list(agent_ids))
        return run_id

    async def run(self, run_id: str) -> RunResult:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] == "closed":
            return RunResult(run_id, "closed", row["ended_reason"])
        goal, agent_ids = row["goal"], json.loads(row["agent_ids"])
        board, ws, events = Board(self.conn, run_id), self._workspace(run_id), self._events(run_id)

        last = self.conn.execute(
            "SELECT round, workspace_commit FROM rounds WHERE run_id = ? ORDER BY round DESC LIMIT 1", (run_id,)
        ).fetchone()
        ws.reset_to(last["workspace_commit"] if last else ws.initial_commit())
        completed = last["round"] if last else 0
        self.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
        library = await self._library(run_id, goal, events)

        for rnd in range(completed + 1, self.settings.max_rounds + 1):
            events.emit("round_started", round=rnd)
            outcomes = await asyncio.gather(
                *(self._step(a, rnd, goal, agent_ids, board, ws, events, library) for a in agent_ids)
            )
            if all(o.failure == "model_error" for o in outcomes):
                self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                events.emit("run_paused", round=rnd, detail=outcomes[0].detail)
                return RunResult(run_id, "paused")
            order = list(zip(agent_ids, outcomes))
            self.rng.shuffle(order)
            posted: list[dict[str, Any]] = []
            with transaction(self.conn):
                for agent_id, outcome in order:
                    posted.append(self._post(board, ws, run_id, rnd, agent_id, outcome))
                self.conn.execute(
                    "INSERT INTO rounds (run_id, round, workspace_commit, completed_at) VALUES (?, ?, ?, ?)",
                    (run_id, rnd, ws.head(), now_iso()),
                )
            for event in posted:
                events.emit("action_posted", **event)
            events.emit("round_completed", round=rnd)
            completed = rnd
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision:
                return self._close(run_id, goal, board, events, rnd, decision)
        decision = stop_decision(board.entries(), agent_ids, completed, self.settings) or StopDecision("budget")
        return self._close(run_id, goal, board, events, completed, decision)

    async def _step(
        self, agent_id: str, rnd: int, goal: str, agent_ids: Sequence[str],
        board: Board, ws: Workspace, events: EventLog, library: Sequence[str],
    ) -> StepOutcome:
        events.emit("agent_step_started", round=rnd, agent=agent_id)
        try:
            inputs = collect_inputs(agent_id=agent_id, goal=goal, round=rnd, board=board, store=self.store,
                                    workspace=ws, view_entries=self.settings.view_entries,
                                    half_life=self.settings.reputation_half_life_runs, library=library)
            who = make_who(inputs.profiles)
            system = system_prompt(
                who(agent_id), self.store.private(agent_id)["temperament.md"], self.settings.max_reads
            )
            client = self.clients[agent_id]
            budget = max(2_000, int(client.context_window * 3 * 0.75) - len(system))
            outcome = await take_step(
                client=client, system=system, view=build_view(inputs, budget_chars=budget), round=rnd,
                max_reads=self.settings.max_reads,
                reader=make_reader(ws, inputs.entries, who, budget // 4,
                                   reviews=lambda a: render_public_reviews(self.conn, a, who, budget // 4)),
                validate=make_validator(inputs.entries, agent_ids),
            )
        except Exception as exc:  # one agent's bad step must never take down the run
            outcome = StepOutcome(Pass(type="pass"), "step_error", f"{type(exc).__name__}: {exc}")
        events.emit("agent_step_finished", round=rnd, agent=agent_id, action=outcome.action.type, failure=outcome.failure)
        return outcome

    async def _library(self, run_id: str, goal: str, events: EventLog) -> list[str]:
        """Methods from earlier runs most similar to the goal, rendered for the View. Never fails the run."""
        if self.embedder is None:
            return []
        try:
            found = await retrieve(self.conn, self.embedder, goal, self.library_top_k, exclude_run=run_id)
        except ModelError as exc:
            events.emit("library_unavailable", detail=str(exc))
            return []
        events.emit("library_retrieved", methods=[method.id for method, _ in found])
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        return render_methods([method for method, _ in found], who)

    def _fail(self, run_id: str, rnd: int, agent_id: str, kind: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, rnd, agent_id, kind, detail, now_iso()),
        )

    def _post(
        self, board: Board, ws: Workspace, run_id: str, rnd: int, agent_id: str, outcome: StepOutcome
    ) -> dict[str, Any]:
        action = outcome.action
        failure = outcome.failure
        if failure:
            entry = board.append(rnd, agent_id, "pass", {"failure": failure})
            self._fail(run_id, rnd, agent_id, failure, outcome.detail)
        elif isinstance(action, WriteFile):
            path = action.path
            try:
                version = ws.write(path, action.base_version, action.content, author=agent_id, note=action.note)
                path = ws.canonical_path(path)
            except (InvalidPath, OSError, GitError, UnicodeError) as exc:
                failure = "write_error"
                entry = board.append(rnd, agent_id, "pass", {"failure": failure})
                self._fail(run_id, rnd, agent_id, failure, f"{type(exc).__name__}: {exc}")
                board.append(rnd, SYSTEM, "write_rejected", {
                    "agent": agent_id, "path": path, "reason": f"{type(exc).__name__}: {exc}",
                }, refs=[entry.id])
            except WriteConflict as exc:
                failure = "write_conflict"
                entry = board.append(rnd, agent_id, "pass", {"failure": failure})
                self._fail(run_id, rnd, agent_id, failure, str(exc))
                board.append(rnd, SYSTEM, "write_rejected", {
                    "agent": agent_id, "path": path, "base_version": action.base_version,
                    "current_version": exc.current_version,
                }, refs=[entry.id])
            else:
                entry = board.append(rnd, agent_id, "write_file", {"path": path, "version": version, "note": action.note})
                board.append(rnd, SYSTEM, "artifact_changed", {
                    "agent": agent_id, "path": path, "from_version": action.base_version,
                    "to_version": version, "note": action.note,
                }, refs=[entry.id])
        else:
            entry = board.append(rnd, agent_id, action.type, action.model_dump(exclude={"type"}), refs=target_refs(action))
        if outcome.reasoning:
            self.conn.execute(
                "INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, ?, ?, ?)",
                (entry.id, run_id, agent_id, outcome.model, outcome.reasoning),
            )
        return {"round": rnd, "agent": agent_id, "entry": entry.id, "type": entry.type, "failure": failure}

    def _close(
        self, run_id: str, goal: str, board: Board, events: EventLog, rnd: int, decision: StopDecision
    ) -> RunResult:
        with transaction(self.conn):
            board.append(rnd, SYSTEM, "run_closed", {"reason": decision.reason, "call_id": decision.call_id})
            self.conn.execute(
                "UPDATE runs SET status = 'closed', ended_reason = ? WHERE id = ?", (decision.reason, run_id)
            )
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        write_report(self.runs_dir / run_id, goal, board.entries(), decision, who)
        events.emit("run_closed", reason=decision.reason, call_id=decision.call_id)
        return RunResult(run_id, "closed", decision.reason, decision.call_id)
