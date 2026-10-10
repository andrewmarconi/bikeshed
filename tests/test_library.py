import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.library import embed_missing, reception_of, record_methods, render_methods, retrieve
from aiswarm.models.fake import FakeEmbedder

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get


def board_run(conn, make_run, run_id, method_a="rotate chores weekly by burden score"):
    make_run(conn, run_id, ("a", "b", "c"))
    board = Board(conn, run_id)
    board.append(0, SYSTEM, "goal", {"goal": "G"})
    a = board.append(1, "a", "propose", {"answer_summary": "S", "method": method_a})
    b = board.append(1, "b", "propose", {"answer_summary": "T", "method": "draw lots for every task"})
    board.append(2, "b", "critique", {"target": a.id, "about": "method", "points": "p"}, refs=[a.id])
    r = board.append(2, "c", "refine", {"target": a.id, "change": "add a swap market", "reason": "r"}, refs=[a.id])
    board.append(3, "c", "adopt", {"target": a.id, "reason": "r"}, refs=[a.id])
    board.append(3, "b", "question", {"target": a.id, "text": "?"}, refs=[a.id])
    call = board.append(4, "c", "call_consensus", {"synthesis": "s", "refs": [a.id], "credits": []}, refs=[a.id])
    board.append(4, SYSTEM, "run_closed", {"reason": "consensus", "call_id": call.id})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = ?", (run_id,))
    return board.entries(), a, b, r


def test_reception_counts_what_others_did_with_an_entry(community, make_run):
    store, conn = community
    entries, a, b, r = board_run(conn, make_run, "r1")
    rec = reception_of(a, entries)
    assert (rec.adopted_by, rec.critiques, rec.refines, rec.questions, rec.in_winning) == (["c"], 1, 1, 1, True)
    assert rec.label(WHO) == "adopted by Cyd (c) · critiqued 1× · refined 1× · questioned 1× · cited by the winning call"
    assert reception_of(b, entries).label(WHO) == "adopted by no one · critiqued 0× · refined 0× · questioned 0×"


def test_record_methods_is_idempotent_and_includes_refines(community, make_run):
    store, conn = community
    entries, a, b, r = board_run(conn, make_run, "r1")
    assert record_methods(conn, "r1", "G", entries) == 3
    assert record_methods(conn, "r1", "G", entries) == 3
    rows = conn.execute("SELECT entry_id, kind, text FROM methods ORDER BY entry_id").fetchall()
    assert [(row["entry_id"], row["kind"], row["text"]) for row in rows] == [
        (a.id, "propose", "rotate chores weekly by burden score"),
        (b.id, "propose", "draw lots for every task"),
        (r.id, "refine", "add a swap market"),
    ]


async def test_embed_and_retrieve_by_similarity_excluding_the_current_run(community, make_run):
    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1")
    record_methods(conn, "r1", "G", entries)
    embedder = FakeEmbedder()
    assert await embed_missing(conn, embedder) == 3
    assert await embed_missing(conn, embedder) == 0
    found = await retrieve(conn, embedder, "how to rotate chores by burden", top_k=2)
    assert [m.text for m, _ in found][0] == "rotate chores weekly by burden score"
    assert len(found) == 2 and found[0][1] >= found[1][1]
    assert await retrieve(conn, embedder, "anything", top_k=2, exclude_run="r1") == []


async def test_retrieve_makes_no_call_when_nothing_to_search(community):
    store, conn = community
    embedder = FakeEmbedder(fail_times=5)
    assert await retrieve(conn, embedder, "goal", top_k=3) == []
    assert await retrieve(conn, embedder, "goal", top_k=0) == []
    assert embedder.calls == []


async def test_render_methods(community, make_run):
    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1", method_a="m" * 700)
    record_methods(conn, "r1", "G", entries)
    await embed_missing(conn, FakeEmbedder())
    [(method, _)] = await retrieve(conn, FakeEmbedder(), "m" * 700, top_k=1)
    [line] = render_methods([method], WHO, max_chars=100)
    assert line.startswith(f"- #{method.entry_id} in run r1 by Alpha (a) (propose); goal: G\n  method: " + "m" * 100 + "…")
    assert "\n  reception: adopted by Cyd (c)" in line


class _Picky(FakeEmbedder):
    """Rejects any request containing a poisoned text, like a server refusing one over-long input."""

    async def embed(self, texts):
        if any("POISON" in t for t in texts):
            self.calls.append(list(texts))
            from aiswarm.models.base import ModelError
            raise ModelError("400: input too long")
        return await super().embed(texts)


async def test_one_bad_row_does_not_block_the_rest(community, make_run):
    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1", method_a="POISON method")
    record_methods(conn, "r1", "G", entries)
    assert await embed_missing(conn, _Picky()) == 2
    assert conn.execute("SELECT COUNT(*) FROM methods WHERE embedding IS NULL").fetchone()[0] == 1


async def test_a_total_outage_raises_so_it_can_be_recorded(community, make_run):
    from aiswarm.models.base import ModelError

    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1")
    record_methods(conn, "r1", "G", entries)
    with pytest.raises(ModelError):
        await embed_missing(conn, FakeEmbedder(fail_times=99))
