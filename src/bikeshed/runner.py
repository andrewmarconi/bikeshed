"""The Runner: plain code that ticks rounds, stores posts, and applies stop rules. It never steers."""

import json
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bikeshed.actions import NOTE_MAX, POLL, STEP, STEP_P2, THINK, Pass, PrivateMessage, WriteFile, target_refs
from bikeshed.agent import StepOutcome, make_reader, make_validator, system_prompt, take_step
from bikeshed.board import SYSTEM, Board, Entry, public
from bikeshed.config import RunSettings
from bikeshed.consensus import StopDecision, stop_decision
from bikeshed.db import now_iso, transaction
from bikeshed.events import EventLog
from bikeshed.library import render_methods, retrieve
from bikeshed.models.base import Embedder, ModelClient, ModelError
from bikeshed.phases import RoundContext, phases
from bikeshed.profiles import ProfileStore
from bikeshed.prompts import render
from bikeshed.report import write_report
from bikeshed.reviews import render_public_reviews
from bikeshed.scorer import FEEDBACK_FILE, score
from bikeshed.view import build_view, collect_inputs, make_who, render_entry
from bikeshed.gitrepo import GitError
from bikeshed.workspace import InvalidPath, Workspace, WriteConflict


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
        scorer: ModelClient | None = None,
    ):
        self.settings = settings
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink
        self.rng = rng  # tests inject one; otherwise each round is shuffled from the run's recorded seed
        self.embedder = embedder
        self.library_top_k = library_top_k
        self.scorer = scorer  # None: a passing call closes the run unscored (library and tests); the CLI always sets one

    def _events(self, run_id: str) -> EventLog:
        return EventLog(self.runs_dir / run_id / "events.jsonl", sink=self.sink)

    def _workspace(self, run_id: str) -> Workspace:
        return Workspace(self.conn, run_id, self.runs_dir / run_id / "workspace")

    def start(
        self, goal: str, agent_ids: Sequence[str], *, goal_id: str | None = None,
        goal_category: str | None = None, is_benchmark: bool = False, process: int = 1,
    ) -> str:
        if process not in (1, 2):
            raise ValueError("process must be 1 or 2")
        if not agent_ids:
            raise ValueError("a run needs at least one agent")
        unknown = [a for a in agent_ids if a not in self.store.ids()]
        if unknown:
            raise ValueError(f"unknown agents: {unknown}")
        controls = [a for a in agent_ids if self.store.record(a).control]
        for agent_id in controls:
            if not self.store.has_seed_public(agent_id):
                raise ValueError(f"control agent {agent_id} has no seed profile; run `bikeshed agents introduce {agent_id}`")
        # Reset before the run exists: a failed reset must not leave a run whose control agent kept its memory.
        name_restored = {agent_id: self.store.reset_to_seed(agent_id) for agent_id in controls}
        run_id = new_run_id()
        seed = secrets.randbits(32)
        (self.runs_dir / run_id).mkdir(parents=True)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at, seed, goal_id, goal_category, is_benchmark, "
                "process) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso(), seed, goal_id, goal_category, int(is_benchmark),
                 process),
            )
            Board(self.conn, run_id).append(0, SYSTEM, "goal", {"goal": goal})
        self._workspace(run_id)
        events = self._events(run_id)
        events.emit("run_started", run_id=run_id, goal=goal, agents=list(agent_ids))
        for agent_id in controls:  # control agents carry no experience between runs
            events.emit("control_reset", agent=agent_id)
            if not name_restored[agent_id]:
                events.emit("control_reset_name_kept", agent=agent_id)
        return run_id

    async def run(self, run_id: str) -> RunResult:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] == "closed":
            return RunResult(run_id, "closed", row["ended_reason"])
        goal, agent_ids = row["goal"], json.loads(row["agent_ids"])
        seed = row["seed"]
        board, ws, events = Board(self.conn, run_id), self._workspace(run_id), self._events(run_id)

        last = self.conn.execute(
            "SELECT round, workspace_commit FROM rounds WHERE run_id = ? ORDER BY round DESC LIMIT 1", (run_id,)
        ).fetchone()
        ws.reset_to(last["workspace_commit"] if last else ws.initial_commit())
        completed = last["round"] if last else 0
        self._discard_after(run_id, completed, events)
        self.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
        library = await self._library(run_id, goal, events)
        settled = await self._settle(run_id, goal, agent_ids, board, ws, events, completed,
                                     final=completed >= self.settings.max_rounds)
        if settled is not None:  # e.g. resumed after a pause at the Scorer: rule before any new round
            return settled

        for rnd in range(completed + 1, self.settings.max_rounds + 1):
            events.emit("round_started", round=rnd)
            ctx = RoundContext(run_id, rnd, goal, agent_ids, seed, row["process"], board, ws, events, library)
            for phase in phases(row["process"], rnd):
                detail = await phase(self, ctx)
                if detail is not None:
                    self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                    events.emit("run_paused", round=rnd, detail=detail)
                    return RunResult(run_id, "paused")
            with transaction(self.conn):
                self.conn.execute(
                    "INSERT INTO rounds (run_id, round, workspace_commit, completed_at) VALUES (?, ?, ?, ?)",
                    (run_id, rnd, ws.head(), now_iso()),
                )
            events.emit("round_completed", round=rnd)
            completed = rnd
            settled = await self._settle(run_id, goal, agent_ids, board, ws, events, rnd, final=False)
            if settled is not None:
                return settled
        settled = await self._settle(run_id, goal, agent_ids, board, ws, events, completed, final=True)
        assert settled is not None
        return settled

    def _discard_after(self, run_id: str, completed: int, events: EventLog) -> None:
        """Remove what an interrupted round left behind, so that round replays from its start."""
        rows = self.conn.execute(
            "SELECT id, type, payload FROM entries WHERE run_id = ? AND round > ?", (run_id, completed)
        ).fetchall()
        if not rows:
            self.conn.execute("DELETE FROM failures WHERE run_id = ? AND round > ?", (run_id, completed))
            return
        ids = [r["id"] for r in rows]
        marks = ", ".join("?" * len(ids))
        written = [(json.loads(r["payload"])["path"], json.loads(r["payload"])["version"])
                   for r in rows if r["type"] == "write_file"]
        with transaction(self.conn):
            self.conn.execute(f"DELETE FROM traces WHERE entry_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM entries WHERE id IN ({marks})", ids)
            self.conn.executemany("DELETE FROM files WHERE run_id = ? AND path = ? AND version = ?",
                                  [(run_id, path, version) for path, version in written])
            self.conn.execute("DELETE FROM failures WHERE run_id = ? AND round > ?", (run_id, completed))
        events.emit("round_discarded", round=completed + 1, entries=len(ids))

    async def _turn(
        self, agent_id: str, ctx: RoundContext, *, phase: str | None = None, request: str = "",
        shape: Any = STEP, opening_rule: bool = True, max_reads: int | None = None,
    ) -> StepOutcome:
        """One model turn for one agent: its view (plus any request) in, one validated reply out. Never raises."""
        extra = {"phase": phase} if phase else {}
        ctx.events.emit("agent_step_started", round=ctx.round, agent=agent_id, **extra)
        try:
            inputs = collect_inputs(agent_id=agent_id, goal=ctx.goal, round=ctx.round, board=ctx.board,
                                    store=self.store, workspace=ctx.ws, view_entries=self.settings.view_entries,
                                    half_life=self.settings.reputation_half_life_runs, library=ctx.library,
                                    max_rounds=self.settings.max_rounds if ctx.process == 2 else None)
            who = make_who(inputs.profiles)
            template = "agent_system.md" if ctx.process == 1 else "process2_agent_system.md"
            system = system_prompt(
                who(agent_id), self.store.private(agent_id)["temperament.md"], self.settings.max_reads, template
            )
            client = self.clients[agent_id]
            budget = max(2_000, int(client.context_window * 3 * 0.75) - len(system) - len(request))
            view = build_view(inputs, budget_chars=budget, opening_hint=phase is None)
            outcome = await take_step(
                client=client, system=system, view=f"{view}\n\n{request}" if request else view, round=ctx.round,
                max_reads=self.settings.max_reads if max_reads is None else max_reads,
                reader=make_reader(ctx.ws, inputs.entries, who, budget // 4,
                                   reviews=lambda a: render_public_reviews(self.conn, a, who, budget // 4)),
                validate=make_validator(inputs.entries, ctx.agent_ids, agent_id),
                shape=shape, opening_rule=opening_rule,
            )
        except Exception as exc:  # one agent's bad step must never take down the run
            outcome = StepOutcome(Pass(type="pass"), "step_error", f"{type(exc).__name__}: {exc}")
        ctx.events.emit("agent_step_finished", round=ctx.round, agent=agent_id, action=outcome.action.type,
                        failure=outcome.failure, **extra)
        return outcome

    async def _step(self, agent_id: str, ctx: RoundContext) -> StepOutcome:
        """A react turn: one posting action."""
        return await self._turn(agent_id, ctx, shape=STEP if ctx.process == 1 else STEP_P2)

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
        elif isinstance(action, PrivateMessage):
            entry = board.append(rnd, agent_id, "private_message", {"to": action.to, "text": action.text},
                                 to=action.to, visible_to=[agent_id, action.to])
        else:
            entry = board.append(rnd, agent_id, action.type, action.model_dump(exclude={"type"}), refs=target_refs(action))
        self._trace(entry.id, run_id, agent_id, outcome)
        return {"round": rnd, "agent": agent_id, "entry": entry.id, "type": entry.type, "failure": failure}

    def _trace(self, entry_id: int, run_id: str, agent_id: str, outcome: StepOutcome) -> None:
        if outcome.reasoning:
            self.conn.execute(
                "INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, ?, ?, ?)",
                (entry_id, run_id, agent_id, outcome.model, outcome.reasoning),
            )

    async def _think(self, agent_id: str, ctx: RoundContext) -> StepOutcome:
        request = render("process2_think_request.md", note_max=f"{NOTE_MAX:,}")
        return await self._turn(agent_id, ctx, phase="think", request=request, shape=THINK, opening_rule=False)

    def _post_note(self, ctx: RoundContext, agent_id: str, outcome: StepOutcome) -> dict[str, Any] | None:
        """A think note, private to its author. A failed think records the failure and fabricates nothing."""
        if outcome.failure:
            self._fail(ctx.run_id, ctx.round, agent_id, f"think:{outcome.failure}", outcome.detail)
            return None
        entry = ctx.board.append(ctx.round, agent_id, "think", {"text": outcome.action.text}, visible_to=[agent_id])
        self._trace(entry.id, ctx.run_id, agent_id, outcome)
        return {"round": ctx.round, "agent": agent_id, "entry": entry.id, "type": "think", "failure": None}

    async def _poll(self, agent_id: str, ctx: RoundContext, call: Entry) -> StepOutcome:
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        request = render("process2_poll_request.md", call=render_entry(call, who), call_id=call.id)
        return await self._turn(agent_id, ctx, phase="poll", request=request, shape=POLL, opening_rule=False,
                                max_reads=0)

    def _post_poll(self, ctx: RoundContext, agent_id: str, outcome: StepOutcome, call: Entry) -> dict[str, Any] | None:
        """support / object become ordinary votes; abstain is recorded publicly; a failure fabricates nothing."""
        if outcome.failure:
            self._fail(ctx.run_id, ctx.round, agent_id, f"poll:{outcome.failure}", outcome.detail)
            return None
        answer = outcome.action
        if answer.stance == "abstain":
            entry = ctx.board.append(ctx.round, agent_id, "abstain", {"target": call.id, "reason": answer.reason},
                                     refs=[call.id])
        else:
            entry = ctx.board.append(ctx.round, agent_id, "vote",
                                     {"target": call.id, "stance": answer.stance, "reason": answer.reason},
                                     refs=[call.id])
        self._trace(entry.id, ctx.run_id, agent_id, outcome)
        return {"round": ctx.round, "agent": agent_id, "entry": entry.id, "type": entry.type, "failure": None}

    def abandon(self, run_id: str) -> RunResult:
        """Close a run that will not be finished. Every entry is kept, including a partial last round."""
        row = self.conn.execute("SELECT goal, status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"no run {run_id!r}")
        if row["status"] == "closed":
            raise ValueError(f"run {run_id} is already closed")
        last = self.conn.execute("SELECT MAX(round) FROM entries WHERE run_id = ?", (run_id,)).fetchone()[0]
        return self._close(run_id, row["goal"], Board(self.conn, run_id), self._events(run_id), last or 0,
                           StopDecision("abandoned"))

    async def _settle(self, run_id: str, goal: str, agent_ids: list[str], board: Board, ws: Workspace,
                      events: EventLog, rnd: int, *, final: bool) -> RunResult | None:
        """The stop rules after a round. A call that passes goes to the Scorer before the run may close."""
        while True:
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision is None or decision.reason != "consensus" or self.scorer is None:
                break
            call = next(e for e in board.entries() if e.id == decision.call_id)
            result = await score(self.scorer, goal, str(call.payload.get("synthesis", "")))
            if result.ruling is None:  # nothing is guessed: pause, and the resumed run asks again
                self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                events.emit("run_paused", round=rnd, detail=f"scorer: {result.detail}")
                return RunResult(run_id, "paused")
            ruling = result.ruling
            events.emit("scorer_ruled", call=call.id, accepted=ruling.accepted, feedback=ruling.feedback,
                        model=result.model)
            if ruling.accepted:
                return self._close(run_id, goal, board, events, rnd, decision,
                                   before=lambda: self._accept(board, rnd, call, agent_ids))
            self._reject(run_id, board, ws, rnd, call, ruling.feedback)
        if decision is not None:
            return self._close(run_id, goal, board, events, rnd, decision)
        return self._close(run_id, goal, board, events, rnd, StopDecision("budget")) if final else None

    def _accept(self, board: Board, rnd: int, call: Entry, agent_ids: Sequence[str]) -> None:
        """One point per member the caller credited (the caller too, if it credited itself); no one else."""
        board.append(rnd, SYSTEM, "scorer_accepted", {"call_id": call.id}, refs=[call.id])
        credited: dict[str, str] = {}
        for credit in call.payload.get("credits", []):
            agent = credit.get("agent_id")
            if agent in agent_ids and agent not in credited:
                credited[agent] = str(credit.get("contribution", ""))
        self.conn.executemany(
            "INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) VALUES (?, ?, ?, ?, ?)",
            [(board.run_id, agent, call.id, text, now_iso()) for agent, text in credited.items()])

    def _reject(self, run_id: str, board: Board, ws: Workspace, rnd: int, call: Entry, feedback: str) -> None:
        """The call is spent; its feedback goes on the board and into the workspace before the next round."""
        path = FEEDBACK_FILE
        with transaction(self.conn):
            ws.write(path, ws.current_version(path), f"# Scorer feedback on call #{call.id}\n\n{feedback.strip()}\n",
                     author=SYSTEM, note=f"Scorer feedback on call #{call.id}")
            board.append(rnd, SYSTEM, "scorer_rejected", {"call_id": call.id, "feedback": feedback.strip()},
                         refs=[call.id])
            # the round's checkpoint now includes the feedback, so a resumed run keeps it
            self.conn.execute("UPDATE rounds SET workspace_commit = ? WHERE run_id = ? AND round = ?",
                              (ws.head(), run_id, rnd))

    def _close(
        self, run_id: str, goal: str, board: Board, events: EventLog, rnd: int, decision: StopDecision,
        before: Callable[[], None] | None = None,
    ) -> RunResult:
        with transaction(self.conn):
            if before is not None:
                before()  # e.g. the Scorer's points: awarded in the same transaction as the close, never twice
            board.append(rnd, SYSTEM, "run_closed", {"reason": decision.reason, "call_id": decision.call_id})
            self.conn.execute(
                "UPDATE runs SET status = 'closed', ended_reason = ? WHERE id = ?", (decision.reason, run_id)
            )
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        write_report(self.runs_dir / run_id, goal, public(board.entries()), decision, who)
        events.emit("run_closed", reason=decision.reason, call_id=decision.call_id)
        return RunResult(run_id, "closed", decision.reason, decision.call_id)
