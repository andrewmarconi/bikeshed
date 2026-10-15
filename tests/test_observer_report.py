from collections import Counter
from dataclasses import replace

from bikeshed.observer.copying import CallCredit, Match, NormOverlap, Reuse
from bikeshed.observer.charts import marker_positions, network_chart, trend_chart
from bikeshed.observer.metrics import BenchmarkAnswer, CategoryStats, Credits, Influence, RunInfo
from bikeshed.observer.process2 import OrderStat, Participation, RecurringPair
from bikeshed.observer.report import (
    Process2Section, Process2Summary, RunSection, SummarySection, render_run, render_summary,
)
from bikeshed.observer.store import Judgment
from bikeshed.observer.report import ScorerSummary
from bikeshed.observer.scoring import Award, RulingRow, SelfCredit

RUN = RunInfo("r1", "Pick a fruit.", "best-fruit", "unsettled", False, "budget", ("a", "b"), "t", "t")


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
    assert "unsettled" in text and "budget" in text
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
        categories={"unsettled": CategoryStats(1, Counter(budget=1), Counter(propose=2))},
        failed=[], benchmark=[BenchmarkAnswer("r1", False, "- Alpha (a): Pepperoni")], reciprocity=[],
        in_progress=["r9"], judge_note=None)
    text = render_summary(s)
    assert "![Peer score](charts/peer.png)" in text
    assert "honesty" in text and "Pepperoni" in text
    assert "e vs no twin found" in text
    assert "r9" in text


def summary_section(**kw):
    base = dict(runs=[], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={}, failed=[],
                benchmark=[], reciprocity=[], in_progress=[], judge_note=None)
    return SummarySection(**(base | kw))


P2 = replace(RUN, process=2)
P3 = replace(RUN, process=3)


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
        {"round": 2, "relation": "diverges", "about": "mango", "think_quote": "I doubt it",
         "action_quote": "Mango is fine"}]},
        None, "claude-x", "p")


def test_process_2_run_report():
    text = render_run(section(run=P2, process2=P2_SECTION, judgments=[themes("a"), tva("a")]))
    block = text.split("## Process 2\n")[1].split("\n## Themes")[0]
    assert "| From \\ To | Alpha (a) | Bea (b) |" in block and "| Alpha (a) | – | 2 |" in block
    assert "![Private messages](../charts/network_r1.png)" in block
    assert "| Alpha (a) | 1 | 1 | 0 | 0 | 0 | 1 |" in block  # polled, support, object, abstain, failed, unprompted
    assert "| 1 | 2 | 1 | 50% |" in block and "| 2 | 2 | 0 | 0% |" in block
    tva_block = text.split("## Think notes vs actions (private vs public)\n")[1]
    assert "- [r2] **diverges** — mango: think “I doubt it” / action “Mango is fine”" in tva_block
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
                       meetings={2: Process2Summary([RecurringPair("a", "b", 2, 5)], {"b": Participation(objected=2)},
                                                    [OrderStat(1, 4, 3)])})
    block = render_summary(s).split("## Process 2\n")[1]
    assert "- Alpha (a) → Bea (b): 2 runs, 5 messages" in block
    assert "| Bea (b) | 2 | 0 | 2 | 0 | 0 | 0 |" in block
    assert "| 1 | 4 | 3 | 75% |" in block


def test_poll_and_order_tables_say_what_they_count():
    run = render_run(section(run=P2, process2=P2_SECTION))
    assert "times polled" in run and "asked again each round" in run
    s = SummarySection(runs=[P2], who=who, charts={}, identity=[], norms=[], codebook=[], twins={}, categories={},
                       failed=[], benchmark=[], reciprocity=[], in_progress=[], judge_note=None,
                       meetings={2: Process2Summary([], {}, [OrderStat(1, 4, 3)])})
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


def test_multi_line_feedback_stays_inside_its_list_item():
    text = render_run(section(rulings=[RulingRow(4, "a", False, "Missing:\n- a price\n# Next")]))
    assert "- call #4 by Alpha (a): **rejected** — Missing: - a price # Next" in text
    assert "\n# Next" not in text


def test_process_3_run_report_and_tag():
    text = render_run(section(run=P3, process2=P2_SECTION, judgments=[themes("a"), tva("a")]))
    assert "## Process 3\n" in text and "## Process 2" not in text
    assert "## Think notes vs actions (private vs public)" in text
    assert P3.tag == "best-fruit (P3)" and P2.tag == "best-fruit (P2)" and RUN.tag == "best-fruit"


