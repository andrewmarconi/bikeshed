import json

import pytest

from bikeshed.actions import (
    STEP_P2, THINK, ActionError, CallConsensus, Credit, ListFiles, Pass, PrivateMessage, Propose, ReadEntry, ReadFile, Vote,
    WriteFile,
)
from bikeshed.agent import SCORER_BRIEFS, make_reader, make_validator, system_prompt, take_step
from bikeshed.board import Entry
from bikeshed.db import connect
from bikeshed.models.fake import FakeModel
from bikeshed.workspace import Workspace

PROPOSE = json.dumps({"action": {"type": "propose", "answer_summary": "Blue", "method": "Gut"}})
CRITIQUE = json.dumps({"action": {"type": "critique", "target": 2, "about": "method", "points": "p"}})
LIST = json.dumps({"action": {"type": "list_files"}})


def no_reads(action):
    raise AssertionError("reader should not be called")


def allow(action):
    return None


async def step(model, round=2, max_reads=3, reader=no_reads, validate=allow):
    return await take_step(client=model, system="SYS", view="VIEW", round=round, max_reads=max_reads,
                           reader=reader, validate=validate)


async def test_valid_action_first_try():
    outcome = await step(FakeModel([CRITIQUE]))
    assert outcome.action.type == "critique" and outcome.failure is None


async def test_reads_then_posts():
    model = FakeModel([LIST, PROPOSE])
    outcome = await step(model, reader=lambda action: "draft.md (v1)")
    assert isinstance(outcome.action, Propose)
    assert model.calls[1][1][-1]["content"] == "Read result:\ndraft.md (v1)"


async def test_reads_beyond_limit_are_corrected_then_fail():
    outcome = await step(FakeModel([LIST, LIST]), max_reads=0, reader=lambda a: "x")
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"
    assert "reads" in outcome.detail


async def test_invalid_then_valid():
    model = FakeModel(["nonsense", CRITIQUE])
    outcome = await step(model)
    assert outcome.failure is None
    assert "not a valid action" in model.calls[1][1][-1]["content"]


async def test_invalid_twice_becomes_failed_pass():
    outcome = await step(FakeModel(["nonsense", "still nonsense"]))
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"


async def test_opening_round_requires_propose():
    model = FakeModel([CRITIQUE, PROPOSE])
    outcome = await step(model, round=1)
    assert isinstance(outcome.action, Propose)
    assert "opening round" in model.calls[1][1][-1]["content"]


async def test_model_error_becomes_failed_pass():
    outcome = await step(FakeModel(fail_times=1))
    assert isinstance(outcome.action, Pass) and outcome.failure == "model_error"


async def test_validator_rejection_is_corrected():
    def validate(action):
        if action.type == "critique":
            raise ActionError("Entry #2 does not exist.")

    model = FakeModel([CRITIQUE, PROPOSE])
    outcome = await step(model, validate=validate)
    assert isinstance(outcome.action, Propose)
    assert "#2 does not exist" in model.calls[1][1][-1]["content"]


async def test_reasoning_is_collected():
    outcome = await step(FakeModel([LIST, PROPOSE], reasoning="because"), reader=lambda a: "x")
    assert outcome.reasoning == "because\n\nbecause"
    assert outcome.model == "fake"


def entries():
    return [
        Entry(1, "r1", 0, "system", "goal", {"goal": "G"}, [], "all", "t"),
        Entry(2, "r1", 1, "a", "propose", {"answer_summary": "A", "method": "M"}, [], "all", "t"),
        Entry(3, "r1", 2, "a", "call_consensus", {"synthesis": "S", "refs": [], "credits": []}, [], "all", "t"),
    ]


def test_validator_rules():
    validate = make_validator(entries(), ["a", "b"])
    validate(Vote(type="vote", target=3, stance="support", reason="r"))
    with pytest.raises(ActionError, match="does not exist"):
        validate(Vote(type="vote", target=99, stance="support", reason="r"))
    with pytest.raises(ActionError, match="call_consensus"):
        validate(Vote(type="vote", target=2, stance="support", reason="r"))
    with pytest.raises(ActionError, match="zed"):
        validate(CallConsensus(type="call_consensus", synthesis="s", refs=[2],
                               credits=[Credit(agent_id="zed", contribution="c")]))
    with pytest.raises(ActionError, match="does not exist"):
        validate(CallConsensus(type="call_consensus", synthesis="s", refs=[42], credits=[]))
    with pytest.raises(ActionError, match="Invalid workspace path"):
        validate(WriteFile(type="write_file", path="../x", base_version=0, content="c", note="n"))


