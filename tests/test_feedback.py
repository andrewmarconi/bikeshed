from bikeshed.board import SYSTEM, Entry
from bikeshed.postrun.feedback import feedback_packet, interactions, transcript

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", "d": "Dee (d)", SYSTEM: "system"}.get
AGENTS = ["a", "b", "c", "d"]


def e(id, round, author, type, links=(), **payload):
    return Entry(id, "r1", round, author, type, payload, list(links), "all", "t")


def board_with_consensus():
    return [
        e(1, 0, SYSTEM, "goal", goal="Pick a colour"),
        e(2, 1, "a", "propose", answer_summary="Red", method="M"),
        e(3, 1, "b", "propose", answer_summary="Blue", method="M"),
        e(4, 2, "b", "critique", links=[2], target=2, about="method", points="P"),
        e(5, 2, "c", "adopt", links=[2], target=2, reason="R"),
        e(6, 3, "a", "call_consensus", links=[2, 3], synthesis="Purple", refs=[2, 3],
          credits=[{"agent_id": "b", "contribution": "the blue half"}]),
        e(7, 3, SYSTEM, "run_closed", reason="consensus", call_id=6),
    ]


def test_interactions_from_refs_either_way_and_shared_files():
    pairs = interactions(board_with_consensus(), [("plan.md", "c"), ("plan.md", "d"), ("solo.md", "a")], AGENTS)
    assert pairs["a"] == {"b", "c"}
    assert pairs["b"] == {"a"}
    assert pairs["c"] == {"a", "d"}
    assert pairs["d"] == {"c"}


def test_interactions_ignore_system_and_non_members():
    entries = [e(2, 1, "a", "propose"), e(3, 1, SYSTEM, "artifact_changed", links=[2]), e(4, 1, "zed", "critique", links=[2])]
    assert interactions(entries, [], ["a", "b"]) == {"a": set(), "b": set()}


def test_packet_lists_responses_credits_citation_and_outcome():
    packet = feedback_packet(
        "b", run_id="r1", goal="Pick a colour", entries=board_with_consensus(), who=WHO,
        answer="# Answer\nPURPLE-OUTCOME", failures=[(2, "model_error", "timed out")],
    )
    assert packet.startswith("Run r1\nGoal: Pick a colour\nThe run ended by consensus.")
    assert "#3 [r1] Bea (b) propose" in packet
    assert "  ← #6 Alpha (a) call_consensus" in packet
    assert "- from Alpha (a) in call #6 (the winning call): the blue half" in packet
    assert "Call #6 by Alpha (a) cited your entries: #3" in packet
    assert "PURPLE-OUTCOME" in packet
    assert "- round 2: model_error: timed out" in packet


def test_packet_for_a_run_without_consensus_or_posts():
    entries = [e(1, 0, SYSTEM, "goal", goal="G"), e(2, 1, "a", "propose", answer_summary="X", method="M"),
               e(3, 6, SYSTEM, "run_closed", reason="budget", call_id=None)]
    packet = feedback_packet("d", run_id="r1", goal="G", entries=entries, who=WHO, answer="", failures=[])
    assert "The run ended by budget." in packet
    assert "## What you posted\n(nothing)" in packet
    assert "## Credits you received\n(none)" in packet
    assert "The run ended without consensus." in packet
    assert "## The outcome\n(no report)" in packet
    assert "## Your failed steps\n(none)" in packet


def test_transcript_keeps_newest_entries_by_the_given_authors():
    entries = [e(i, 1, "a" if i % 2 else "b", "question", text="x" * 50) for i in range(2, 30)]
    text = transcript(entries, WHO, {"a"}, max_chars=400)
    assert text.startswith("[earlier entries omitted]\n")
    assert "#29 [r1] Alpha (a)" in text and "Bea (b)" not in text
    assert len(text) <= 400 + len("[earlier entries omitted]\n")
    assert transcript(entries, WHO, {"c"}, max_chars=400) == "(no entries)"
