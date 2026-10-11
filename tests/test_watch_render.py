from aiswarm.watch import render
from aiswarm.watch.state import AgentCard, CallLine, FileLine, PostRunRow, RunState


def state(**kw):
    s = RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_header():
    assert render.header(state(round=4, phase="rounds")) == \
        "best-fruit (contested) · run r1 · round 4/12 · rounds — Pick a fruit."


def test_cards_show_control_reputation_state_and_last_action():
    s = state(cards={"a": AgentCard("a", "Ash", control=True, reputation=4.25, state="thinking…", last="critique #301"),
                     "b": AgentCard("b", "Bea")})
    text = render.cards(s)
    assert "Ash (a) [control] ★4.2  thinking…" in text and "  last: critique #301" in text
    assert "Bea (b) ★–  idle" in text and "  last: –" in text


def test_calls_and_files():
    s = state(calls=[CallLine(9, "Bea (b)", 3, 1)], files=[FileLine("rules.md", 3, "c", 4)])
    assert render.calls(s) == "Calls for consensus\n#9 by Bea (b): 3 for, 1 against"
    assert render.files(s) == "Workspace\nrules.md v3 (c, r4)"
    assert render.calls(state()) == "Calls for consensus\n(none yet)"
    assert render.files(state()) == "Workspace\n(no files yet)"


def test_postrun_grid():
    rows = [PostRunRow("journal", 5, 0, []), PostRunRow("review", 3, 2, ["b", "d"])]
    text = render.postrun(state(postrun=rows, postrun_seen=True, current_step="review"))
    assert "journal" in text and "done 5" in text
    assert "review" in text and "failed 2 (b, d)" in text and "← running" in text
    assert render.postrun(state(closed_reason="budget")) == "Post-run\nno post-run"


def test_show_postrun_only_after_the_rounds():
    assert not render.show_postrun(state(phase="rounds"))
    assert render.show_postrun(state(closed_reason="budget"))


def test_footer():
    assert render.footer(state(), True) == "q quit · f follow: on"
    assert render.footer(state(bad_lines=2), False) == "q quit · f follow: off · 2 unreadable event lines"
