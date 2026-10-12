import json

import pytest
from pydantic import ValidationError

from aiswarm.models.fake import FakeModel
from aiswarm.scorer import Ruling, score, scorer_request

ACCEPT = json.dumps({"accepted": True, "feedback": ""})
REJECT = json.dumps({"accepted": False, "feedback": "It describes a poll, not a topping combination."})


def test_a_rejection_needs_feedback():
    assert Ruling(accepted=True).feedback == ""
    with pytest.raises(ValidationError):
        Ruling(accepted=False, feedback="   ")


def test_the_scorer_sees_only_the_goal_and_the_synthesis():
    request = scorer_request("Pick a topping.", "Mushrooms and olives.")
    assert request == "## Goal\n\nPick a topping.\n\n## Synthesis\n\nMushrooms and olives.\n\nReply with JSON only."


async def test_score_returns_the_ruling_and_the_reported_model():
    client = FakeModel([REJECT], model="claude-x")
    result = await score(client, "Pick a topping.", "We will run a poll.")
    assert result.ruling == Ruling(accepted=False, feedback="It describes a poll, not a topping combination.")
    assert result.model == "claude-x" and result.detail is None
    system, messages, schema = client.calls[0]
    assert "Scorer" in system and messages[0]["content"] == scorer_request("Pick a topping.", "We will run a poll.")
    assert schema is not None


async def test_an_invalid_ruling_gets_one_correction():
    client = FakeModel([json.dumps({"accepted": False, "feedback": ""}), ACCEPT])
    result = await score(client, "G", "S")
    assert result.ruling == Ruling(accepted=True) and len(client.calls) == 2


async def test_two_invalid_rulings_give_no_ruling():
    client = FakeModel(["not json", "still not json"])
    result = await score(client, "G", "S")
    assert result.ruling is None and result.detail.startswith("invalid ruling:")


async def test_a_model_error_gives_no_ruling():
    result = await score(FakeModel([ACCEPT], fail_times=1), "G", "S")
    assert result.ruling is None and result.detail.startswith("model error:")
