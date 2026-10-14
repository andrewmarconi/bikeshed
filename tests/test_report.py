from bikeshed.board import SYSTEM, Entry
from bikeshed.consensus import StopDecision
from bikeshed.report import account, write_report


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


def _e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else list(payload.get("refs", []))
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", SYSTEM: "system"}.get


def test_account_lists_key_moments_in_agents_own_words():
    entries = [
        _e(1, 0, SYSTEM, "goal", goal="G"),
        _e(2, 1, "a", "propose", answer_summary="RED-SUMMARY", method="RED-METHOD"),
        _e(3, 1, "b", "propose", answer_summary="BLUE", method="M"),
        _e(4, 2, "b", "critique", target=2, about="method", points="TOO-VAGUE"),
        _e(5, 3, "a", "refine", target=2, change="SHARPER", reason="r"),
        _e(6, 3, "c", "adopt", target=2, reason="PERSUADED"),
        _e(7, 4, "c", "adopt", target=2, reason="AGAIN"),
        _e(8, 4, "a", "call_consensus", synthesis="SYNTH", refs=[2], credits=[]),
        _e(9, 5, "b", "vote", target=8, stance="object", reason="NOT-YET"),
        _e(10, 5, SYSTEM, "run_closed", reason="budget", call_id=None),
    ]
    text = account("Pick a colour", entries, StopDecision("budget"), WHO)
    assert text.startswith("# Account\n\n**Goal:** Pick a colour\n\n**Ended:** budget after 5 rounds\n")
    assert '- #2 Alpha (a): "RED-SUMMARY" — method: "RED-METHOD"' in text
    assert '- #4 Bea (b) critiqued #2 (Alpha (a)): "TOO-VAGUE" → #5 Alpha (a) refined it: "SHARPER"' in text
    assert '- r3: Cyd (c) adopted #2 (Alpha (a)): "PERSUADED"' in text and "AGAIN" not in text
    assert '- #8 by Alpha (a) in r4: "SYNTH"' in text and "critiqued: no" in text
    assert '  - r5 Bea (b) object: "NOT-YET"' in text


def test_account_handles_a_quiet_run_and_long_quotes():
    entries = [_e(1, 0, SYSTEM, "goal", goal="G"), _e(2, 1, "a", "propose", answer_summary="x" * 400, method="m")]
    text = account("G", entries, StopDecision("stagnation"), WHO)
    assert "**Ended:** stagnation after 1 round\n" in text
    assert '"' + "x" * 300 + '…"' in text
    assert text.count("_None._") == 3


def test_write_report_writes_account(tmp_path):
    entries = [_e(1, 0, SYSTEM, "goal", goal="G"), _e(2, 1, "a", "propose", answer_summary="S", method="M")]
    write_report(tmp_path, "G", entries, StopDecision("budget"), WHO)
    assert (tmp_path / "report" / "account.md").read_text().startswith("# Account")


def test_a_budget_end_after_a_rejected_call_says_the_scorer_accepted_none(tmp_path):
    entries = [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="Blue", method="Gut"),
        e(3, 2, "a", "call_consensus", synthesis="Poll first", refs=[], credits=[]),
        e(4, 2, "system", "scorer_rejected", call_id=3, feedback="No colour named."),
    ]
    write_report(tmp_path, "G", entries, StopDecision("budget"), who)
    minority = read(tmp_path, "minority.md")
    assert "_The community reached a consensus, but the Scorer accepted no call._" in minority
    assert "No consensus was reached" not in minority


def test_write_report_writes_the_briefs_only_when_given(tmp_path):
    write_report(tmp_path / "with", "G", [], StopDecision("budget"), lambda a: a, briefs="## The briefs this run\nX")
    write_report(tmp_path / "without", "G", [], StopDecision("budget"), lambda a: a)
    assert (tmp_path / "with" / "report" / "briefs.md").read_text() == "## The briefs this run\nX\n"
    assert not (tmp_path / "without" / "report" / "briefs.md").exists()
