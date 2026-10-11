import json

import pytest
from observer_helpers import add_entry, add_journal, add_review, add_run

from aiswarm.config import Config
from aiswarm.models.fake import FakeModel
from aiswarm.observer.observe import observe

FUTURE = "2099-01-01T00:00:00+00:00"  # after every fixture commit; git cannot parse far-future dates like 2999


def config(tmp_path):
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}, "observer": {"judge": "claude"}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    cfg.observer.dir = tmp_path / "observer"
    return cfg


def judge_reply(system, messages):
    text = messages[0]["content"]
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
    add_run(conn, "r1", created=FUTURE, goal_id="best-fruit", category="contested")
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


async def test_changing_the_judge_model_re_judges(lab):
    client = FakeModel(judge_reply)
    await observe(lab, judge_client=client)
    calls = len(client.calls)
    lab.models["claude"].model = "opus"
    await observe(lab, judge_client=client)
    assert len(client.calls) > calls


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
