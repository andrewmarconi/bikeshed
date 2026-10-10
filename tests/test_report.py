from aiswarm.board import Entry
from aiswarm.consensus import StopDecision
from aiswarm.report import write_report


def e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else []
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


def who(author):
    return {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}[author]


def read(tmp_path, name):
    return (tmp_path / "report" / name).read_text()


def test_consensus_report(tmp_path):
    entries = [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="Blue", method="Gut"),
        e(3, 2, "a", "call_consensus", synthesis="Blue it is", refs=[2],
          credits=[{"agent_id": "b", "contribution": "framing"}]),
        e(4, 3, "b", "critique", target=3, about="answer", points="p"),
        e(5, 3, "c", "vote", target=3, stance="object", reason="Green is better"),
    ]
    write_report(tmp_path, "Pick a colour", entries, StopDecision("consensus", 3), who)
    answer = read(tmp_path, "answer.md")
    assert "**Goal:** Pick a colour" in answer
    assert "Blue it is" in answer
    assert "- Bea (b): framing" in answer
    assert "- Cyd (c): Green is better" in read(tmp_path, "minority.md")


def test_consensus_with_no_credits_or_objections(tmp_path):
    entries = [e(1, 0, "system", "goal", goal="G"), e(2, 2, "a", "call_consensus", synthesis="S", refs=[], credits=[])]
    write_report(tmp_path, "G", entries, StopDecision("consensus", 2), who)
    assert "_The caller credited no one._" in read(tmp_path, "answer.md")
    assert "_No objections._" in read(tmp_path, "minority.md")


def test_no_consensus_lists_latest_proposal_per_member(tmp_path):
    entries = [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="Blue", method="Gut"),
        e(3, 1, "b", "propose", answer_summary="Red", method="Survey"),
        e(4, 2, "a", "propose", answer_summary="Teal", method="Revised"),
    ]
    write_report(tmp_path, "G", entries, StopDecision("budget"), who)
    answer = read(tmp_path, "answer.md")
    assert "## Competing positions" in answer
    assert "Teal" in answer and "Red" in answer and "Blue" not in answer
    assert "_No consensus was reached._" in read(tmp_path, "minority.md")
