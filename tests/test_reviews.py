import pytest

from aiswarm.reviews import (
    Reputation, add_response, add_review, render_public_reviews, reputation, responses_for, reviews_of,
)

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get


def closed_run(conn, run_id, created_at):
    conn.execute(
        "INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at) "
        "VALUES (?, 'g', 'closed', 'budget', '[\"a\", \"b\", \"c\"]', ?)",
        (run_id, created_at),
    )


def review(conn, run_id, reviewer, reviewee, overall=4, note="NOTE"):
    return add_review(
        conn, run_id=run_id, reviewer=reviewer, reviewee=reviewee, more_of="MORE", less_of="LESS",
        private_note=note, evidence=[2, 3], overall=overall, overall_reason="REASON",
    )


def test_add_review_round_trips_and_marks_self(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    peer = review(conn, "r1", "b", "a")
    own = review(conn, "r1", "a", "a")
    assert not peer.is_self and own.is_self
    assert reviews_of(conn, "a") == [peer]
    assert reviews_of(conn, "a", include_self=True) == [own, peer]
    assert reviews_of(conn, "a", run_id="other") == []
    assert peer.evidence == [2, 3]


def test_add_review_twice_replaces_instead_of_duplicating(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    first = review(conn, "r1", "b", "a", overall=2)
    second = review(conn, "r1", "b", "a", overall=5)
    assert first.id == second.id
    assert [r.overall for r in reviews_of(conn, "a")] == [5]


def test_responses_upsert(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    r = review(conn, "r1", "b", "a")
    add_response(conn, r.id, "dispute", "No.")
    add_response(conn, r.id, "accept", "Fair.")
    assert responses_for(conn, [r.id])[r.id].stance == "accept"
    assert responses_for(conn, []) == {}


def test_reputation_is_recency_weighted_and_excludes_self(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    closed_run(conn, "r2", "2026-01-02T00:00:00+00:00")
    review(conn, "r1", "b", "a", overall=5)   # one closed run since → weight 0.5 at half-life 1
    review(conn, "r2", "c", "a", overall=1)   # newest → weight 1
    review(conn, "r2", "a", "a", overall=5)   # self → excluded
    rep = reputation(conn, "a", half_life=1)
    assert rep.count == 2
    assert rep.score == pytest.approx((5 * 0.5 + 1 * 1) / 1.5)
    assert reputation(conn, "b", half_life=1) == Reputation(None, 0)


def test_reputation_labels():
    assert Reputation(None, 0).label() == "no reviews yet"
    assert Reputation(4.25, 12).label() == "★4.2 (12 reviews)"
    assert Reputation(3.0, 1).label() == "★3.0 (1 review)"


def test_public_reviews_hide_private_notes_and_self_reviews(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    peer = review(conn, "r1", "b", "a", note="SECRET-NOTE")
    review(conn, "r1", "a", "a", note="SELF-NOTE")
    add_response(conn, peer.id, "accept", "Fair point.")
    text = render_public_reviews(conn, "a", WHO)
    assert f"Review {peer.id} of Alpha (a) by Bea (b) after run r1" in text
    assert "more of: MORE" in text and "evidence: #2, #3" in text and "overall: 4/5 — REASON" in text
    assert "response: accept — Fair point." in text
    assert "SECRET-NOTE" not in text and "SELF-NOTE" not in text and "Self-review" not in text
    assert render_public_reviews(conn, "c", WHO) == "No reviews of Cyd (c) yet."


def test_public_reviews_truncate(community):
    store, conn = community
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    review(conn, "r1", "b", "a")
    text = render_public_reviews(conn, "a", WHO, max_chars=40)
    assert text.startswith("Review") and "[truncated:" in text


def adopt_dimension(conn, name="Care", run_id="r0"):
    closed_run(conn, run_id, "2025-12-31T00:00:00+00:00")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES (?, 'a', 'dimension', ?, 'Q?', 'adopted', 't')", (run_id, name))
    pid = conn.execute("SELECT MAX(id) FROM norm_proposals").fetchone()[0]
    return conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES (?, 'Q?', ?, ?) "
                        "RETURNING id", (name, pid, run_id)).fetchone()[0]


def test_dimension_ratings_store_render_and_feed_reputation(community):
    from aiswarm.reviews import dimension_reputations, ratings_for, render_review, set_ratings

    store, conn = community
    care = adopt_dimension(conn)
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    r = review(conn, "r1", "b", "a")
    set_ratings(conn, r.id, [(care, 2, "Rushed.")])
    set_ratings(conn, r.id, [(care, 5, "Careful.")])
    [rating] = ratings_for(conn, [r.id])[r.id]
    assert (rating.dimension, rating.rating, rating.reason) == ("Care", 5, "Careful.")
    assert "  Care: 5/5 — Careful." in render_review(r, WHO, private=False, ratings=[rating])
    assert "Care: 5/5" in render_public_reviews(conn, "a", WHO)
    [(name, rep)] = dimension_reputations(conn, "a", half_life=5)
    assert (name, rep.score, rep.count) == ("Care", 5.0, 1)
    assert dimension_reputations(conn, "b", half_life=5) == []