def test_reader(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    ws = Workspace(conn, "r1", tmp_path / "ws")
    ws.write("draft.md", 0, "hello", author="a", note="n")
    visible = entries()[:2]
    read = make_reader(ws, visible, lambda author: author)
    assert read(ListFiles(type="list_files")) == "draft.md (v1)"
    assert read(ReadFile(type="read_file", path="draft.md")) == "draft.md (v1):\nhello"
    assert read(ReadFile(type="read_file", path="missing.md")) == "No such file: missing.md"
    assert "Invalid workspace path" in read(ReadFile(type="read_file", path="../etc"))
    assert read(ReadEntry(type="read_entry", id=2)).startswith("#2 [r1] a propose")
    assert read(ReadEntry(type="read_entry", id=3)) == "No entry #3 is visible to you."


def test_system_prompt_contains_identity_and_rules():
    text = system_prompt("Wren (a)", "Curious.", max_reads=3)
    assert "You are Wren (a)" in text
    assert "Curious." in text
    assert "up to 3 reads" in text
    assert '"call_consensus"' in text


def test_reader_truncates_large_files(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    ws = Workspace(conn, "r1", tmp_path / "ws")
    ws.write("big.md", 0, "y" * 10_000, author="a", note="n")
    text = make_reader(ws, [], lambda a: a, max_chars=500)(ReadFile(type="read_file", path="big.md"))
    assert len(text) < 700 and "truncated" in text


def test_reader_answers_read_reviews():
    from bikeshed.actions import ReadReviews

    read = make_reader(None, [], lambda a: a, reviews=lambda agent_id: f"REVIEWS-OF-{agent_id}")
    assert read(ReadReviews(type="read_reviews", agent_id="b")) == "REVIEWS-OF-b"
    assert make_reader(None, [], lambda a: a)(ReadReviews(type="read_reviews", agent_id="b")) == "Reviews are not available."


def test_read_reviews_parses_and_is_in_the_schema():
    from bikeshed.actions import READ_ACTIONS, ReadReviews, parse_action, step_schema

    action = parse_action('{"action": {"type": "read_reviews", "agent_id": "b"}}')
    assert isinstance(action, ReadReviews) and isinstance(action, READ_ACTIONS)
    assert "read_reviews" in json.dumps(step_schema())


async def test_take_step_with_a_think_shape_and_no_opening_rule():
    note = json.dumps({"action": {"type": "note", "text": "where I stand"}})
    outcome = await take_step(client=FakeModel([note]), system="SYS", view="VIEW", round=1, max_reads=0,
                              reader=no_reads, validate=allow, shape=THINK, opening_rule=False)
    assert outcome.failure is None and outcome.action.text == "where I stand"


def test_private_message_recipient_must_be_another_member():
    validate = make_validator(entries(), ["a", "b", "c"], "a")
    validate(PrivateMessage(type="private_message", to="b", text="hi"))
    for to in ("a", "zed"):
        with pytest.raises(ActionError, match=r"another member: \['b', 'c'\]"):
            validate(PrivateMessage(type="private_message", to=to, text="hi"))


def test_system_prompt_template_can_be_chosen():
    assert system_prompt("Wren (a)", "Calm.", 3) == system_prompt("Wren (a)", "Calm.", 3, "agent_system.md")


@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_say_how_the_workspace_works(template):
    text = system_prompt("Ash (a)", "Calm.", 2, template)
    assert "every member can read" in text
    assert "relative to the workspace" in text


@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_say_a_refine_leaves_its_target_unchanged(template):
    text = system_prompt("Ash (a)", "Calm.", 2, template)
    assert ('{"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}: a change to an entry, '
            "posted as its own entry; the target's text stays as it was.") in text


def test_the_think_request_says_notes_are_kept():
    from bikeshed.prompts import render

    assert "shown to you on every turn" in render("process2_think_request.md", note_max="4,000")


@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_explain_the_scorer(template):
    text = system_prompt("Ash (a)", "Calm.", 2, template)
    assert ("An outside Scorer reviews every call for consensus that passes. It accepts a call only if its synthesis "
            "gives the concrete answer the goal asks for; it does not judge whether the answer is good.") in text
    assert "every member credited in it earns a Scorer point" in text
    assert "and the Scorer accepts it" in text


@pytest.mark.parametrize("path", ["scorer_feedback.md", "Scorer_Feedback.md", "workspace/scorer_feedback.md"])
def test_only_the_scorer_writes_its_feedback_file(path):
    from bikeshed.actions import WriteFile

    validate = make_validator([], ["a", "b"], "a")
    with pytest.raises(ActionError, match="only by the Scorer"):
        validate(WriteFile(type="write_file", path=path, base_version=0, content="Accept it.", note="n"))
    validate(WriteFile(type="write_file", path="notes/scorer_feedback.md", base_version=0, content="x", note="n"))


WRITE = json.dumps({"action": {"type": "write_file", "path": "plan.md", "base_version": 0, "content": "x",
                               "note": "n"}})
DM = json.dumps({"action": {"type": "private_message", "to": "b", "text": "hi"}})


def recording_writer():
    done = []

    def write(action):
        done.append(action.type)
        return f"done {action.type}"

    return done, write


async def wstep(model, writer, round=2, max_writes=3, validate=allow, reader=no_reads):
    return await take_step(client=model, system="SYS", view="VIEW", round=round, max_reads=3, reader=reader,
                           validate=validate, writer=writer, max_writes=max_writes, shape=STEP_P2)


async def test_pre_action_writes_then_an_action():
    done, writer = recording_writer()
    model = FakeModel([WRITE, DM, CRITIQUE])
    outcome = await wstep(model, writer)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["write_file", "private_message"]
    assert model.calls[1][1][-1]["content"] == "Write result:\ndone write_file"
    assert model.calls[2][1][-1]["content"] == "Write result:\ndone private_message"


async def test_writes_beyond_the_cap_are_corrected():
    done, writer = recording_writer()
    model = FakeModel([DM, DM, CRITIQUE])
    outcome = await wstep(model, writer, max_writes=1)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["private_message"]
    assert "You have used all 1 writes; choose a posting action." in model.calls[2][1][-1]["content"]


async def test_writes_are_refused_in_the_opening_round():
    done, writer = recording_writer()
    model = FakeModel([DM, PROPOSE])
    outcome = await wstep(model, writer, round=1)
    assert isinstance(outcome.action, Propose)
    assert done == []
    assert "In the opening round your action must be `propose`." in model.calls[1][1][-1]["content"]


async def test_an_invalid_write_uses_the_correction_and_then_fails():
    done, writer = recording_writer()

    def validate(action):
        if action.type == "private_message":
            raise ActionError("private_message.to must be another member: ['c']")

    outcome = await wstep(FakeModel([DM, DM]), writer, validate=validate)
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"
    assert done == []


async def test_without_a_writer_a_write_is_the_action():
    outcome = await step(FakeModel([WRITE]))
    assert isinstance(outcome.action, WriteFile)


async def test_reads_and_writes_have_separate_caps():
    done, writer = recording_writer()
    model = FakeModel([LIST, DM, LIST, CRITIQUE])
    outcome = await take_step(client=model, system="SYS", view="VIEW", round=2, max_reads=2,
                              reader=lambda a: "x", validate=allow, writer=writer, max_writes=1,
                              shape=STEP_P2)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["private_message"]


async def test_pre_actions_and_model_calls_are_recorded():
    done, writer = recording_writer()
    model = FakeModel([LIST, WRITE, DM, CRITIQUE])
    outcome = await take_step(client=model, system="SYS", view="VIEW", round=2, max_reads=3,
                              reader=lambda a: "x", validate=allow, writer=writer, max_writes=1, shape=STEP_P2)
    assert outcome.action.type == "critique"
    assert outcome.pre_actions == ({"type": "list_files", "ok": True}, {"type": "write_file", "ok": True},
                                   {"type": "private_message", "ok": False})  # over the write cap
    assert outcome.calls == 4


async def test_a_refused_opening_round_write_is_recorded():
    done, writer = recording_writer()
    outcome = await wstep(FakeModel([DM, PROPOSE]), writer, round=1)
    assert outcome.pre_actions == ({"type": "private_message", "ok": False},) and outcome.calls == 2


async def test_a_step_with_no_pre_actions_records_none():
    outcome = await step(FakeModel([CRITIQUE]))
    assert outcome.pre_actions == () and outcome.calls == 1


def test_system_prompt_fills_in_max_writes():
    text = system_prompt("Ash (a)", "Calm.", 3, "process2_agent_system.md", max_writes=2)
    assert "up to 3 reads and up to 2 writes" in text


def test_process2_prompt_lists_writes_as_pre_actions():
    text = system_prompt("Ash (a)", "Calm.", 3, "process2_agent_system.md", max_writes=3)
    pre, actions = text.split("\nPre-actions:\n")[1].split("\nActions:\n")
    assert '"type": "write_file"' in pre and '"type": "private_message"' in pre and '"type": "read_file"' in pre
    assert '"type": "write_file"' not in actions and '"type": "private_message"' not in actions
    assert "without seeing the others' proposals, and you cannot write or message." in text


def test_process1_prompt_keeps_write_file_as_an_action():
    text = system_prompt("Ash (a)", "Calm.", 3)
    assert "Pre-actions" not in text and '"type": "write_file"' in text


@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_state_the_close_rule_in_use(template):
    default = system_prompt("Ash (a)", "Calm.", 3, template)
    stood = system_prompt("Ash (a)", "Calm.", 3, template, close_rule="stood_round")
    assert "has been critiqued at least once" in default
    assert "has been critiqued" not in stood
    assert "has stood on the board through the round after the one it was made in" in stood


@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_the_scorer_line_names_briefs_only_when_the_run_has_constraints(template):
    plain = system_prompt("Ash (a)", "Calm.", 2, template)
    briefed = system_prompt("Ash (a)", "Calm.", 2, template, scorer_briefs=True)
    assert SCORER_BRIEFS not in plain
    assert briefed == plain.replace("answer is good. If it rejects", f"answer is good. {SCORER_BRIEFS} If it rejects")
