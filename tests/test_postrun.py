import json
import re

import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.events import read_events
from aiswarm.journal import journal_section
from aiswarm.models.base import ModelError
from aiswarm.models.fake import FakeModel
from aiswarm.postrun import PostRun
from aiswarm.reviews import responses_for, reviews_of


def finished_run(conn, tmp_path, make_run, reason="budget"):
    """r1: a, b, c propose; b critiques a; c does nothing else. a and b interacted; c interacted with no one."""
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    board.append(1, "a", "propose", {"answer_summary": "Red", "method": "M"})        # 2
    board.append(1, "b", "propose", {"answer_summary": "Blue", "method": "M"})       # 3
    board.append(1, "c", "propose", {"answer_summary": "Green", "method": "M"})      # 4
    board.append(2, "b", "critique", {"target": 2, "about": "method", "points": "P"}, refs=[2])  # 5
    board.append(2, SYSTEM, "run_closed", {"reason": reason, "call_id": None})       # 6
    conn.execute("UPDATE runs SET status = 'closed', ended_reason = ? WHERE id = 'r1'", (reason,))
    report = tmp_path / "runs" / "r1" / "report"
    report.mkdir(parents=True)
    (report / "answer.md").write_text("# Answer\nCOMPETING-POSITIONS\n")
    return "r1"


def responder(postrun_reply, **overrides):
    def respond(system, messages):
        request = messages[0]["content"]
        step = re.match(r"# Post-run: (\w+)", request).group(1)
        if step in overrides:
            return overrides[step](request)
        return postrun_reply(request)
    return respond


def cycle(store, conn, tmp_path, clients):
    return PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients)


def steps(conn):
    return {(r["step"], r["agent_id"], r["subject"]): r["status"] for r in conn.execute("SELECT * FROM postrun_steps")}


async def test_cycle_writes_journals_reviews_and_responses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply), reasoning=f"REASONING-{a}") for a in "abc"}
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    for agent in "abc":
        assert journal_section(conn, agent, run_id, "before") == "Thoughts."
        assert (store.root / "agents" / agent / "journal" / f"{run_id}.md").exists()
    pairs = {(r["reviewer"], r["reviewee"]) for r in conn.execute("SELECT reviewer, reviewee FROM reviews")}
    assert pairs == {("a", "b"), ("a", "a"), ("b", "a"), ("b", "b"), ("c", "c")}
    for agent in "ab":
        peer = reviews_of(conn, agent, run_id=run_id)
        assert len(peer) == 1 and responses_for(conn, [peer[0].id])[peer[0].id].stance == "accept"
    assert ("responses", "c", "") not in steps(conn)       # c has no peer reviews: no responses step
    assert set(steps(conn).values()) == {"done"}
    reasoning = conn.execute("SELECT reasoning FROM postrun_steps WHERE step = 'journal' AND agent_id = 'b'").fetchone()
    assert reasoning["reasoning"] == "REASONING-b"
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[0] == "postrun_started" and names[-1] == "postrun_finished"
    assert "postrun_step_finished" in names


