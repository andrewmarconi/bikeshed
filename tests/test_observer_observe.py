import asyncio
import json

import pytest
from observer_helpers import add_entry, add_journal, add_review, add_run

from bikeshed.config import Config
from bikeshed.models.fake import FakeModel
from bikeshed.observer.observe import observe

FUTURE = "2099-01-01T00:00:00+00:00"  # after every fixture commit; git cannot parse far-future dates like 2999


def config(tmp_path):
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}, "observer": {"judge": "claude"}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    cfg.observer.dir = tmp_path / "observer"
    return cfg


def judge_reply(system, messages):
    text = messages[0]["content"]
    if "## think notes" in text:
        return json.dumps({"points": [{"relation": "diverges", "about": "pear", "think_quote": "Pear is weak.",
                                       "action_quote": "Pear is fine."}]})
    if "codebook" in system.lower() and "## new labels" in text:
        labels = text.split("## new labels\n\n")[1].split("\n\nReply")[0].splitlines()
        return json.dumps({"assignments": [{"label": label, "code": label, "description": "d"} for label in labels]})
    if "## journal" in text and "## reviews written" in text:
        if "Feeling sure." in text:
            return json.dumps({"themes": [{"label": "confidence", "polarity": "expressed",
                                           "evidence": "Feeling sure.", "source": "journal"}]})
        return json.dumps({"themes": [{"label": "quiet", "polarity": "expressed", "evidence": "(none)",
                                       "source": "journal"}]})
    if "## reviews given" in text:
        return json.dumps({"findings": []})
    if "## self.md now" in text:
        return json.dumps({"changes": [], "private_public_gap": "none", "gap_evidence": ""})
    return json.dumps({"trend": "first", "optimises_for": "x", "evidence": []})


@pytest.fixture
def lab(tmp_path, community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE, goal_id="best-fruit", category="unsettled")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango", "method": "m"})
    add_journal(conn, "r1", "a", "after", "Feeling sure.")
    add_review(conn, "r1", "a", "b", 4)
    add_run(conn, "r2", created=FUTURE, benchmark=True)
    add_entry(conn, "r2", "b", "propose", {"answer_summary": "Pepperoni", "method": "m"})
    add_run(conn, "r3", status="running", created=FUTURE)
    return config(tmp_path)


async def test_observe_writes_reports_summary_and_charts(lab):
    result = await observe(lab, judge_client=FakeModel(judge_reply))
    names = {p.name for p in result.written}
    assert {"r1.md", "r2.md", "summary.md"} <= names
    assert any(p.suffix == ".png" for p in result.written)
    assert result.in_progress == ["r3"] and result.failed == 0
    r1 = (lab.observer.dir / "runs" / "r1.md").read_text()
    assert "confidence" in r1 and "Feeling sure." in r1
    assert "Pepperoni" in (lab.observer.dir / "runs" / "r2.md").read_text()


async def test_second_observe_uses_the_cache(lab):
    client = FakeModel(judge_reply)
    await observe(lab, judge_client=client)
    calls = len(client.calls)
    await observe(lab, judge_client=client)
    assert len(client.calls) == calls


async def test_observe_without_retry_leaves_failed_judgments_alone(lab):
    first = await observe(lab, judge_client=FakeModel(judge_reply, fail_times=1000))
    assert first.failed > 0
    client = FakeModel(judge_reply)
    lines: list[str] = []
    result = await observe(lab, judge_client=client, retry_failed=False, progress=lines.append)
    assert result.failed == first.failed and client.calls == []
    assert any("failed, not retried" in line for line in lines)
    retried = await observe(lab, judge_client=client)
    assert retried.failed == 0 and client.calls


async def test_changing_the_judge_model_re_judges(lab):
    client = FakeModel(judge_reply)
    await observe(lab, judge_client=client)
    calls = len(client.calls)
    lab.models["claude"].model = "opus"
    await observe(lab, judge_client=client)
    assert len(client.calls) > calls


async def test_summary_compares_the_control_with_its_twin(lab, community):
    store, conn = community
    store.create("e", "fake", "Temperament of b.", control=True)
    add_run(conn, "r4", agents=("b", "e"), created=FUTURE, goal_id="dishwasher")
    add_review(conn, "r4", "b", "e", 2)
    add_entry(conn, "r4", "e", "vote", {"target": 1, "stance": "support", "reason": "r"})
    await observe(lab, judge_note="--no-judge")
    summary = (lab.observer.dir / "summary.md").read_text()
    section = summary.split("\n## Control vs twin\n")[1].split("\n## ")[0]
    assert "Bea (b)" in section
    assert "| best-fruit | Bea (b) | 4.0 |" in section
    assert "| dishwasher | e (e) | 2.0 |" in section
    assert (lab.observer.dir / "charts" / "control_e.png").exists()


async def test_observe_one_run_without_a_judge(lab):
    result = await observe(lab, run_id="r1", judge_note="--no-judge")
    assert [p.name for p in result.written] == ["r1.md"]
    assert "_not judged (--no-judge)_" in (lab.observer.dir / "runs" / "r1.md").read_text()


