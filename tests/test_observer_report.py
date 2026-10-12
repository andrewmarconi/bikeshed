from collections import Counter
from dataclasses import replace

from aiswarm.observer.charts import marker_positions, network_chart, trend_chart
from aiswarm.observer.metrics import BenchmarkAnswer, CategoryStats, Credits, Influence, RunInfo
from aiswarm.observer.process2 import OrderStat, Participation, RecurringPair
from aiswarm.observer.report import (
    Process2Section, Process2Summary, RunSection, SummarySection, render_run, render_summary,
)
from aiswarm.observer.store import Judgment
from aiswarm.observer.report import ScorerSummary
from aiswarm.observer.scoring import Award, RulingRow, SelfCredit

RUN = RunInfo("r1", "Pick a fruit.", "best-fruit", "contested", False, "budget", ("a", "b"), "t", "t")


def who(agent_id):
    return {"a": "Alpha (a)", "b": "Bea (b)"}.get(agent_id, agent_id)


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


def test_run_report_shows_dimension_scores():
    text = render_run(section(dimensions={"a": {"clarity": 2.5}}))
    assert "## Dimensions" in text and "| Alpha (a) | 2.5 |" in text
    assert "## Dimensions" not in render_run(section())


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
    assert "e vs no twin found" in text
    assert "r9" in text


P2 = replace(RUN, process=2)


def test_run_report_names_the_process():
    assert "- **Process:** 1" in render_run(section())
    assert "- **Process:** 2" in render_run(section(run=P2))


def test_summary_marks_process_2_runs():
    s = SummarySection(runs=[RUN, replace(P2, id="r2", goal_id="dishwasher")], who=who, charts={}, identity=[],
                       norms=[], codebook=[], twins={}, categories={}, failed=[], benchmark=[], reciprocity=[],
                       in_progress=[], judge_note=None)
    assert "2 closed runs: best-fruit, dishwasher (P2)" in render_summary(s)


def test_network_chart_writes_a_png(tmp_path):
    path = network_chart(tmp_path / "c" / "n.png", "Private messages", {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"},
                         {("a", "b"): 3, ("b", "a"): 1})
    assert path.exists() and path.read_bytes()[:4] == b"\x89PNG"


def test_network_chart_with_one_agent_and_no_messages(tmp_path):
    path = network_chart(tmp_path / "n.png", "Private messages", {"a": "Alpha (a)"}, {})
    assert path.read_bytes()[:4] == b"\x89PNG"


P2_SECTION = Process2Section(
    network=Counter({("a", "b"): 2}), chart="../charts/network_r1.png",
    participation={"a": Participation(supported=1, unprompted=1), "b": Participation(abstained=1)},
    order=[OrderStat(1, 2, 1), OrderStat(2, 2, 0)])


def tva(agent):
    return Judgment("think_vs_act", "r1", agent, "ok", {"points": [
        {"relation": "diverges", "about": "mango", "think_quote": "I doubt it", "action_quote": "Mango is fine"}]},
        None, "claude-x", "p")


def test_process_2_run_report():
    text = render_run(section(run=P2, process2=P2_SECTION, judgments=[themes("a"), tva("a")]))
    block = text.split("## Process 2\n")[1].split("\n## Themes")[0]
    assert "| From \\ To | Alpha (a) | Bea (b) |" in block and "| Alpha (a) | – | 2 |" in block
    assert "![Private messages](../charts/network_r1.png)" in block
    assert "| Alpha (a) | 1 | 1 | 0 | 0 | 0 | 1 |" in block  # polled, support, object, abstain, failed, unprompted
    assert "| 1 | 2 | 1 | 50% |" in block and "| 2 | 2 | 0 | 0% |" in block
    tva_block = text.split("## Think notes vs actions (private vs public)\n")[1]
    assert "**diverges** — mango: think “I doubt it” / action “Mango is fine”" in tva_block
    assert "_wrote no think notes_" in tva_block  # b has no judgment and none was expected


def test_process_2_run_without_private_messages_or_sequential_rounds():
    empty = Process2Section(network=Counter(), chart=None, participation={}, order=[])
    block = render_run(section(run=P2, process2=empty)).split("## Process 2\n")[1]
    assert "_No private messages._" in block and "_No rounds after the first._" in block


def test_process_1_run_report_has_no_process_2_sections():
    text = render_run(section())
    assert "## Process 2" not in text and "Think notes vs actions" not in text


def test_summary_process_2_section():
    s = SummarySection(runs=[P2], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={},
                       failed=[], benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
                       process2=Process2Summary([RecurringPair("a", "b", 2, 5)], {"b": Participation(objected=2)},
                                                [OrderStat(1, 4, 3)]))
    block = render_summary(s).split("## Process 2\n")[1]
    assert "- Alpha (a) → Bea (b): 2 runs, 5 messages" in block
    assert "| Bea (b) | 2 | 0 | 2 | 0 | 0 | 0 |" in block
    assert "| 1 | 4 | 3 | 75% |" in block


def test_poll_and_order_tables_say_what_they_count():
    run = render_run(section(run=P2, process2=P2_SECTION))
    assert "times polled" in run and "asked again each round" in run
    s = SummarySection(runs=[P2], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={},
                       failed=[], benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
                       process2=Process2Summary([], {}, [OrderStat(1, 4, 3)]))
    assert "Summed over all Process 2 runs, whatever their size" in render_summary(s)


def test_run_report_lists_rulings_feedback_and_awards():
    text = render_run(section(rulings=[RulingRow(4, "a", False, "Which fruit?"), RulingRow(9, "b", True, "")],
                              awards=[Award("b", 9, "me"), Award("a", 9, "kiwi")]))
    scorer = text.split("## Scorer\n")[1]
    assert "- call #4 by Alpha (a): **rejected** — Which fruit?" in scorer
    assert "- call #9 by Bea (b): **accepted**" in scorer
    assert "- Bea (b): 1 point for call #9 — me (self-credit)" in scorer
    assert "- Alpha (a): 1 point for call #9 — kiwi" in scorer
    assert text.index("## Credits") < text.index("## Scorer")


def test_run_report_without_rulings():
    assert "_The Scorer did not rule in this run._" in render_run(section())


def test_summary_scorer_section():
    s = SummarySection(
        runs=[RUN], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={}, failed=[],
        benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
        scorer=ScorerSummary(accepted=1, rejected=2, points={"a": 3},
                             self_credit={"a": SelfCredit(4, 1), "b": SelfCredit(0, 0)}))
    text = render_summary(s)
    scorer = text.split("## Scorer\n")[1]
    assert "3 rulings: 1 accepted, 2 rejected." in scorer
    assert "| Alpha (a) | 3 | 4 | 1 |" in scorer and "| Bea (b) | 0 | 0 | 0 |" in scorer
    assert text.index("## Credit reciprocity") < text.index("## Scorer")


def test_summary_before_any_ruling():
    s = SummarySection(
        runs=[RUN], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={}, failed=[],
        benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
        scorer=ScorerSummary(0, 0, {}, {}))
    assert "_The Scorer has not ruled yet._" in render_summary(s)
