from bikeshed.board import Entry
from bikeshed.config import RunSettings
from bikeshed.consensus import StopDecision, call_status, spent_calls, stop_decision

AGENTS = ["a", "b", "c"]


def e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else []
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


def base():
    return [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="A", method="M"),
        e(3, 1, "b", "propose", answer_summary="B", method="M"),
        e(4, 1, "c", "propose", answer_summary="C", method="M"),
        e(5, 2, "a", "call_consensus", synthesis="S", refs=[], credits=[]),
    ]


def test_passes_with_quorum_and_a_critique():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    status = call_status(entries, 5)
    assert status.supporters == {"a", "c"} and status.critiqued
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("consensus", 5)


def test_unchallenged_agreement_does_not_pass():
    entries = base() + [
        e(6, 3, "b", "vote", target=5, stance="support", reason="r"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_callers_own_critique_does_not_count():
    entries = base() + [
        e(6, 3, "a", "critique", target=5, about="answer", points="self"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert not call_status(entries, 5).critiqued


def test_latest_vote_wins():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
        e(8, 4, "c", "vote", target=5, stance="object", reason="changed mind"),
    ]
    status = call_status(entries, 5)
    assert status.objectors == {"c"}
    assert stop_decision(entries, AGENTS, 4, RunSettings()) is None


def test_votes_on_other_calls_do_not_count():
    entries = base() + [
        e(6, 3, "b", "call_consensus", synthesis="T", refs=[], credits=[]),
        e(7, 3, "c", "vote", target=6, stance="support", reason="r"),
    ]
    assert call_status(entries, 5).supporters == {"a"}


def test_stagnation_after_consecutive_all_pass_rounds():
    entries = base()[:4] + [e(5, 2, "a", "pass"), e(6, 2, "b", "pass"), e(7, 2, "c", "pass")] + [
        e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("stagnation")


def test_one_active_member_prevents_stagnation():
    entries = base()[:4] + [e(5, 2, "a", "pass"), e(6, 2, "b", "pass"), e(7, 2, "c", "question", target=2, text="?")] + [
        e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_budget_at_max_rounds_and_consensus_takes_precedence():
    assert stop_decision(base(), AGENTS, 12, RunSettings()) == StopDecision("budget")
    entries = base() + [
        e(6, 12, "b", "critique", target=5, about="answer", points="p"),
        e(7, 12, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert stop_decision(entries, AGENTS, 12, RunSettings()) == StopDecision("consensus", 5)


def test_readopting_the_same_entry_counts_as_idle():
    entries = base()[:4] + [e(5, 2, "a", "adopt", target=2, reason="r"), e(6, 2, "b", "pass"), e(7, 2, "c", "pass")] + [
        e(8, 3, "a", "adopt", target=2, reason="again"), e(9, 3, "b", "pass"), e(10, 3, "c", "pass"),
        e(11, 4, "a", "adopt", target=2, reason="again"), e(12, 4, "b", "pass"), e(13, 4, "c", "pass"),
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None  # round 2's first adopt was progress
    assert stop_decision(entries, AGENTS, 4, RunSettings()) == StopDecision("stagnation")


def test_adopting_a_different_entry_is_progress():
    entries = base()[:4] + [e(5, 2, "a", "adopt", target=2, reason="r"), e(6, 2, "b", "pass"), e(7, 2, "c", "pass")] + [
        e(8, 3, "a", "adopt", target=3, reason="changed mind"), e(9, 3, "b", "pass"), e(10, 3, "c", "pass"),
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_a_call_the_scorer_rejected_is_spent():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
        e(8, 3, "system", "scorer_rejected", call_id=5, feedback="No answer."),
    ]
    assert spent_calls(entries) == {5}
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None
    later = entries + [e(9, 4, "c", "vote", target=5, stance="support", reason="again")]
    assert stop_decision(later, AGENTS, 4, RunSettings()) is None  # votes on a spent call count for nothing


def test_pre_action_messages_do_not_prevent_stagnation():
    dm = lambda id, rnd: e(id, rnd, "a", "private_message", to="b", text="psst", pre_action=True)
    entries = base()[:4] + [dm(5, 2), e(6, 2, "a", "pass"), e(7, 2, "b", "pass"), e(8, 2, "c", "pass"),
                            dm(9, 3), e(10, 3, "a", "pass"), e(11, 3, "b", "pass"), e(12, 3, "c", "pass")]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("stagnation")


def test_a_message_taken_as_the_action_is_still_progress():
    entries = base()[:4] + [e(5, 2, "a", "private_message", to="b", text="psst"), e(6, 2, "b", "pass"),
                            e(7, 2, "c", "pass")] + [e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_stood_round_rule_closes_without_a_critique_once_the_call_has_stood_a_round():
    entries = base() + [
        e(6, 2, "b", "vote", target=5, stance="support", reason="r"),
        e(7, 2, "c", "vote", target=5, stance="support", reason="r"),
    ]
    settings = RunSettings(close_rule="stood_round")
    assert stop_decision(entries, AGENTS, 2, settings) is None  # made this round: not yet
    assert stop_decision(entries, AGENTS, 3, settings) == StopDecision("consensus", 5)


def test_stood_round_rule_still_needs_the_quorum():
    entries = base() + [e(6, 2, "b", "vote", target=5, stance="support", reason="r")]
    assert stop_decision(entries, ["a", "b", "c", "d"], 3, RunSettings(close_rule="stood_round")) is None


def test_the_critique_rule_is_the_default():
    assert RunSettings().close_rule == "critique"
