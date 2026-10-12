"""Scripted model client for tests and offline development."""

import re
import zlib
from collections.abc import Callable, Sequence
from typing import Any

from bikeshed.models.base import ChatResult, Message, ModelError

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


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are similar."""

    def __init__(self, dims: int = 64, fail_times: int = 0):
        self.dims = dims
        self.fail_times = fail_times
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ModelError("fake outage")
        vectors = []
        for text in texts:
            vector = [0.0] * self.dims
            for word in re.findall(r"[a-z]+", text.lower()):
                vector[zlib.crc32(word.encode()) % self.dims] += 1.0
            vectors.append(vector)
        return vectors