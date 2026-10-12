from rich.text import Text

from aiswarm.board import Entry
from aiswarm.watch import render
from aiswarm.watch.state import AgentCard, CallLine, FeedLine, FileLine, PostRunRow, RunState, feed_line

COLOURS = render.agent_colours(["a", "b"])
PINK, TEAL = render.AGENT_COLOURS[0], render.AGENT_COLOURS[1]


def state(**kw):
    s = RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def who(agent_id):
    return {"a": "Ash (a)", "b": "Bea (b)", "system": "system"}.get(agent_id, f"{agent_id} ({agent_id})")


def line(type_, payload, author="a"):
    return feed_line(Entry(5, "r1", 2, author, type_, payload, [], "all", "t"), who)


def style_of(text: Text, fragment: str) -> str:
    start = text.plain.index(fragment)
    return " ".join(str(s.style) for s in text.spans if s.start <= start < s.end)


ALL = [line("propose", {"answer_summary": "Mango [bold]ripe[/] [red]x", "method": "m"}),
       line("critique", {"target": 3, "points": "too sweet"}, "b"),
       line("vote", {"target": 3, "stance": "object", "reason": "no"}),
       line("vote", {"target": 3, "stance": "support", "reason": ""}),
       line("pass", {"failure": "model_error"}), line("pass", {}),
       line("think", {"text": "[/] where I stand"}), line("private_message", {"to": "b", "text": "psst"}),
       line("private_message", {"to": "zz", "text": "to nobody"}),
       line("artifact_changed", {"path": "x"}, "system"), line("question", {"text": "why?"}, "q")]


def test_styled_text_reads_exactly_like_the_plain_line():
    for item in ALL:
        assert render.styled(item, COLOURS).plain == item.text


def test_brackets_in_entries_stay_literal():
    assert "Mango [bold]ripe[/] [red]x" in render.styled(ALL[0], COLOURS).plain


def test_action_words_and_agents_are_coloured():
    propose = render.styled(ALL[0], COLOURS)
    assert "green" in style_of(propose, "propose") and f"bold {PINK}" in style_of(propose, "Ash (a)")
    assert "dark_orange" in style_of(render.styled(ALL[1], COLOURS), "critique")
    assert "red" in style_of(render.styled(ALL[2], COLOURS), "object")
    assert "green" in style_of(render.styled(ALL[3], COLOURS), "support")
    assert "red" in style_of(render.styled(ALL[4], COLOURS), "(failed: model_error)")
    assert "dim" in style_of(render.styled(ALL[5], COLOURS), "pass")
    pm = render.styled(ALL[7], COLOURS)
    assert f"bold {TEAL}" in style_of(pm, "Bea (b)")
    assert style_of(render.styled(ALL[9], COLOURS), "artifact_changed") == "dim italic"


def test_unknown_agents_are_bold_without_a_colour():
    assert style_of(render.styled(ALL[8], COLOURS), "zz (zz)") == "bold"
    assert style_of(render.styled(ALL[10], COLOURS), "q (q)") == "bold"


def test_agent_colours_follow_id_order_and_repeat():
    colours = render.agent_colours(["f", "b", "a", "c", "d", "e"])
    assert [colours[x] for x in "abcde"] == list(render.AGENT_COLOURS)
    assert colours["f"] == render.AGENT_COLOURS[0]


def test_status_bar():
    s = state(process=2, phase="rounds", step_phase="react", round=7,
              cards={"a": AgentCard("a", "Ash", control=True, reputation=4.25, state="speaking…"),
                     "b": AgentCard("b", "Bartholomew-the-long", state="failed: model_error")},
              calls=[CallLine(40, "Bea (b)", 3, 1), CallLine(12, "Ash (a)", 1, 0)])
    bar = render.status(s, COLOURS)
    assert bar.plain == ("round 7/12 · react │ Ash ctl ★4.2 speaking… · Bartholomew… ★– failed: model_error"
                         " │ call #40: 3 for, 1 against")
    assert "bold" in style_of(bar, "speaking…") and "red" in style_of(bar, "failed: model_error")
    assert f"bold {PINK}" in style_of(bar, "Ash")
    assert render.status(state(), COLOURS).plain == "round 0/12 · waiting │ (no agents yet) │ no call yet"


def test_phase_label():
    assert render.phase_label(state(process=2, phase="rounds", step_phase="poll")) == "poll"
    assert render.phase_label(state(process=2, phase="post-run", step_phase="poll")) == "post-run"
    assert render.phase_label(state(phase="rounds")) == "rounds"


def test_popup_holds_the_workspace_and_post_run_grid():
    s = state(files=[FileLine("rules.md", 3, "c", 4)], postrun=[PostRunRow("journal", 5, 0, [])], postrun_seen=True)
    text = render.popup(s)
    assert "Workspace\nrules.md v3 (c, r4)" in text and "Post-run" in text and "journal" in text


def test_files_and_postrun_grid():
    assert render.files(state()) == "Workspace\n(no files yet)"
    rows = [PostRunRow("journal", 5, 0, []), PostRunRow("review", 3, 2, ["b", "d"])]
    text = render.postrun(state(postrun=rows, postrun_seen=True, current_step="review"))
    assert "done 5" in text and "failed 2 (b, d)" in text and "← running" in text
    assert render.postrun(state(closed_reason="budget")) == "Post-run\nno post-run"


def test_show_postrun_only_after_the_rounds():
    assert not render.show_postrun(state(phase="rounds"))
    assert render.show_postrun(state(closed_reason="budget"))


def test_footer():
    assert render.footer(state(), True) == "q quit · f follow: on · w workspace"
    assert render.footer(state(bad_lines=2), False) == \
        "q quit · f follow: off · w workspace · 2 unreadable event lines"


def test_feed_line_type_is_exported_for_the_app():
    assert FeedLine is type(ALL[0])