async def test_observe_unknown_run(lab):
    with pytest.raises(ValueError, match="no closed run"):
        await observe(lab, run_id="nope")


async def test_a_run_whose_post_run_has_not_finished_is_skipped(lab, community):
    store, conn = community
    conn.execute("INSERT INTO postrun_steps (run_id, step, agent_id, subject, status, detail, updated_at) "
                 "VALUES ('r1', 'journal', 'a', '', 'done', '', ?)", (FUTURE,))
    events = lab.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"event": "postrun_started", "run_id": "r1"}\n')
    result = await observe(lab, judge_note="--no-judge")
    assert "r1 (post-run)" in result.in_progress
    assert not (lab.observer.dir / "runs" / "r1.md").exists()
    with pytest.raises(ValueError, match="post-run"):
        await observe(lab, run_id="r1", judge_note="--no-judge")
    events.write_text(events.read_text() + '{"event": "postrun_finished", "failed": 0}\n')
    await observe(lab, judge_note="--no-judge")
    assert (lab.observer.dir / "runs" / "r1.md").exists()


async def test_observe_reports_a_process_2_run(lab, community):
    store, conn = community
    add_run(conn, "r5", created=FUTURE, goal_id="debate", process=2)
    add_entry(conn, "r5", "a", "think", {"text": "Pear is weak."}, round=1, visible_to=["a"])
    add_entry(conn, "r5", "a", "propose", {"answer_summary": "Pear", "method": "m"}, round=1)
    add_entry(conn, "r5", "a", "think", {"text": "Still unsure."}, round=2, visible_to=["a"])
    add_entry(conn, "r5", "a", "adopt", {"target": 2, "reason": "Pear is fine."}, refs=[2], round=2)
    add_entry(conn, "r5", "b", "private_message", {"to": "a", "text": "psst"}, round=2, visible_to=["b", "a"], to="a")
    result = await observe(lab, judge_client=FakeModel(judge_reply))
    report = (lab.observer.dir / "runs" / "r5.md").read_text()
    assert "- **Process:** 2" in report and "## Process 2" in report
    assert "![Private messages](../charts/network_r5.png)" in report
    assert (lab.observer.dir / "charts" / "network_r5.png") in result.written
    assert "think “Pear is weak.” / action “Pear is fine.”" in report
    assert "## Process 2" not in (lab.observer.dir / "runs" / "r1.md").read_text()
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "debate (P2)" in summary and "## Process 2" in summary
    add_run(conn, "r6", created=FUTURE, goal_id="tidy", process=3)
    add_entry(conn, "r6", "a", "think", {"text": "Tidy first."}, round=1, visible_to=["a"])
    add_entry(conn, "r6", "a", "propose", {"answer_summary": "Tidy", "method": "m"}, round=1)
    await observe(lab, judge_client=FakeModel(judge_reply))
    assert "## Process 3" in (lab.observer.dir / "runs" / "r6.md").read_text()
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "tidy (P3)" in summary and "## Process 3" in summary and "## Process 2" in summary


def _scored(conn):
    add_run(conn, "r5", created=FUTURE, ended="consensus")
    first = add_entry(conn, "r5", "a", "call_consensus",
                      {"synthesis": "Fruit", "credits": [{"agent_id": "a", "contribution": "me"}]})
    add_entry(conn, "r5", "system", "scorer_rejected", {"call_id": first, "feedback": "Which fruit?"}, refs=[first])
    second = add_entry(conn, "r5", "b", "call_consensus",
                       {"synthesis": "Kiwi", "credits": [{"agent_id": "c", "contribution": "kiwi"}]})
    add_entry(conn, "r5", "system", "scorer_accepted", {"call_id": second}, refs=[second])
    add_entry(conn, "r5", "system", "run_closed", {"reason": "consensus", "call_id": second})
    conn.execute("INSERT INTO scorer_points (run_id, agent_id, call_id, contribution, created_at) "
                 "VALUES ('r5', 'c', ?, 'kiwi', 't')", (second,))
    return first, second


async def test_observe_reports_the_scorer(lab, community):
    store, conn = community
    first, second = _scored(conn)
    result = await observe(lab, judge_client=None, judge_note="--no-judge")
    report = (lab.observer.dir / "runs" / "r5.md").read_text()
    assert f"- call #{first} by Alpha (a): **rejected** — Which fruit?" in report
    assert f"- Cyd (c): 1 point for call #{second} — kiwi" in report
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "2 rulings: 1 accepted, 1 rejected." in summary
    assert "| Alpha (a) | 0 | 1 | 1 |" in summary and "| Cyd (c) | 1 | 0 | 0 |" in summary
    for name in ("scorer_points.png", "scorer_rulings.png", "self_credit.png"):
        assert (lab.observer.dir / "charts" / name) in result.written


