"""Scripted model client for tests and offline development."""

from collections.abc import Callable, Sequence
from typing import Any

from aiswarm.models.base import ChatResult, Message, ModelError

Responder = str | Callable[[str, list[Message]], str]
_PASS = '{"action": {"type": "pass"}}'


class FakeModel:
    def __init__(
        self,
        script: Sequence[Responder] | Callable[[str, list[Message]], str] = (),
        *,
        fail_times: int = 0,
        context_window: int = 32768,
        model: str = "fake",
        reasoning: str | None = None,
    ):
        self._script = script if callable(script) else list(script)
        self.fail_times = fail_times
        self.context_window = context_window
        self.model = model
        self.reasoning = reasoning
        self.calls: list[tuple[str, list[Message], dict[str, Any] | None]] = []

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult:
        self.calls.append((system, [dict(m) for m in messages], json_schema))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ModelError("fake outage")
        if callable(self._script):
            text = self._script(system, messages)
        elif self._script:
            item = self._script.pop(0)
            text = item(system, messages) if callable(item) else item
        else:
            text = _PASS
        return ChatResult(text=text, reasoning=self.reasoning, model=self.model)
