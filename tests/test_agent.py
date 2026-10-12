import json

import pytest

from aiswarm.actions import (
    THINK, ActionError, CallConsensus, Credit, ListFiles, Pass, PrivateMessage, Propose, ReadEntry, ReadFile, Vote,
    WriteFile,
)
from aiswarm.agent import make_reader, make_validator, system_prompt, take_step
from aiswarm.board import Entry
from aiswarm.db import connect
from aiswarm.models.fake import FakeModel
from aiswarm.workspace import Workspace

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
    from aiswarm.actions import ReadReviews

    read = make_reader(None, [], lambda a: a, reviews=lambda agent_id: f"REVIEWS-OF-{agent_id}")
    assert read(ReadReviews(type="read_reviews", agent_id="b")) == "REVIEWS-OF-b"
    assert make_reader(None, [], lambda a: a)(ReadReviews(type="read_reviews", agent_id="b")) == "Reviews are not available."


def test_read_reviews_parses_and_is_in_the_schema():
    from aiswarm.actions import READ_ACTIONS, ReadReviews, parse_action, step_schema

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
