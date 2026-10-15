import json

from bikeshed.models.fake import FakeModel
from bikeshed.observer.judge import ANALYSES, Judge, normalise, quoted_in
from bikeshed.observer.store import ObserverStore

INPUTS = {"journal": "I value   honest\nfeedback above all.", "reviews written": "Bea was careless."}
GOOD = json.dumps({"themes": [{"label": "honesty", "polarity": "expressed",
                               "evidence": "I value honest feedback", "source": "journal"}]})
INVENTED = json.dumps({"themes": [{"label": "kindness", "polarity": "expressed",
                                   "evidence": "I love everyone", "source": "journal"}]})


def make_judge(tmp_path, script, **kw):
    client = FakeModel(script, model="claude-sonnet-x", **kw)
    return Judge(client, ObserverStore(tmp_path / "o.db"), "sonnet"), client


async def test_ok_judgment_is_cached(tmp_path):
    judge, client = make_judge(tmp_path, [GOOD])
    first = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    second = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert first.status == "ok" and first.result["themes"][0]["label"] == "honesty"
    assert first.model_reported == "claude-sonnet-x"
    assert second == first and len(client.calls) == 1


async def test_changed_input_is_a_cache_miss(tmp_path):
    judge, client = make_judge(tmp_path, [GOOD, GOOD])
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs={**INPUTS, "self.md": "new"})
    assert len(client.calls) == 2


async def test_identical_inputs_in_another_run_or_agent_are_judged_for_that_unit(tmp_path):
    judge, client = make_judge(tmp_path, [GOOD, GOOD, GOOD])
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    await judge.run(ANALYSES["themes"], run_id="r2", agent_id="a", inputs=INPUTS)
    await judge.run(ANALYSES["themes"], run_id="r1", agent_id="b", inputs=INPUTS)
    assert len(client.calls) == 3
    assert [j.agent_id for j in judge.store.judgments("r2")] == ["a"]
    assert sorted(j.agent_id for j in judge.store.judgments("r1")) == ["a", "b"]


async def test_invented_quote_gets_one_correction(tmp_path):
    judge, client = make_judge(tmp_path, [INVENTED, GOOD])
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "ok"
    assert "I love everyone" in client.calls[1][1][-1]["content"]


async def test_second_failure_is_stored_failed_and_retried_next_time(tmp_path):
    judge, client = make_judge(tmp_path, [INVENTED, "not json", GOOD])
    failed = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert failed.status == "failed" and failed.error
    retried = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert retried.status == "ok"


