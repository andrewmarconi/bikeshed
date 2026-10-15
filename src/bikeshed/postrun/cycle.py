"""The post-run cycle: plain code that asks each agent to reflect, review, respond, and update. It never steers."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bikeshed.board import Board, Entry, public, visible
from bikeshed.briefs import load as load_briefs
from bikeshed.briefs import load_conflicts, reveal
from bikeshed.consensus import StopDecision
from bikeshed.db import now_iso, transaction
from bikeshed.events import EventLog
from bikeshed.journal import JOURNAL_MAX, journal_section, write_journal
from bikeshed.library import embed_missing, record_methods
from bikeshed.models.base import Embedder, ModelClient, ModelError
from bikeshed.norms import (
    DIMENSION_NAME_MAX, NORM_MAX, add_norm_critique, add_norm_vote, add_proposal, critiques_of, current_norms, decide,
    dimensions, proposals, render_norms, render_proposal,
)
from bikeshed.postrun.ask import ReplyError, ask
from bikeshed.postrun.feedback import feedback_packet, participants, transcript
from bikeshed.postrun.replies import (
    JournalReply, NarrativeReply, NormCritiquesReply, NormVotesReply, ResponsesReply, ReviewReply, UpdateReply,
    is_keep, keeps_pronouns,
)
from bikeshed.profiles import HEADLINE_MAX, INTRO_MAX, NAME_MAX, PRIVATE_MAX, PRONOUNS_MAX, ProfileError, ProfileStore
from bikeshed.prompts import render
from bikeshed.report import account
from bikeshed.reviews import (
    Review, add_response, add_review, ratings_for, render_review, responses_for, reviews_of, set_ratings,
)
from bikeshed.scorer import run_summary
from bikeshed.view import Who, make_who

_OPTIONAL = ("library", "narrative")  # their failure never pauses the cycle
STEPS = ("journal", "review", "responses", "update", "norms_critique", "norms_vote", "library", "narrative")
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
        embedder: Embedder | None = None, quorum_slack: int = 1, narrator: ModelClient | None = None,
    ):
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink
        self.embedder = embedder
        self.quorum_slack = quorum_slack
        self.narrator = narrator

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
            if pending:
                outcomes = await asyncio.gather(*(self._attempt(ctx, call) for call in pending))
                failed += sum(outcome != "done" for outcome in outcomes)
                # Pause only for an outage: every call hit a model error and nothing in this step ever finished.
                # A resumed step whose only pending calls belong to one dead backend must not stall everyone else.
                if (step not in _OPTIONAL and all(outcome == "model_error" for outcome in outcomes)
                        and not self._step_started(run_id, step)):
                    ctx.events.emit("postrun_paused", step=step)
                    return PostRunResult(run_id, "paused", failed)
            if step == "norms_vote":
                self._decide(ctx)
        ctx.events.emit("postrun_finished", failed=failed)
        return PostRunResult(run_id, "done", failed)

    # --- bookkeeping -------------------------------------------------------------------------

    def _calls(self, step: str, ctx: _Context) -> list[_Call]:
        if step == "journal":
            return [_Call(step, a, "", lambda a=a: self._journal(ctx, a)) for a in ctx.agent_ids]
        if step == "review":
            files = [(r["path"], r["author"]) for r in self.conn.execute(
                "SELECT path, author FROM files WHERE run_id = ?", (ctx.run_id,))]
            taking_part = participants(public(ctx.entries), files, ctx.agent_ids)

            def subjects(reviewer: str) -> list[str]:
                others = sorted(p for p in taking_part if p != reviewer) if reviewer in taking_part else []
                return [*others, reviewer]

            return [
                _Call(step, reviewer, reviewee, lambda r=reviewer, e=reviewee: self._review(ctx, r, e))
                for reviewer in ctx.agent_ids for reviewee in subjects(reviewer)
            ]
        if step == "responses":
            return [
                _Call(step, a, "", lambda a=a: self._responses(ctx, a))
                for a in ctx.agent_ids if reviews_of(self.conn, a, run_id=ctx.run_id)
            ]
        if step == "update":
            return [_Call(step, a, "", lambda a=a: self._update(ctx, a)) for a in ctx.agent_ids]
        if step in ("norms_critique", "norms_vote"):
            if not proposals(self.conn, ctx.run_id, "open"):
                return []
            method = self._norms_critique if step == "norms_critique" else self._norms_vote
            return [_Call(step, a, "", lambda a=a, m=method: m(ctx, a)) for a in ctx.agent_ids]
        if step == "library":
            return [_Call(step, "system", "", lambda: self._library(ctx))]
        if step == "narrative":
            return [_Call(step, "narrator", "", lambda: self._narrative(ctx))] if self.narrator else []
        raise ValueError(f"unknown step {step!r}")

    def _done(self, run_id: str, call: _Call) -> bool:
        done = self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step = ? AND agent_id = ? AND subject = ? "
            "AND status = 'done'", (run_id, call.step, call.agent_id, call.subject),
        ).fetchone() is not None
        if done and call.step == "responses":
            return not self._unanswered(run_id, call.agent_id)  # a review added on retry reopens the step
        return done

    def _deliberation_started(self, run_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step IN ('norms_critique', 'norms_vote')", (run_id,)
        ).fetchone() is not None

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
        self._mark(ctx.run_id, call, "failed", f"{kind}: {detail}", None)
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

    def _seen(self, ctx: _Context, agent_id: str) -> list[Entry]:
        """What this agent may be shown about the run: no one else's private entries, and no think notes."""
        return [e for e in visible(ctx.entries, agent_id) if e.type != "think"]

    def _packet(self, ctx: _Context, agent_id: str) -> str:
        failures = [(r["round"], r["kind"], r["detail"]) for r in self.conn.execute(
            "SELECT round, kind, detail FROM failures WHERE run_id = ? AND agent_id = ? AND round > 0 ORDER BY id",
            (ctx.run_id, agent_id))]
        return feedback_packet(agent_id, run_id=ctx.run_id, goal=ctx.goal, entries=self._seen(ctx, agent_id),
                               who=ctx.who, answer=ctx.answer, failures=failures, briefs=self._reveal(ctx))

    def _reveal(self, ctx: _Context) -> str:
        return reveal(load_briefs(self.conn, ctx.run_id), load_conflicts(self.conn, ctx.run_id), ctx.who)

    # --- steps -------------------------------------------------------------------------------

    async def _journal(self, ctx: _Context, agent_id: str) -> str | None:
        packet = self._packet(ctx, agent_id)
        notes = [e for e in ctx.entries if e.type == "think" and e.author == agent_id]
        if notes:  # Process 2 only; Process 1 runs have none, so their request is unchanged
            packet += "\n\n## Your private notes from this run\n" + "\n\n".join(
                f"Round {e.round}: {e.payload.get('text', '').strip()}" for e in notes)
        if summary := run_summary(self.conn, ctx.run_id, ctx.entries, ctx.who):
            packet += "\n\n" + summary
        request = render("journal_request.md", packet=packet, journal_max=JOURNAL_MAX)
        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, JournalReply)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "before", answer.reply.journal)
        return answer.reasoning

    async def _review(self, ctx: _Context, reviewer: str, reviewee: str) -> str | None:
        client = self.clients[reviewer]
        own = reviewer == reviewee
        name = ctx.who(reviewee)
        dims = dimensions(self.conn, before_run=ctx.run_id)
        dimension_block = "" if not dims else (
            f"\nAlso rate {'yourself' if own else 'them'} on each of the community's rating dimensions, "
            "as an integer from 1 to 5 with a reason:\n" + "\n".join(f'- "{d.name}": {d.question}' for d in dims) + "\n"
        )
        dimension_json = "" if not dims else ', "dimension_ratings": [{"dimension": "<name>", "rating": <1-5>, "reason": "..."}]'
        revealed = self._reveal(ctx)
        request = render(
            "review_request.md",
            heading="yourself" if own else name,
            run_id=ctx.run_id, goal=ctx.goal,
            authors="you" if own else f"you and {name}",
            transcript=transcript(self._seen(ctx, reviewer), ctx.who, {reviewer, reviewee}, max(2_000, int(client.context_window * 1.5)))
            + (f"\n\n{revealed}" if revealed else ""),
            instruction="Review your own work in this run." if own else f"Review the work of {name} in this run.",
            subject="you" if own else "this colleague",
            object="yourself" if own else "them",
            private_reader="Only you will read this." if own else f"Only {name} will read this.",
            visibility=(
                "Other members will not see this self-review." if own else
                "Your review is signed with your name. Other members can read it in later runs, except private_note."
            ),
            dimension_block=dimension_block, dimension_json=dimension_json,
        )
        valid = {entry.id for entry in visible(ctx.entries, reviewer)}

        def check(reply: ReviewReply) -> None:
            unknown = [i for i in reply.evidence if i not in valid]
            if unknown:
                raise ReplyError(f"entry #{unknown[0]} is not part of this run")
            names = sorted(d.name.casefold() for d in dims)
            if sorted(x.dimension.strip().casefold() for x in reply.dimension_ratings) != names:
                raise ReplyError(f"rate each dimension exactly once: {[d.name for d in dims]}")

        answer = await ask(client, self._system(ctx, reviewer), request, ReviewReply, check)
        r = answer.reply
        stored = add_review(self.conn, run_id=ctx.run_id, reviewer=reviewer, reviewee=reviewee, more_of=r.more_of,
                   less_of=r.less_of, private_note=r.private_note, evidence=r.evidence, overall=r.overall,
                   overall_reason=r.overall_reason)
        by_name = {d.name.casefold(): d.id for d in dims}
        set_ratings(self.conn, stored.id, [(by_name[x.dimension.strip().casefold()], x.rating, x.reason)
                                           for x in r.dimension_ratings])
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
        ratings = ratings_for(self.conn, [r.id for r in reviews])
        request = render(
            "update_request.md",
            packet=self._packet(ctx, agent_id),
            first_journal=journal_section(self.conn, agent_id, ctx.run_id, "before") or "(none)",
            reviews="\n\n".join(
                render_review(r, ctx.who, private=True, response=responses.get(r.id), ratings=ratings[r.id])
                for r in reviews
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
            if is_keep(content):
                continue
            try:
                self.store.write_private(agent_id, filename, content)
            except ProfileError as exc:
                rejected.append(f"{filename}: {exc}")
        if reply.public_profile is not None:
            profile = reply.public_profile
            current = self.store.public(agent_id)
            base = current.model_dump() if current else {}
            changed = {field: getattr(profile, field) for field in ("name", "headline", "introduction")
                       if not is_keep(getattr(profile, field))}
            pronouns = None if keeps_pronouns(profile.pronouns) else profile.pronouns
            if changed or pronouns is not None:
                merged = {field: changed.get(field, base.get(field, "")) for field in ("name", "headline", "introduction")}
                try:
                    self.store.set_public(agent_id, merged["name"], merged["headline"], merged["introduction"],
                                          pronouns=pronouns)
                except ProfileError as exc:
                    rejected.append(f"public profile: {exc}")
        if reply.proposal is not None and self._deliberation_started(ctx.run_id):
            rejected.append("proposal: Your proposal was not considered: this run's proposals had already been deliberated")
        elif reply.proposal is not None:
            p = reply.proposal
            add_proposal(self.conn, run_id=ctx.run_id, author=agent_id, kind=p.kind, text=p.text.strip(),
                         target=p.target, name=p.name.strip(), question=p.question.strip())
        journal = reply.journal
        if rejected:
            journal += "\n\nRejected changes (previous versions kept):\n" + "\n".join(f"- {r}" for r in rejected)
        write_journal(self.conn, self.store, agent_id, ctx.run_id, "after", journal)
        return answer.reasoning

    def _proposals_text(self, ctx: _Context, with_critiques: bool) -> str:
        texts = dict(current_norms(self.conn))
        blocks = []
        for p in proposals(self.conn, ctx.run_id, "open"):
            lines = [render_proposal(p, ctx.who, texts)]
            if with_critiques:
                critiques = critiques_of(self.conn, p.id)
                lines += [f"  critique by {ctx.who(a)}: {points}" for a, points in critiques] or ["  (no critiques)"]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    async def _norms_critique(self, ctx: _Context, agent_id: str) -> str | None:
        open_ids = {p.id for p in proposals(self.conn, ctx.run_id, "open")}
        request = render("norms_critique_request.md", run_id=ctx.run_id, proposals=self._proposals_text(ctx, False),
                         norms=render_norms(self.conn) or "(none adopted yet)")

        def check(reply: NormCritiquesReply) -> None:
            unknown = [c.proposal_id for c in reply.critiques if c.proposal_id not in open_ids]
            if unknown:
                raise ReplyError(f"proposal {unknown[0]} is not one of the open proposals {sorted(open_ids)}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, NormCritiquesReply, check)
        with transaction(self.conn):
            for c in answer.reply.critiques:
                add_norm_critique(self.conn, c.proposal_id, agent_id, c.points)
        return answer.reasoning

    async def _norms_vote(self, ctx: _Context, agent_id: str) -> str | None:
        expected = sorted(p.id for p in proposals(self.conn, ctx.run_id, "open"))
        request = render("norms_vote_request.md", run_id=ctx.run_id, proposals=self._proposals_text(ctx, True),
                         norms=render_norms(self.conn) or "(none adopted yet)", slack=self.quorum_slack)

        def check(reply: NormVotesReply) -> None:
            if sorted(v.proposal_id for v in reply.votes) != expected:
                raise ReplyError(f"give exactly one vote for each proposal id: {expected}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, NormVotesReply, check)
        with transaction(self.conn):
            for v in answer.reply.votes:
                add_norm_vote(self.conn, v.proposal_id, agent_id, v.stance, v.reason)
        return answer.reasoning

    def _decide(self, ctx: _Context) -> None:
        if self.conn.execute(
            "SELECT 1 FROM postrun_steps WHERE run_id = ? AND step = 'norms_vote' AND status = 'failed' "
            "AND detail LIKE 'model_error:%'", (ctx.run_id,),
        ).fetchone():
            # A vote lost to an outage is retried on resume, not counted as non-support.
            ctx.events.emit("norms_deferred", reason="votes pending after a model error")
            return
        decided = decide(self.conn, ctx.run_id, ctx.agent_ids, self.quorum_slack)
        content = render_norms(self.conn)
        path = self.store.root / "norms.md"
        if content and (not path.exists() or path.read_text() != content):
            self.store.write_community_file("norms.md", content, f"norms after run {ctx.run_id}")
        if decided:
            ctx.events.emit("norms_decided", adopted=[p.id for p in decided if p.status == "adopted"],
                            rejected=[p.id for p in decided if p.status == "rejected"])

    async def _library(self, ctx: _Context) -> None:
        record_methods(self.conn, ctx.run_id, ctx.goal, public(ctx.entries))
        if self.embedder is None:
            return
        try:
            await embed_missing(self.conn, self.embedder)
        except ModelError as exc:  # unembedded methods are retried by the next cycle's library step
            self.conn.execute(
                "INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) VALUES (?, 0, 'system', ?, ?, ?)",
                (ctx.run_id, "postrun:library:embed_error", str(exc), now_iso()),
            )
            ctx.events.emit("library_embed_failed", detail=str(exc))

    async def _narrative(self, ctx: _Context) -> str | None:
        assert self.narrator is not None
        closed = next((e for e in reversed(ctx.entries) if e.type == "run_closed"), None)
        decision = StopDecision(closed.payload.get("reason", "budget"), closed.payload.get("call_id")) if closed \
            else StopDecision("budget")
        report = self.runs_dir / ctx.run_id / "report"
        minority = report / "minority.md"
        revealed = self._reveal(ctx)
        request = render(
            "narrative_request.md",
            account=account(ctx.goal, ctx.entries, decision, ctx.who) + (f"\n{revealed}\n" if revealed else ""),
            answer=ctx.answer.strip() or "(no report)",
            minority=minority.read_text().strip() if minority.exists() else "(none)", narrative_max=JOURNAL_MAX,
        )
        answer = await ask(self.narrator, render("narrator_system.md"), request, NarrativeReply)
        report.mkdir(parents=True, exist_ok=True)
        (report / "narrative.md").write_text(
            "# Narrative\n\n_Written by the narrator model from the account and not checked against the record. "
            "Where it disagrees with account.md, the account is right. No agent ever sees this._\n\n"
            + answer.reply.narrative.strip() + "\n"
        )
        return answer.reasoning