async def test_packets_tell_each_agent_how_the_run_ended(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    journal_request = clients["c"].calls[0][1][0]["content"]
    assert journal_request.startswith("# Post-run: journal")
    assert "The run ended by budget." in journal_request
    assert "The run ended without consensus." in journal_request
    assert "COMPETING-POSITIONS" in journal_request


async def test_review_citing_unknown_entries_is_corrected_then_recorded_as_failed(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    fabricated = json.dumps({"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [999],
                             "overall": 2, "overall_reason": "r"})
    clients = {
        "a": FakeModel(responder(postrun_reply, review=lambda request: fabricated)),
        "b": FakeModel(responder(postrun_reply)),
        "c": FakeModel(responder(postrun_reply)),
    }
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.status == "done" and result.failed == 2              # a→a and a→b
    assert conn.execute("SELECT COUNT(*) FROM reviews WHERE reviewer = 'a'").fetchone()[0] == 0
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM failures WHERE agent_id = 'a'")]
    assert kinds == ["postrun:review:invalid_reply", "postrun:review:invalid_reply"]
    assert steps(conn)[("review", "a", "b")] == "failed"
    assert journal_section(conn, "a", run_id, "before") == "Thoughts."   # the rest of the cycle still ran


async def test_rerun_retries_only_unfinished_calls_and_never_duplicates(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)

    def crash(request):
        raise RuntimeError("process died")

    first = {
        "a": FakeModel(responder(postrun_reply, review=lambda r: crash(r) if "review of Bea" in r else postrun_reply(r))),
        "b": FakeModel(responder(postrun_reply)),
        "c": FakeModel(responder(postrun_reply)),
    }
    result = await cycle(store, conn, tmp_path, first).run(run_id)
    assert result.failed == 1 and steps(conn)[("review", "a", "b")] == "failed"

    second = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    result = await cycle(store, conn, tmp_path, second).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    assert [len(second[a].calls) for a in "abc"] == [1, 1, 0]        # a's review of b, then b's response to it
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM journals WHERE section = 'before'").fetchone()[0] == 3

    third = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, third).run(run_id)
    assert [len(third[a].calls) for a in "abc"] == [0, 0, 0]


async def test_a_step_where_every_call_hits_a_model_error_pauses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    down = {a: FakeModel(fail_times=10) for a in "abc"}
    result = await cycle(store, conn, tmp_path, down).run(run_id)
    assert result.status == "paused"
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert names[-1] == "postrun_paused"
    up = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    assert (await cycle(store, conn, tmp_path, up).run(run_id)).status == "done"


async def test_cycle_refuses_unknown_and_unclosed_runs(tmp_path, community, make_run):
    store, conn = community
    make_run(conn, "open", ("a",))
    with pytest.raises(KeyError):
        await cycle(store, conn, tmp_path, {}).run("missing")
    with pytest.raises(ValueError, match="not closed"):
        await cycle(store, conn, tmp_path, {}).run("open")


async def test_requests_never_carry_another_agents_private_notes(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    store.write_private("b", "lessons.md", "LESSON-OF-B")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    for agent in "ac":
        for system, messages, _ in clients[agent].calls:
            assert "LESSON-OF-B" not in system and all("LESSON-OF-B" not in m["content"] for m in messages)
    assert any("LESSON-OF-B" in system for system, _, _ in clients["b"].calls)


def update_reply(**fields):
    base = {"journal": "AFTER", "lessons": "NEW-LESSONS", "self_description": None, "temperament": None,
            "peer_notes": None, "public_profile": None, "proposal": None}
    return lambda request: json.dumps({**base, **fields})


async def test_update_writes_second_journal_lessons_and_rewrites(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        self_description="NEW-SELF", temperament="NEW-TEMPERAMENT", peer_notes="NEW-NOTES",
        public_profile={"name": "Alder", "headline": "New headline", "introduction": "New intro."})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    private = store.private("a")
    assert private["lessons.md"] == "NEW-LESSONS" and private["self.md"] == "NEW-SELF"
    assert private["temperament.md"] == "NEW-TEMPERAMENT" and private["peer-notes.md"] == "NEW-NOTES"
    profile = store.public("a")
    assert (profile.name, profile.former_names) == ("Alder", ["Alpha"])
    assert journal_section(conn, "a", run_id, "after") == "AFTER"
    assert store.private("b")["lessons.md"] == "Listen first."      # default responder
    assert store.private("b")["temperament.md"].strip() == "Temperament of b."


async def test_update_request_shows_reviews_with_private_notes_and_responses(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    update = next(m[0]["content"] for _, m, _ in clients["a"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "by Bea (b)" in update and "private note: Between us." in update and "response: accept — Fair." in update
    assert "## Your journal entry from before the reviews\n\nThoughts." in update
    c_update = next(m[0]["content"] for _, m, _ in clients["c"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "private note" not in c_update                       # nobody reviewed c


async def test_rejected_rewrites_keep_previous_versions_and_are_noted(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        temperament="   ", self_description="KEPT-SELF",
        public_profile={"name": "bea", "headline": "h", "introduction": "i"})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.failed == 0
    assert store.private("a")["temperament.md"].strip() == "Temperament of a."
    assert store.public("a").name == "Alpha"
    assert store.private("a")["self.md"] == "KEPT-SELF" and store.private("a")["lessons.md"] == "NEW-LESSONS"
    after = journal_section(conn, "a", run_id, "after")
    assert after.startswith("AFTER\n\nRejected changes (previous versions kept):\n")
    assert "- temperament.md: temperament must not be empty" in after
    assert "- public profile: the name 'bea' is taken" in after


async def test_lessons_over_the_cap_get_one_correction(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    replies = iter([update_reply(lessons="x" * 5000), update_reply(lessons="short")])
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=lambda request: next(replies)(request)))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert store.private("a")["lessons.md"] == "short"


async def test_a_review_added_on_retry_still_gets_a_response(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    Board(conn, run_id).append(2, "c", "question", {"target": 3, "text": "Why blue?"}, refs=[3])  # c and b interact
    fabricated = json.dumps({"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [999],
                             "overall": 2, "overall_reason": "r"})
    first = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    first["a"] = FakeModel(responder(postrun_reply, review=lambda r: fabricated if "review of Bea" in r else postrun_reply(r)))
    await cycle(store, conn, tmp_path, first).run(run_id)
    assert len(reviews_of(conn, "b", run_id=run_id)) == 1                       # only c's review of b so far

    second = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, second).run(run_id)
    reviews = reviews_of(conn, "b", run_id=run_id)
    assert len(reviews) == 2
    assert set(responses_for(conn, [r.id for r in reviews])) == {r.id for r in reviews}
    asked = [m[0]["content"] for _, m, _ in second["b"].calls if m[0]["content"].startswith("# Post-run: responses")]
    assert len(asked) == 1 and "by Alpha (a)" in asked[0] and "by Cyd (c)" not in asked[0]   # only the new review


async def test_resume_with_one_dead_backend_does_not_stall_everyone(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "ab"}
    clients["c"] = FakeModel(fail_times=1000)
    assert (await cycle(store, conn, tmp_path, clients).run(run_id)).status == "done"
    result = await cycle(store, conn, tmp_path, {**clients, "c": FakeModel(fail_times=1000)}).run(run_id)
    assert result.status == "done"
    assert result.failed == 3                     # c's journal, self-review, and update, retried and failed again


async def test_update_can_set_pronouns_and_omitting_them_keeps_them(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    store.set_public("b", "Bea", "Bea headline", "I am Bea.", pronouns="she/her")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {"c": FakeModel(responder(postrun_reply))}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        public_profile={"name": "Alpha", "pronouns": "they/them", "headline": "h", "introduction": "i"})))
    clients["b"] = FakeModel(responder(postrun_reply, update=update_reply(
        public_profile={"name": "Bea", "headline": "new", "introduction": "i"})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert store.public("a").pronouns == "they/them"
    assert store.public("b").pronouns == "she/her" and store.public("b").headline == "new"
    update = next(m[0]["content"] for _, m, _ in clients["a"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert '"pronouns"' in update


async def test_cycle_records_and_embeds_methods(tmp_path, community, make_run, postrun_reply):
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    post = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder())
    assert (await post.run(run_id)).status == "done"
    rows = conn.execute("SELECT author, embedding FROM methods WHERE run_id = ?", (run_id,)).fetchall()
    assert sorted(r["author"] for r in rows) == ["a", "b", "c"] and all(r["embedding"] for r in rows)
    assert steps(conn)[("library", "system", "")] == "done"


async def test_methods_are_recorded_without_an_embedder(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    await cycle(store, conn, tmp_path, {a: FakeModel(responder(postrun_reply)) for a in "abc"}).run(run_id)
    assert conn.execute("SELECT COUNT(*) FROM methods WHERE embedding IS NULL").fetchone()[0] == 3


async def test_an_embeddings_outage_pauses_at_the_library_step(tmp_path, community, make_run, postrun_reply):
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    post = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder(fail_times=9))
    assert (await post.run(run_id)).status == "paused"
    assert steps(conn)[("update", "a", "")] == "done"
    assert conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0] == 3
    later = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder())
    assert (await later.run(run_id)).status == "done"
    assert conn.execute("SELECT COUNT(*) FROM methods WHERE embedding IS NULL").fetchone()[0] == 0


async def test_update_can_make_one_proposal_and_sees_current_norms(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(proposal={"kind": "norm", "text": "Cite ids."})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    [p] = proposals(conn, run_id)
    assert (p.author, p.kind, p.text) == ("a", "norm", "Cite ids.")
    update = next(m[0]["content"] for _, m, _ in clients["b"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "## Community norms\n\n(none adopted yet)" in update
    assert '"proposal"' in update


async def test_an_amendment_must_target_an_existing_norm(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        proposal={"kind": "amendment", "target": 7, "text": "x"})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.failed == 1 and proposals(conn, run_id) == []
    assert steps(conn)[("update", "a", "")] == "failed"


async def test_a_dimension_must_not_reuse_a_name(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('old', 'a', 'dimension', 'Care', 'Q', 'adopted', 't')")
    conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES ('Care', 'Q', 1, 'old')")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        proposal={"kind": "dimension", "name": "care", "question": "Again?"})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert proposals(conn, run_id) == []


def proposing(postrun_reply, kind="norm", **fields):
    proposal = {"kind": kind, **fields}
    return FakeModel(responder(postrun_reply, update=update_reply(proposal=proposal)))


async def test_a_critiqued_proposal_with_quorum_is_adopted_into_norms_md(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import critiques_of, proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = proposing(postrun_reply, text="Cite entry ids.")
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    [p] = proposals(conn, run_id)
    assert p.status == "adopted"
    assert [author for author, _ in critiques_of(conn, p.id)] == ["a", "b", "c"]
    assert (store.root / "norms.md").read_text() == "1. Cite entry ids.\n"
    vote = next(m[0]["content"] for _, m, _ in clients["b"].calls if m[0]["content"].startswith("# Post-run: norms_vote"))
    assert f"Proposal {p.id} by Alpha (a): a new norm" in vote and "critique by Cyd (c): Consider edge cases." in vote
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert "norms_decided" in names


async def test_a_proposal_nobody_else_critiques_is_rejected(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    no_critique = lambda request: json.dumps({"critiques": []})
    clients = {a: FakeModel(responder(postrun_reply, norms_critique=no_critique)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, norms_critique=no_critique,
                                       update=update_reply(proposal={"kind": "norm", "text": "Unchallenged."})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert [p.status for p in proposals(conn, run_id)] == ["rejected"]
    assert not (store.root / "norms.md").exists()


async def test_no_proposals_means_no_deliberation_calls(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    asked = [m[0]["content"].split("\n")[0] for c in clients.values() for _, m, _ in c.calls]
    assert not any("norms_" in line for line in asked)


async def test_rerun_after_the_decision_does_not_vote_again(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = proposing(postrun_reply, text="Cite entry ids.")
    await cycle(store, conn, tmp_path, clients).run(run_id)
    again = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, again).run(run_id)
    assert [len(again[a].calls) for a in "abc"] == [0, 0, 0]
    assert (store.root / "norms.md").read_text() == "1. Cite entry ids.\n"
    assert conn.execute("SELECT COUNT(*) FROM norms").fetchone()[0] == 1


async def test_critiques_must_name_open_proposals_and_votes_cover_each(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    bad_critique = lambda request: json.dumps({"critiques": [{"proposal_id": 999, "points": "?"}]})
    partial_vote = lambda request: json.dumps({"votes": []})
    clients = {a: FakeModel(responder(postrun_reply)) for a in "c"}
    clients["b"] = FakeModel(responder(postrun_reply, norms_critique=bad_critique, norms_vote=partial_vote))
    clients["a"] = proposing(postrun_reply, text="X.")
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert steps(conn)[("norms_critique", "b", "")] == "failed"
    assert steps(conn)[("norms_vote", "b", "")] == "failed"
    assert result.failed == 2
