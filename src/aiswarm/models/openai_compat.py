"""OpenAI-compatible backend, used for oMLX."""

from typing import Any

from openai import APIError, AsyncOpenAI

from aiswarm.config import OpenAIBackend
from aiswarm.models.base import ChatResult, Message, ModelError


class OpenAICompatClient:
    def __init__(self, backend: OpenAIBackend, client: AsyncOpenAI | None = None):
        self.backend = backend
        self.context_window = backend.context_window
        self._client = client or AsyncOpenAI(
            base_url=backend.base_url, api_key=backend.api_key, timeout=backend.timeout_s, max_retries=0
        )

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult:
        kwargs: dict[str, Any] = {}
        # oMLX skips thinking entirely under a JSON schema, so thinking agents get prompt-only JSON.
        if json_schema is not None and self.backend.json_schema and not self.backend.thinking:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "schema": json_schema}}
        if self.backend.thinking is not None:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": self.backend.thinking}}
        try:
            response = await self._client.chat.completions.create(
                model=self.backend.model,
                messages=[{"role": "system", "content": system}, *messages],
                **kwargs,
            )
        except APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        message = response.choices[0].message
        extra = message.model_extra or {}
        return ChatResult(
            text=message.content or "",
            reasoning=extra.get("reasoning_content") or extra.get("reasoning"),
            model=response.model,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
        )

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.models.list()
        except APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        return [model.id for model in page.data]
