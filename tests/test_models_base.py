import asyncio
import json

import pytest

from aiswarm.models.base import ChatResult, ModelError, RetryingClient, limit_clients
from aiswarm.models.fake import FakeModel


async def test_fake_returns_script_in_order_then_pass():
    model = FakeModel(["a", lambda system, messages: f"echo:{messages[-1]['content']}"])
    assert (await model.chat("sys", [{"role": "user", "content": "x"}])).text == "a"
    assert (await model.chat("sys", [{"role": "user", "content": "y"}])).text == "echo:y"
    assert json.loads((await model.chat("sys", [])).text) == {"action": {"type": "pass"}}
    assert model.calls[0][0] == "sys"


async def test_fake_with_single_callable_answers_every_call():
    model = FakeModel(lambda system, messages: "same", reasoning="why")
    first = await model.chat("s", [])
    second = await model.chat("s", [])
    assert first.text == second.text == "same"
    assert first.reasoning == "why"


async def test_fake_can_fail():
    model = FakeModel(["ok"], fail_times=1)
    with pytest.raises(ModelError):
        await model.chat("s", [])
    assert (await model.chat("s", [])).text == "ok"


async def test_retrying_client_retries_with_backoff_then_succeeds():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    client = RetryingClient(FakeModel(["ok"], fail_times=2), attempts=3, base_delay=1.0, sleep=fake_sleep)
    assert (await client.chat("s", [])).text == "ok"
    assert sleeps == [1.0, 2.0]
    assert client.context_window == 32768


async def test_retrying_client_gives_up():
    async def fake_sleep(seconds):
        pass

    client = RetryingClient(FakeModel(fail_times=5), attempts=3, sleep=fake_sleep)
    with pytest.raises(ModelError):
        await client.chat("s", [])


class _Slow:
    context_window = 1000

    def __init__(self, tracker):
        self.tracker = tracker

    async def chat(self, system, messages, json_schema=None):
        self.tracker["now"] += 1
        self.tracker["peak"] = max(self.tracker["peak"], self.tracker["now"])
        await asyncio.sleep(0.01)
        self.tracker["now"] -= 1
        return ChatResult(text="ok")


async def test_limit_clients_caps_simultaneous_calls_across_clients():
    tracker = {"now": 0, "peak": 0}
    clients = limit_clients({key: _Slow(tracker) for key in "abcdef"}, 2)
    results = await asyncio.gather(*(c.chat("s", []) for c in clients.values() for _ in range(3)))
    assert tracker["peak"] == 2
    assert [r.text for r in results] == ["ok"] * 18
    assert clients["a"].context_window == 1000
