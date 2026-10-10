"""Model client interface shared by all backends."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

Message = dict[str, str]


@dataclass(frozen=True)
class ChatResult:
    text: str
    reasoning: str | None = None
    model: str | None = None
    input_tokens: int | None = None


class ModelError(RuntimeError):
    """The backend failed or was unreachable."""


class ModelClient(Protocol):
    context_window: int

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult: ...


class RetryingClient:
    """Retries ModelError with exponential backoff, then re-raises."""

    def __init__(
        self,
        inner: ModelClient,
        attempts: int,
        base_delay: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.inner = inner
        self.attempts = attempts
        self.base_delay = base_delay
        self._sleep = sleep
        self.context_window = inner.context_window

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult:
        last: ModelError | None = None
        for attempt in range(self.attempts):
            try:
                return await self.inner.chat(system, messages, json_schema)
            except ModelError as exc:
                last = exc
                if attempt < self.attempts - 1:
                    await self._sleep(self.base_delay * 2**attempt)
        assert last is not None
        raise last