async def test_observe_a_store_from_before_the_scorer(lab, community):
    store, conn = community
    conn.execute("DROP TABLE scorer_points")
    await observe(lab, judge_client=None, judge_note="--no-judge")
    summary = (lab.observer.dir / "summary.md").read_text()
    assert "_The Scorer has not ruled yet._" in summary
    assert "_The Scorer did not rule in this run._" in (lab.observer.dir / "runs" / "r1.md").read_text()


async def test_an_analysis_can_have_its_own_judge(lab, community):
    store, conn = community
    add_run(conn, "r5", created=FUTURE, goal_id="debate", process=2)
    add_entry(conn, "r5", "a", "think", {"text": "Pear is weak."}, round=1, visible_to=["a"])
    add_entry(conn, "r5", "a", "propose", {"answer_summary": "Pear is fine.", "method": "m"}, round=1)
    lab = Config.model_validate({
        "models": {"claude": {"kind": "claude_cli"}, "local": {"kind": "openai", "model": "qwen"}},
        "observer": {"judge": "claude", "judges": {"think_vs_act": "local"}, "dir": lab.observer.dir},
        "community_dir": lab.community_dir, "runs_dir": lab.runs_dir})
    default, local = FakeModel(judge_reply, model="sonnet"), FakeModel(judge_reply, model="qwen")
    result = await observe(lab, judge_client=default, analysis_clients={"think_vs_act": local})
    assert result.failed == 0
    assert local.calls and all("## think notes" in m[0]["content"] for _, m, _ in local.calls)
    assert not any("## think notes" in m[0]["content"] for _, m, _ in default.calls)
    report = (lab.observer.dir / "runs" / "r5.md").read_text()
    assert "think “Pear is weak.” / action “Pear is fine.”" in report
    assert "_Judged by qwen (think_vs_act), sonnet (" in report


async def test_an_analysis_judge_without_a_client_is_an_error(lab):
    lab.observer.judges = {"think_vs_act": "claude"}
    with pytest.raises(ValueError, match="think_vs_act"):
        await observe(lab, judge_client=FakeModel(judge_reply))


async def test_unknown_analysis_in_judges_is_an_error(lab):
    lab.observer.judges = {"vibes": "claude"}
    with pytest.raises(ValueError, match="vibes"):
        await observe(lab, judge_client=FakeModel(judge_reply), analysis_clients={"vibes": FakeModel(judge_reply)})


async def test_observe_reports_progress(lab):
    lines: list[str] = []
    await observe(lab, judge_client=FakeModel(judge_reply), progress=lines.append)
    text = "\n".join(lines)
    assert "2 closed runs" in text and "r3" in text  # what it covers and what it skips
    assert "0 cached" in text and "to judge" in text
    assert any(line.startswith("  [1/") and "themes" in line and "ok" in line for line in lines)
    assert "codebook" in text
    assert "r1.md" in text and "summary.md" in text


async def test_progress_counts_cached_judgments(lab):
    client = FakeModel(judge_reply)
    await observe(lab, judge_client=client)
    lines: list[str] = []
    await observe(lab, judge_client=client, progress=lines.append)
    assert any("cached, 0 to judge" in line for line in lines)
    assert not any(line.startswith("  [1/") for line in lines)  # no judgment lines, only run headers


async def test_progress_without_a_judge(lab):
    lines: list[str] = []
    await observe(lab, judge_note="--no-judge", progress=lines.append)
    assert any("metrics only" in line for line in lines)
    assert not any("to judge" in line for line in lines)


class Slow:
    """Wraps a client so each call takes a moment, and records the most calls in flight at once (shared by all)."""
    in_flight = peak = 0

    def __init__(self, inner):
        self.inner, self.context_window = inner, inner.context_window

    async def chat(self, system, messages, json_schema=None):
        Slow.in_flight += 1
        Slow.peak = max(Slow.peak, Slow.in_flight)
        try:
            await asyncio.sleep(0.01)
            return await self.inner.chat(system, messages, json_schema)
        finally:
            Slow.in_flight -= 1


async def test_judges_share_the_run_concurrency_limit(lab, community):
    store, conn = community
    add_run(conn, "r5", created=FUTURE, goal_id="debate", process=2)
    for agent in ("a", "b", "c"):
        add_entry(conn, "r5", agent, "think", {"text": "Pear is weak."}, round=1, visible_to=[agent])
        add_entry(conn, "r5", agent, "propose", {"answer_summary": "Pear is fine.", "method": "m"}, round=1)
    lab = Config.model_validate({
        "models": {"claude": {"kind": "claude_cli"}, "local": {"kind": "openai", "model": "qwen"}},
        "run": {"max_concurrent_calls": 2},
        "observer": {"judge": "claude", "judges": {"think_vs_act": "local"}, "dir": lab.observer.dir},
        "community_dir": lab.community_dir, "runs_dir": lab.runs_dir})
    Slow.in_flight = Slow.peak = 0
    await observe(lab, judge_client=Slow(FakeModel(judge_reply)),
                  analysis_clients={"think_vs_act": Slow(FakeModel(judge_reply))})
    assert Slow.peak == 2
