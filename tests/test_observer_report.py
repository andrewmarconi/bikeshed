from collections import Counter

from aiswarm.observer.charts import marker_positions, trend_chart
from aiswarm.observer.metrics import BenchmarkAnswer, CategoryStats, Credits, Influence, RunInfo
from aiswarm.observer.report import RunSection, SummarySection, render_run, render_summary
from aiswarm.observer.store import Judgment

RUN = RunInfo("r1", "Pick a fruit.", "best-fruit", "contested", False, "budget", ("a", "b"), "t", "t")


def who(agent_id):
    return {"a": "Alpha (a)", "b": "Bea (b)"}[agent_id]


def themes(agent):
    return Judgment("themes", "r1", agent, "ok", {"themes": [
        {"label": "candour", "polarity": "expressed", "evidence": "I say it straight", "source": "journal"}]},
        None, "claude-x", "p")


def section(**kw):
    base = dict(run=RUN, who=who, changes=[], mix={"a": Counter(propose=2), "b": Counter(failed=1)},
                influence={"a": Influence(adopted=1), "b": Influence()}, credits=None, peer={"a": 3.5},
                gap={"a": 1.0}, judgments=[themes("a")], codes={"candour": "honesty"}, benchmark=None,
                judge_note=None)
    base.update(kw)
    return RunSection(**base)


def test_marker_positions_sit_between_runs():
    assert marker_positions([1, 3]) == [0.5, 2.5]


def test_trend_chart_writes_a_png(tmp_path):
    path = trend_chart(tmp_path / "c" / "x.png", "Peer score", ["r1", "r2"], {"a": [3.0, None]}, [1])
    assert path.exists() and path.read_bytes()[:4] == b"\x89PNG"


def test_run_report_sections():
    text = render_run(section())
    assert text.startswith("# r1 — Pick a fruit.")
    assert "contested" in text and "budget" in text
    assert "| Alpha (a) |" in text
    assert "honesty" in text and "I say it straight" in text
    assert "No closing call yet" in text
    assert "_not judged (no judgment)_" in text  # b has no themes judgment


def test_run_report_shows_harness_changes_and_failures():
    failed = Judgment("themes", "r1", "b", "failed", None, "invented quote", None, "p")
    text = render_run(section(changes=["since run r0: prompts journal_request.md"], judgments=[themes("a"), failed]))
    assert "Harness changed" in text and "journal_request.md" in text
    assert "_not judged (invented quote)_" in text


def test_run_report_without_a_judge():
    text = render_run(section(judgments=[], judge_note="no judge configured"))
    assert "_not judged (no judge configured)_" in text


def test_run_report_credits():
    text = render_run(section(credits=Credits("a", {"a": "me", "b": "idea"})))
    assert "self-credit" in text and "idea" in text


def test_summary_sections(tmp_path):
    s = SummarySection(
        runs=[RUN], who=who, charts={"Peer score": "charts/peer.png"}, identity=[], norms=[],
        codebook=[("honesty", "Says what it thinks.")], twins={"e": []},
        categories={"contested": CategoryStats(1, Counter(budget=1), Counter(propose=2))},
        failed=[], benchmark=[BenchmarkAnswer("r1", False, "- Alpha (a): Pepperoni")], reciprocity=[],
        in_progress=["r9"], judge_note=None)
    text = render_summary(s)
    assert "![Peer score](charts/peer.png)" in text
    assert "honesty" in text and "Pepperoni" in text
    assert "e: no twin found" in text
    assert "r9" in text
