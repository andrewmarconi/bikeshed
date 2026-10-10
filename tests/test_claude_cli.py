import json
from pathlib import Path

import pytest

from aiswarm.config import ClaudeCliBackend, OpenAIBackend
from aiswarm.models.base import ModelError, RetryingClient
from aiswarm.models.claude_cli import ClaudeCliClient, build_argv, leak_check, parse_output, render_prompt
from aiswarm.models.factory import make_client
from aiswarm.models.openai_compat import OpenAICompatClient


def sample(input_tokens=2, cache_creation=1193, **overrides):
    payload = {
        "is_error": False, "subtype": "success", "result": '{"x": 1}', "structured_output": {"x": 1},
        "modelUsage": {"claude-haiku-5-5": {
            "inputTokens": input_tokens, "cacheCreationInputTokens": cache_creation,
            "cacheReadInputTokens": 0, "canonicalModel": "claude-haiku-5-5",
        }},
    }
    payload.update(overrides)
    return json.dumps(payload)


def flag(argv, name):
    return argv[argv.index(name) + 1]


def test_build_argv_isolates_the_session():
    argv = build_argv(ClaudeCliBackend(kind="claude_cli", model="haiku"), "SYS", {"type": "object"})
    assert argv[:2] == ["claude", "-p"]
    assert flag(argv, "--model") == "haiku"
    assert flag(argv, "--tools") == ""
    assert flag(argv, "--setting-sources") == ""
    assert flag(argv, "--system-prompt") == "SYS"
    assert flag(argv, "--output-format") == "json"
    assert json.loads(flag(argv, "--json-schema")) == {"type": "object"}
    assert "--no-session-persistence" in argv
    assert "--strict-mcp-config" in argv  # claude.ai connectors otherwise attach MCP tools nondeterministically
    assert "--bare" not in argv


def test_build_argv_bare_and_no_schema():
    argv = build_argv(ClaudeCliBackend(kind="claude_cli", bare=True), "SYS", None)
    assert "--bare" in argv
    assert "--json-schema" not in argv


def test_render_prompt_single_user_message_is_verbatim():
    assert render_prompt([{"role": "user", "content": "hi"}]) == "hi"


def test_render_prompt_transcript():
    text = render_prompt([
        {"role": "user", "content": "view"},
        {"role": "assistant", "content": "{}"},
        {"role": "user", "content": "result"},
    ])
    assert text == "[user]\nview\n\n[assistant]\n{}\n\n[user]\nresult"


def test_parse_output_prefers_structured_output():
    result = parse_output(sample())
    assert json.loads(result.text) == {"x": 1}
    assert result.model == "claude-haiku-5-5"
    assert result.input_tokens == 1195


def test_parse_output_falls_back_to_result_text():
    assert parse_output(sample(structured_output=None)).text == '{"x": 1}'


def test_parse_output_error_and_garbage():
    with pytest.raises(ModelError):
        parse_output(sample(is_error=True, result="rate limited"))
    with pytest.raises(ModelError):
        parse_output("not json")


async def test_client_runs_in_workdir_with_prompt_on_stdin(tmp_path):
    seen = {}

    async def runner(argv, stdin_text, timeout, cwd):
        seen.update(argv=argv, stdin=stdin_text, cwd=cwd)
        return 0, sample(), ""

    client = ClaudeCliClient(ClaudeCliBackend(kind="claude_cli"), workdir=tmp_path, runner=runner)
    result = await client.chat("SYS", [{"role": "user", "content": "view"}], {"type": "object"})
    assert seen["cwd"] == tmp_path
    assert seen["stdin"] == "view"
    assert flag(seen["argv"], "--system-prompt") == "SYS"
    assert result.model == "claude-haiku-5-5"
    assert client.context_window == 200_000


async def test_client_nonzero_exit_raises(tmp_path):
    async def runner(argv, stdin_text, timeout, cwd):
        return 1, "", "boom"

    client = ClaudeCliClient(ClaudeCliBackend(kind="claude_cli"), workdir=tmp_path, runner=runner)
    with pytest.raises(ModelError, match="boom"):
        await client.chat("S", [{"role": "user", "content": "x"}])


async def test_leak_check_passes_and_fails_on_token_count(tmp_path):
    async def small(argv, stdin_text, timeout, cwd):
        return 0, sample(), ""

    async def large(argv, stdin_text, timeout, cwd):
        return 0, sample(cache_creation=50_000), ""

    backend = ClaudeCliBackend(kind="claude_cli")
    assert await leak_check(ClaudeCliClient(backend, workdir=tmp_path, runner=small)) == (True, 1195)
    assert await leak_check(ClaudeCliClient(backend, workdir=tmp_path, runner=large)) == (False, 50_002)


def test_make_client_builds_retrying_backends():
    local = make_client(OpenAIBackend(kind="openai", model="m"), retries=2)
    claude = make_client(ClaudeCliBackend(kind="claude_cli"), retries=2)
    assert isinstance(local, RetryingClient) and isinstance(local.inner, OpenAICompatClient)
    assert isinstance(claude, RetryingClient) and isinstance(claude.inner, ClaudeCliClient)
    assert not claude.inner.workdir.is_relative_to(Path.home())