def test_summary_has_one_section_per_meeting_process():
    s = summary_section(runs=[P2, P3], meetings={
        2: Process2Summary([RecurringPair("a", "b", 2, 5)], {}, []),
        3: Process2Summary([RecurringPair("b", "a", 3, 7)], {}, [])})
    text = render_summary(s)
    two = text.split("## Process 2\n")[1].split("## Process 3\n")[0]
    three = text.split("## Process 3\n")[1]
    assert "Alpha (a) → Bea (b): 2 runs" in two and "Bea (b) → Alpha (a)" not in two
    assert "Bea (b) → Alpha (a): 3 runs" in three and "Summed over all Process 3 runs" in three


def self_image(agent):
    return Judgment("self_image", "r1", agent, "ok", {"changes": [], "private_public_gap": "none",
                                                      "gap_evidence": ""}, None, "claude-x", "p")


def test_the_controls_self_image_says_it_is_compared_with_its_seed():
    text = render_run(section(judgments=[self_image("a"), self_image("b")], controls=frozenset({"b"})))
    block = text.split("## Self-image\n")[1]
    alpha, bea = block.split("### Bea (b)")
    assert "compared with its seed" not in alpha
    assert bea.lstrip().startswith("_control: compared with its seed, not its previous run_")


def test_old_think_vs_act_points_without_round_still_render():
    old = Judgment("think_vs_act", "r1", "a", "ok", {"points": [
        {"relation": "agrees", "about": "pear", "think_quote": "x", "action_quote": "y"}]}, None, "claude-x", "p")
    text = render_run(section(run=P2, process2=P2_SECTION, judgments=[old]))
    assert "- **agrees** — pear: think “x” / action “y”" in text


def test_run_report_shows_copying_credit_checks_and_library_reuse():
    copied = [Match("a", 9, "vote", "reason", "b", 7, "exact", True)]
    checks = [CallCredit(11, "a", ("b",), ("a", "c"), ("b",), ("c",))]
    reuse = [Reuse(4, "r0", "b", ("a", "b"), (("a", 8, "partial"),)), Reuse(5, "r0", "b", ("a",), ())]
    text = render_run(section(copies=copied, credit_checks=checks, reuse=reuse))
    block = text.split("## Copying\n")[1].split("\n## ")[0]
    assert "| Alpha (a) | #9 vote.reason | #7 Bea (b) | exact | same round |" in block
    assert "_Matches: exact, or partial when at least 50%" in block
    assert ("- #11 by Alpha (a): copied from Bea (b); credited Alpha (a), c; uncredited: Bea (b); "
            "credited without matching text: c") in text
    lib = text.split("## Library reuse\n")[1].split("\n## ")[0]
    assert "- method #4 (run r0, by Bea (b)), shown to Alpha (a), Bea (b): reused by Alpha (a) in #8 (partial)" in lib
    assert "- method #5 (run r0, by Bea (b)), shown to Alpha (a): not reused" in lib


def test_run_report_without_copies_or_a_library_record():
    text = render_run(section())
    assert "_No copied text found._" in text.split("## Copying\n")[1]
    assert "_Not recorded._" in text.split("## Library reuse\n")[1]


def test_summary_shows_copying_credit_gaps_library_reuse_and_norm_overlap():
    text = render_summary(summary_section(
        copying={2: Counter({("a", "b", "exact"): 3, ("a", "b", "partial"): 1})},
        credit_gaps={2: (1, 4)}, reuse={2: (2, 6)}, methods_shown=[(4, 5, 2)],
        norm_overlaps=[NormOverlap(14, "r10", "a", 1, "Cite the entry you disagree with.", "partial", "rejected")]))
    copying = text.split("## Copying\n")[1].split("\n## ")[0]
    assert "### Process 2" in copying and "| Alpha (a) | Bea (b) | 3 | 1 |" in copying
    assert "Process 2: 1 of 4 calls left out a copied source." in text
    lib = text.split("## Library reuse\n")[1].split("\n## ")[0]
    assert "Process 2: 2 of 6 shown methods reused." in lib and "| #4 | 5 | 2 |" in lib
    overlap = text.split("## Norm overlap\n")[1].split("\n## ")[0]
    assert "| #14 | r10 | Alpha (a) | #1 Cite the entry you disagree with. | partial | rejected |" in overlap


def test_summary_without_norm_overlap():
    assert "_No proposal restates an adopted norm._" in render_summary(summary_section())
