from datetime import UTC, datetime, timedelta

import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.journal import write_journal
from aiswarm.reviews import add_response, add_review
from aiswarm.timeline import resolve_agent, timeline


def iso(delta_hours):
    return (datetime.now(UTC) + timedelta(hours=delta_hours)).isoformat(timespec="seconds")


def add_run(conn, run_id, goal, created_at):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'closed', '[\"a\",\"b\"]', ?)",
                 (run_id, goal, created_at))


@pytest.fixture
def history(community):
    """Profile commits happen now (before any run); r1 starts in 1h, r2 in 2h."""
    store, conn = community
    store.set_public("a", "Ari", "Alpha headline", "I am Alpha.")  # rename Alpha -> Ari, before any run
    add_run(conn, "r1", "Best fruit?", iso(1))
    add_run(conn, "r2", "Best pizza?", iso(2))
    write_journal(conn, store, "a", "r1", "before", "JOURNAL-TEXT")
    board = Board(conn, "r1")
    proposal = board.append(1, "a", "propose", {"answer_summary": "Mango", "method": "Taste"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, 'r1', 'a', 'm', ?)",
                 (proposal.id, "Because " + "mango " * 100))
    call = board.append(2, "a", "call_consensus", {"synthesis": "Mango", "refs": [proposal.id],
                                                   "credits": [{"agent_id": "b", "contribution": "tasting"}]})
    board.append(2, SYSTEM, "run_closed", {"reason": "consensus", "call_id": call.id})
    review = add_review(conn, run_id="r1", reviewer="b", reviewee="a", more_of="More.", less_of="Less.",
                        private_note="PRIVATE-NOTE", evidence=[proposal.id], overall=4, overall_reason="Good.")
    add_response(conn, review.id, "accept", "Fair.")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, text, created_at) VALUES ('r2', 'a', 'norm', "
                 "'Cite your sources.', ?)", (iso(2),))
    return store, conn


def test_resolve_agent_by_id_name_and_former_name(history):
    store, conn = history
    assert resolve_agent(store, "a") == "a"
    assert resolve_agent(store, "ARI") == "a"
    assert resolve_agent(store, "alpha") == "a"
    assert resolve_agent(store, "nobody") is None


def test_timeline_groups_everything_under_runs(history):
    store, conn = history
    text = timeline(conn, store, "a")
    before, rest = text.split("## r1 — Best fruit?")
    r1, r2 = rest.split("## r2 — Best pizza?")
    assert text.startswith("# Timeline: Ari (a)\nformerly: Alpha")
    assert "## Before any run" in before and "create agent a" in before and "renamed Alpha → Ari" in before
    assert "JOURNAL-TEXT" in r1
    assert "review received from Bea (b)" in r1 and "PRIVATE-NOTE" in r1 and "response: accept" in r1
    assert "credits given in the closing call" in r1 and "- Bea (b): tasting" in r1
    assert "reasoning behind #" in r1 and "…" in r1
    assert "proposed a norm" in r2 and "Cite your sources." in r2


def test_timeline_shows_credits_and_reviews_from_the_other_side(history):
    store, conn = history
    text = timeline(conn, store, "b")
    assert "credited by Ari (a)" in text and "tasting" in text
    assert "review given to Ari (a)" in text


def test_since_drops_earlier_runs(history):
    store, conn = history
    text = timeline(conn, store, "a", since="r2")
    assert "## r2" in text and "## r1" not in text and "Before any run" not in text
    with pytest.raises(KeyError):
        timeline(conn, store, "a", since="missing")


def test_timeline_of_an_agent_with_no_runs(community):
    store, conn = community
    text = timeline(conn, store, "c")
    assert "## Before any run" in text and "create agent c" in text
