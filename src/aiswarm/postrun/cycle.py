"""The post-run cycle: plain code that asks each agent to reflect, review, respond, and update. It never steers."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiswarm.board import Board, Entry
from aiswarm.db import now_iso, transaction
from aiswarm.events import EventLog
from aiswarm.journal import JOURNAL_MAX, journal_section, write_journal
from aiswarm.library import embed_missing, record_methods
from aiswarm.models.base import Embedder, ModelClient, ModelError
from aiswarm.norms import DIMENSION_NAME_MAX, NORM_MAX, add_proposal, current_norms, dimensions, render_norms
from aiswarm.postrun.ask import ReplyError, ask
from aiswarm.postrun.feedback import feedback_packet, interactions, transcript
from aiswarm.postrun.replies import JournalReply, ResponsesReply, ReviewReply, UpdateReply
from aiswarm.profiles import HEADLINE_MAX, INTRO_MAX, NAME_MAX, PRIVATE_MAX, PRONOUNS_MAX, ProfileError, ProfileStore
from aiswarm.prompts import render
from aiswarm.reviews import Review, add_response, add_review, render_review, responses_for, reviews_of
from aiswarm.view import Who, make_who

STEPS = ("journal", "review", "responses", "update", "library")
_NOTES = ("self.md", "lessons.md", "peer-notes.md")


@dataclass(frozen=True)
class PostRunResult:
    run_id: str
    status: str
    failed: int = 0


@dataclass(frozen=True)
class _Context:
    run_id: str
    goal: str
    agent_ids: list[str]
    entries: list[Entry]
    who: Who
    answer: str
    events: EventLog


@dataclass(frozen=True)
class _Call:
    step: str
    agent_id: str
    subject: str
    run: Callable[[], Awaitable[str | None]]


class PostRun:
    def __init__(
        self, *, conn: sqlite3.Connection, store: ProfileStore, runs_dir: Path,
        clients: Mapping[str, ModelClient], sink: Callable[[dict[str, Any]], None] | None = None,
        embedder: Embedder | None = None,
    ):
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink
        self.embedder = embedder

    async def run(self, run_id: str) -> PostRunResult:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] != "closed":
            raise ValueError(f"run {run_id} is not closed")
        answer = self.runs_dir / run_id / "report" / "answer.md"
        ctx = _Context(
            run_id=run_id, goal=row["goal"], agent_ids=json.loads(row["agent_ids"]),
            entries=Board(self.conn, run_id).entries(),
            who=make_who({i: self.store.public(i) for i in self.store.ids()}),
            answer=answer.read_text() if answer.exists() else "",
            events=EventLog(self.runs_dir / run_id / "events.jsonl", sink=self.sink),
        )
        ctx.events.emit("postrun_started", run_id=run_id)
        failed = 0
        for step in STEPS:
            pending = [call for call in self._calls(step, ctx) if not self._done(run_id, call)]
            if not pending:
                continue
            outcomes = await asyncio.gather(*(self._attempt(ctx, call) for call in pending))
            failed += sum(outcome != "done" for outcome in outcomes)
            # Pause only for an outage: every call hit a model error and nothing in this step ever finished.
            # A resumed step whose only pending calls belong to one dead backend must not stall everyone else.
            if all(outcome == "model_error" for outcome in outcomes) and not self._step_started(run_id, step):
                ctx.events.emit("postrun_paused", step=step)
                return PostRunResult(run_id, "paused", failed)
        ctx.events.emit("postrun_finished", failed=failed)
        return PostRunResult(run_id, "done", failed)

    # --- bookkeeping -------------------------------------------------------------------------

    def _calls(self, step: str, ctx: _Context) -> list[_Call]:
        if step == "journal":
            return [_Call(step, a, "", lambda a=a: self._journal(ctx, a)) for a in ctx.agent_ids]
        if step == "review":
            files = [(r["path"], r["author"]) for r in self.conn.execute(
                "SELECT path, author FROM files WHERE run_id = ?", (ctx.run_id,))]
            pairs = interactions(ctx.entries, files, ctx.agent_ids)
            return [
                _Call(step, reviewer, reviewee, lambda r=reviewer, e=reviewee: self._review(ctx, r, e))
                for reviewer in ctx.agent_ids for reviewee in [*sorted(pairs[reviewer]), reviewer]
            ]
        if step == "responses":
            return [
                _Call(step, a, "", lambda a=a: self._responses(ctx, a))
                for a in ctx.agent_ids if reviews_of(self.conn, a, run_id=ctx.run_id)
            ]
        if step == "update":
            return [_Call(step, a, "", lambda a=a: self._update(ctx, a)) for a in ctx.agent_ids]
        if step == "library":
            return [_Call(step, "system", "", lambda: self._library(ctx))]
        raise ValueError(f"unknown step {step!r}")

    def _done(self, run_id: str, call: _Call) -> bool:
        done = self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step = ? AND agent_id = ? AND subject = ? "
            "AND status = 'done'", (run_id, call.step, call.agent_id, call.subject),
        ).fetchone() is not None
        if done and call.step == "responses":
            return not self._unanswered(run_id, call.agent_id)  # a review added on retry reopens the step
        return done

    def _step_started(self, run_id: str, step: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step = ? AND status = 'done'", (run_id, step)
        ).fetchone() is not None

    def _unanswered(self, run_id: str, agent_id: str) -> list[Review]:
        reviews = reviews_of(self.conn, agent_id, run_id=run_id)
        answered = responses_for(self.conn, [r.id for r in reviews])
        return [r for r in reviews if r.id not in answered]

    def _mark(self, run_id: str, call: _Call, status: str, detail: str, reasoning: str | None) -> None:
        self.conn.execute(
            "INSERT INTO postrun_steps (run_id, step, agent_id, subject, status, detail, reasoning, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, step, agent_id, subject) DO UPDATE SET "
            "status = excluded.status, detail = excluded.detail, reasoning = excluded.reasoning, "
            "updated_at = excluded.updated_at",
            (run_id, call.step, call.agent_id, call.subject, status, detail, reasoning, now_iso()),
        )

    async def _attempt(self, ctx: _Context, call: _Call) -> str:
        try:
            reasoning = await call.run()
        except ModelError as exc:
            kind, detail = "model_error", str(exc)
        except ReplyError as exc:
            kind, detail = "invalid_reply", str(exc)
        except Exception as exc:  # one agent's bad step must never take down the cycle
            kind, detail = "step_error", f"{type(exc).__name__}: {exc}"
        else:
            self._mark(ctx.run_id, call, "done", "", reasoning)
            ctx.events.emit("postrun_step_finished", step=call.step, agent=call.agent_id, subject=call.subject, status="done")
            return "done"
        self._mark(ctx.run_id, call, "failed", detail, None)
        self.conn.execute(
            "INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) VALUES (?, 0, ?, ?, ?, ?)",
            (ctx.run_id, call.agent_id, f"postrun:{call.step}:{kind}", detail, now_iso()),
        )
        ctx.events.emit("postrun_step_finished", step=call.step, agent=call.agent_id, subject=call.subject, status=kind)
        return kind

    # --- inputs ------------------------------------------------------------------------------

    def _system(self, ctx: _Context, agent_id: str) -> str:
        private = self.store.private(agent_id)
        notes = [f"## {name}\n{private[name].strip()}" for name in _NOTES if private[name].strip()]
        block = "\n# Your private notes (only you can see these)\n" + "\n\n".join(notes) if notes else ""
        return render("postrun_system.md", display=ctx.who(agent_id), temperament=private["temperament.md"].strip(),
                      private_notes=block)

    def _packet(self, ctx: _Context, agent_id: str) -> str:
        failures = [(r["round"], r["kind"], r["detail"]) for r in self.conn.execute(
            "SELECT round, kind, detail FROM failures WHERE run_id = ? AND agent_id = ? AND round > 0 ORDER BY id",
            (ctx.run_id, agent_id))]
        return feedback_packet(agent_id, run_id=ctx.run_id, goal=ctx.goal, entries=ctx.entries, who=ctx.who,
                               answer=ctx.answer, failures=failures)

    # --- steps -------------------------------------------------------------------------------

    async def _journal(self, ctx: _Context, agent_id: str) -> str | None:
        request = render("journal_request.md", packet=self._packet(ctx, agent_id), journal_max=JOURNAL_MAX)
        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, JournalReply)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "before", answer.reply.journal)
        return answer.reasoning

    async def _review(self, ctx: _Context, reviewer: str, reviewee: str) -> str | None:
        client = self.clients[reviewer]
        own = reviewer == reviewee
        name = ctx.who(reviewee)
        request = render(
            "review_request.md",
            heading="yourself" if own else name,
            run_id=ctx.run_id, goal=ctx.goal,
            authors="you" if own else f"you and {name}",
            transcript=transcript(ctx.entries, ctx.who, {reviewer, reviewee}, max(2_000, int(client.context_window * 1.5))),
            instruction="Review your own work in this run." if own else f"Review the work of {name} in this run.",
            subject="you" if own else "this colleague",
            object="yourself" if own else "them",
            private_reader="Only you will read this." if own else f"Only {name} will read this.",
            visibility=(
                "Other members will not see this self-review." if own else
                "Your review is signed with your name. Other members can read it in later runs, except private_note."
            ),
        )
        valid = {entry.id for entry in ctx.entries}

        def check(reply: ReviewReply) -> None:
            unknown = [i for i in reply.evidence if i not in valid]
            if unknown:
                raise ReplyError(f"entry #{unknown[0]} is not part of this run")

        answer = await ask(client, self._system(ctx, reviewer), request, ReviewReply, check)
        r = answer.reply
        add_review(self.conn, run_id=ctx.run_id, reviewer=reviewer, reviewee=reviewee, more_of=r.more_of,
                   less_of=r.less_of, private_note=r.private_note, evidence=r.evidence, overall=r.overall,
                   overall_reason=r.overall_reason)
        return answer.reasoning

    async def _responses(self, ctx: _Context, agent_id: str) -> str | None:
        reviews = self._unanswered(ctx.run_id, agent_id)
        text = "\n\n".join(render_review(r, ctx.who, private=True) for r in reviews)
        request = render("responses_request.md", run_id=ctx.run_id, reviews=text)
        expected = sorted(r.id for r in reviews)

        def check(reply: ResponsesReply) -> None:
            if sorted(item.review_id for item in reply.responses) != expected:
                raise ReplyError(f"give exactly one response for each review id: {expected}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, ResponsesReply, check)
        with transaction(self.conn):
            for item in answer.reply.responses:
                add_response(self.conn, item.review_id, item.stance, item.reason)
        return answer.reasoning

    async def _update(self, ctx: _Context, agent_id: str) -> str | None:
        reviews = reviews_of(self.conn, agent_id, run_id=ctx.run_id)
        responses = responses_for(self.conn, [r.id for r in reviews])
        request = render(
            "update_request.md",
            packet=self._packet(ctx, agent_id),
            first_journal=journal_section(self.conn, agent_id, ctx.run_id, "before") or "(none)",
            reviews="\n\n".join(
                render_review(r, ctx.who, private=True, response=responses.get(r.id)) for r in reviews
            ) or "(no reviews)",
            journal_max=JOURNAL_MAX, private_max=PRIVATE_MAX, name_max=NAME_MAX, pronouns_max=PRONOUNS_MAX,
            headline_max=HEADLINE_MAX, intro_max=INTRO_MAX,
            norms=render_norms(self.conn) or "(none adopted yet)", norm_max=NORM_MAX,
            dimension_max=DIMENSION_NAME_MAX,
        )
        numbers = {number for number, _ in current_norms(self.conn)}
        taken = {d.name.casefold() for d in dimensions(self.conn)}

        def check(reply: UpdateReply) -> None:
            p = reply.proposal
            if p and p.kind == "amendment" and p.target not in numbers:
                raise ReplyError(f"there is no norm {p.target} to amend; current norms: {sorted(numbers) or 'none'}")
            if p and p.kind == "dimension" and p.name.strip().casefold() in taken:
                raise ReplyError(f"a dimension named {p.name.strip()!r} already exists")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, UpdateReply, check)
        reply = answer.reply
        rejected: list[str] = []
        for filename, content in (
            ("lessons.md", reply.lessons), ("self.md", reply.self_description),
            ("temperament.md", reply.temperament), ("peer-notes.md", reply.peer_notes),
        ):
            if content is None:
                continue
            try:
                self.store.write_private(agent_id, filename, content)
            except ProfileError as exc:
                rejected.append(f"{filename}: {exc}")
        if reply.public_profile is not None:
            profile = reply.public_profile
            try:
                self.store.set_public(agent_id, profile.name, profile.headline, profile.introduction,
                                      pronouns=profile.pronouns)
            except ProfileError as exc:
                rejected.append(f"public profile: {exc}")
        if reply.proposal is not None:
            p = reply.proposal
            add_proposal(self.conn, run_id=ctx.run_id, author=agent_id, kind=p.kind, text=p.text.strip(),
                         target=p.target, name=p.name.strip(), question=p.question.strip())
        journal = reply.journal
        if rejected:
            journal += "\n\nRejected changes (previous versions kept):\n" + "\n".join(f"- {r}" for r in rejected)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "after", journal)
        return answer.reasoning

    async def _library(self, ctx: _Context) -> None:
        record_methods(self.conn, ctx.run_id, ctx.goal, ctx.entries)
        if self.embedder is not None:
            await embed_missing(self.conn, self.embedder)
