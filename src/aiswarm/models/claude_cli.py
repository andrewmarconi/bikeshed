"""Claude backend via headless Claude Code (`claude -p`), isolated from the user's environment."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiswarm.config import ClaudeCliBackend
from aiswarm.models.base import ChatResult, Message, ModelError

Runner = Callable[[list[str], str, float, Path], Awaitable[tuple[int, str, str]]]


def build_argv(backend: ClaudeCliBackend, system: str, json_schema: dict[str, Any] | None) -> list[str]:
    argv = [
        backend.executable, "-p",
        "--model", backend.model,
        "--tools", "",
        "--setting-sources", "",
        "--strict-mcp-config",  # account-level MCP connectors would otherwise attach tools at random
        "--no-session-persistence",
        "--output-format", "json",
        "--system-prompt", system,
    ]
    if backend.bare:
        argv.append("--bare")
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    return argv


def render_prompt(messages: list[Message]) -> str:
    """`claude -p` takes one prompt, so multi-turn exchanges are rendered as a transcript."""
    if len(messages) == 1 and messages[0]["role"] == "user":
        return messages[0]["content"]
    return "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)


def parse_output(stdout: str) -> ChatResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ModelError(f"claude -p returned non-JSON output: {stdout[:200]!r}") from exc
    if data.get("is_error"):
        raise ModelError(f"claude -p error: {data.get('result') or data.get('subtype')}")
    structured = data.get("structured_output")
    text = json.dumps(structured) if structured is not None else (data.get("result") or "")
    usage = data.get("modelUsage") or {}
    model_key = next(iter(usage), None)
    stats = usage.get(model_key, {}) if model_key else {}
    input_tokens = (
        stats.get("inputTokens", 0) + stats.get("cacheCreationInputTokens", 0) + stats.get("cacheReadInputTokens", 0)
        if stats else None
    )
    return ChatResult(text=text, model=stats.get("canonicalModel", model_key), input_tokens=input_tokens)


async def _run_subprocess(argv: list[str], stdin_text: str, timeout: float, cwd: Path) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=cwd,
        )
    except OSError as exc:
        raise ModelError(f"could not start {argv[0]}: {exc}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ModelError(f"claude -p timed out after {timeout}s") from None
    return proc.returncode or 0, out.decode(), err.decode()


class ClaudeCliClient:
    def __init__(self, backend: ClaudeCliBackend, workdir: Path, runner: Runner = _run_subprocess):
        self.backend = backend
        self.workdir = workdir
        self.context_window = backend.context_window
        self._runner = runner

    async def chat(
        self, system: str, messages: list[Message], json_schema: dict[str, Any] | None = None
    ) -> ChatResult:
        argv = build_argv(self.backend, system, json_schema)
        code, out, err = await self._runner(argv, render_prompt(messages), self.backend.timeout_s, self.workdir)
        if code != 0 and not out.strip():
            raise ModelError(f"claude -p exited {code}: {err.strip()[:500]}")
        return parse_output(out)


async def leak_check(client: ClaudeCliClient) -> tuple[bool, int | None]:
    """A minimal call must see almost no input; extra tokens mean outside context leaked in."""
    result = await client.chat("Reply with the single word OK.", [{"role": "user", "content": "OK?"}])
    tokens = result.input_tokens
    return tokens is not None and tokens <= client.backend.leak_threshold_tokens, tokens
