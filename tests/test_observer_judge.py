import json

from bikeshed.models.fake import FakeModel
from bikeshed.observer.judge import ANALYSES, Judge, normalise
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


TVA = {"think notes": "#5 [r2] Alpha (a) think\n  text: I doubt the mango plan.",
       "public actions": "#6 [r2] Alpha (a) adopt → #3\n  target: 3\n  reason: Mango is fine.",
       "private messages sent": "(none)"}


def tva_reply(think_quote, action_quote):
    return json.dumps({"points": [{"relation": "diverges", "about": "mango", "think_quote": think_quote,
                                   "action_quote": action_quote}]})


async def test_think_vs_act_quotes_each_side_from_its_own_section(tmp_path):
    judge, _ = make_judge(tmp_path, [tva_reply("I doubt the mango plan.", "Mango is fine.")])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "ok" and j.result["points"][0]["relation"] == "diverges"


async def test_think_vs_act_rejects_a_quote_from_the_wrong_side(tmp_path):
    swapped = tva_reply("Mango is fine.", "I doubt the mango plan.")  # both verbatim, each from the other side
    judge, client = make_judge(tmp_path, [swapped, swapped])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and "think notes" in j.error
    assert len(client.calls) == 2  # one correction round
