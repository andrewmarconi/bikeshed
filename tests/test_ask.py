import pytest

from aiswarm.models.base import ModelError
from aiswarm.models.fake import FakeModel
from aiswarm.postrun.ask import ReplyError, ask
from aiswarm.postrun.replies import JournalReply, ReviewReply, UpdateReply
from aiswarm.prompts import render


async def test_ask_returns_the_validated_reply_and_reasoning():
    client = FakeModel(['{"journal": "Fine."}'], reasoning="thinking")
    answer = await ask(client, "SYS", "REQUEST", JournalReply)
    assert answer.reply.journal == "Fine." and answer.reasoning == "thinking"
    system, messages, schema = client.calls[0]
    assert system == "SYS" and messages == [{"role": "user", "content": "REQUEST"}]
    assert schema["properties"]["journal"]["type"] == "string"


async def test_ask_corrects_once_then_succeeds():
    client = FakeModel(["no json here", '{"journal": "Second try."}'])
    answer = await ask(client, "S", "R", JournalReply)
    assert answer.reply.journal == "Second try."
    assert "could not be accepted" in client.calls[1][1][-1]["content"]


async def test_ask_runs_the_check_and_gives_up_after_one_correction():
    reply = '{"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [99], "overall": 3, "overall_reason": "r"}'
    client = FakeModel([reply, reply])

    def check(r):
        raise ReplyError("entry #99 is not part of this run")

    with pytest.raises(ReplyError, match="#99"):
        await ask(client, "S", "R", ReviewReply, check)
    assert len(client.calls) == 2


async def test_ask_rejects_out_of_range_ratings():
    bad = '{"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [2], "overall": 9, "overall_reason": "r"}'
    with pytest.raises(ReplyError, match="overall"):
        await ask(FakeModel([bad, bad]), "S", "R", ReviewReply)


async def test_model_errors_propagate():
    with pytest.raises(ModelError):
        await ask(FakeModel(fail_times=1), "S", "R", JournalReply)


def test_update_reply_optional_rewrites_default_to_none():
    reply = UpdateReply.model_validate({"journal": "j", "lessons": "l"})
    assert reply.self_description is None and reply.public_profile is None


def test_postrun_templates_render():
    assert render("postrun_system.md", display="Alpha (a)", temperament="T", private_notes="").startswith("You are Alpha (a)")
    assert render("journal_request.md", packet="P", journal_max=6000).startswith("# Post-run: journal")
    assert render("review_request.md", heading="H", run_id="r1", goal="G", authors="you", transcript="T",
                  instruction="I", subject="you", object="yourself", private_reader="Only you will read this.",
                  visibility="V").startswith("# Post-run: review")
    assert render("responses_request.md", run_id="r1", reviews="R").startswith("# Post-run: responses")
    assert render("update_request.md", packet="P", first_journal="J", reviews="R", journal_max=1, private_max=2,
                  name_max=3, headline_max=4, intro_max=5).startswith("# Post-run: update")


def test_postrun_reply_fixture_answers_each_step(postrun_reply):
    assert postrun_reply("# Round 2\n...") is None
    assert '"journal"' in postrun_reply("# Post-run: journal\n...")
    assert '"evidence": [7]' in postrun_reply("# Post-run: review of x\n\n#7 [r1] Alpha (a) propose\n")
    assert '"review_id": 12' in postrun_reply("# Post-run: responses\n\nReview 12 of Alpha (a) by Bea (b) after run r1\n")
    assert '"lessons"' in postrun_reply("# Post-run: update\n...")