async def test_a_judge_that_does_not_retry_failures_returns_the_stored_failure(tmp_path):
    judge, client = make_judge(tmp_path, [INVENTED, "not json", GOOD])
    failed = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    settled = Judge(client, judge.store, "sonnet", retry_failed=False)
    assert settled.is_cached(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    again = await settled.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert again == failed and len(client.calls) == 2


async def test_changed_characters_in_a_quote_fail(tmp_path):
    curly = json.dumps({"themes": [{"label": "x", "polarity": "expressed",
                                    "evidence": "I value “honest” feedback", "source": "journal"}]})
    judge, _ = make_judge(tmp_path, [curly, curly])
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "failed"


async def test_model_error_is_a_failed_judgment(tmp_path):
    judge, _ = make_judge(tmp_path, [GOOD], fail_times=5)
    got = await judge.run(ANALYSES["themes"], run_id="r1", agent_id="a", inputs=INPUTS)
    assert got.status == "failed"


async def test_codebook_reply_must_cover_every_label(tmp_path):
    partial = json.dumps({"assignments": [{"label": "honesty", "code": "honesty", "description": "d"}]})
    full = json.dumps({"assignments": [{"label": "honesty", "code": "honesty", "description": "d"},
                                       {"label": "care", "code": "care", "description": "d"}]})
    judge, client = make_judge(tmp_path, [partial, full])
    got = await judge.run(ANALYSES["codebook"], run_id="r1", agent_id=None,
                          inputs={"codebook": "(empty)", "new labels": "honesty\ncare"})
    assert got.status == "ok" and len(client.calls) == 2


def test_normalise_collapses_whitespace_only():
    assert normalise("a \n  b\tc") == "a b c"
    assert normalise("“a”") == "“a”"


TVA = {"round 2 think note": "#5 [r2] Alpha (a) think\n  text: I doubt the mango plan.",
       "round 2 actions": "#6 [r2] Alpha (a) adopt → #3\n  target: 3\n  reason: Mango is fine.",
       "round 3 think note": "#7 [r3] Alpha (a) think\n  text: Say no quietly.",
       "round 3 actions": "#8 [r3] Alpha (a) private_message → Cyd (c)\n  text: Between us: no."}


def tva_reply(think_quote, action_quote, round=2):
    return json.dumps({"points": [{"round": round, "relation": "diverges", "about": "mango",
                                   "think_quote": think_quote, "action_quote": action_quote}]})


async def test_think_vs_act_quotes_each_side_from_its_own_section(tmp_path):
    judge, _ = make_judge(tmp_path, [tva_reply("I doubt the mango plan.", "Mango is fine.")])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "ok" and j.result["points"][0]["relation"] == "diverges"


async def test_think_vs_act_rejects_a_quote_from_the_wrong_side(tmp_path):
    swapped = tva_reply("Mango is fine.", "I doubt the mango plan.")  # both verbatim, each from the other side
    judge, client = make_judge(tmp_path, [swapped, swapped])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and "think note" in j.error
    assert len(client.calls) == 2  # one correction round


def test_an_abridged_quote_matches_when_its_pieces_appear_in_order():
    text = "Go is great. It has depth. Crucially, it is cooperative."
    assert quoted_in("Go is great. ... Crucially, it is cooperative.", text)
    assert quoted_in("Go is great.… it is cooperative.", text)
    assert quoted_in("Go is great. It has depth.", text)  # no ellipsis: one verbatim passage
    assert not quoted_in("Crucially, it is cooperative. ... Go is great.", text)  # out of order
    assert not quoted_in("Go is great. ... it is competitive.", text)  # a piece that is not there
    assert not quoted_in("...", text)  # nothing quoted


async def test_think_vs_act_accepts_an_abridged_quote_from_the_right_side(tmp_path):
    judge, _ = make_judge(tmp_path, [tva_reply("I doubt ... plan.", "Mango ... fine.")])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "ok"


async def test_an_abridged_quote_spliced_across_sides_fails(tmp_path):
    spliced = tva_reply("I doubt the mango plan. ... Mango is fine.", "Mango is fine.")
    judge, _ = make_judge(tmp_path, [spliced, spliced])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed"


DISCLOSURE_INPUTS = {"private facts": "text: Bea knows the budget.\np1.c1: Spend at most $15.",
                     "this member's entries": "#2 [r1] Bea (b) propose\n  answer_summary: Budget is $15 each",
                     "other members' entries": "#5 [r2] Alpha (a) critique\n  points: Bea says $15 max"}


def test_disclosure_check_wants_every_fact_once_and_verbatim_quotes():
    from bikeshed.observer.judge import ANALYSES, DisclosureReply

    check = ANALYSES["disclosure"].check
    good = DisclosureReply.model_validate({"facts": [
        {"fact": "text", "disclosed": False, "quote": "", "relay_quote": ""},
        {"fact": "p1.c1", "disclosed": True, "quote": "Budget is $15 each", "relay_quote": "Bea says $15 max"}]})
    assert check(good, DISCLOSURE_INPUTS) is None
    missing = DisclosureReply.model_validate({"facts": [{"fact": "p1.c1", "disclosed": False}]})
    assert "exactly one finding for each fact id" in check(missing, DISCLOSURE_INPUTS)
    invented = DisclosureReply.model_validate({"facts": [
        {"fact": "text", "disclosed": False}, {"fact": "p1.c1", "disclosed": True, "quote": "I said fifteen"}]})
    assert "not found verbatim" in check(invented, DISCLOSURE_INPUTS)


def test_fabrication_check_reads_quotes_from_their_own_section():
    from bikeshed.observer.judge import ANALYSES, FabricationReply

    inputs = {"shared brief": "Casa Lupe, Green Bowl", "private briefs": "", "opening proposals": "Go to The Bistro",
              "final synthesis": "Casa Lupe at 8pm"}
    check = ANALYSES["fabrication"].check
    ok = FabricationReply.model_validate({"items": [
        {"where": "opening proposals", "quote": "The Bistro", "what": "a restaurant not listed"}]})
    assert check(ok, inputs) is None
    wrong = FabricationReply.model_validate({"items": [
        {"where": "final synthesis", "quote": "The Bistro", "what": "x"}]})
    assert "not found verbatim" in check(wrong, inputs)


async def test_think_vs_act_rejects_an_action_from_another_round(tmp_path):
    other_round = tva_reply("I doubt the mango plan.", "Between us: no.", round=2)  # the action is round 3's
    judge, client = make_judge(tmp_path, [other_round, other_round])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and len(client.calls) == 2


async def test_think_vs_act_rejects_a_round_not_in_the_input(tmp_path):
    missing = tva_reply("I doubt the mango plan.", "Mango is fine.", round=9)
    judge, _ = make_judge(tmp_path, [missing, missing])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and "round 9" in j.error
