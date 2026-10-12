import os
import shutil
import tempfile
from pathlib import Path

import pytest

from bikeshed.actions import parse_action, step_schema
from bikeshed.config import ClaudeCliBackend, OpenAIBackend
from bikeshed.models.claude_cli import ClaudeCliClient, leak_check
from bikeshed.models.openai_compat import OpenAICompatClient

pytestmark = pytest.mark.smoke
VIEW = "# Goal\nName a colour.\n\n# Round 1\nThis is the opening round. Propose your own position (action `propose`)."
SYSTEM = 'You are Test (t). Reply with JSON only, in the form {"action": {...}}.'


def claude_client():
    return ClaudeCliClient(ClaudeCliBackend(kind="claude_cli"), workdir=Path(tempfile.mkdtemp(prefix="bikeshed-claude-")))


def omlx_backend():
    return OpenAIBackend(
        kind="openai",
        model=os.environ["BIKESHED_SMOKE_MODEL"],
        base_url=os.environ.get("BIKESHED_SMOKE_BASE_URL", "http://localhost:8000/v1"),
        api_key=os.environ.get("BIKESHED_SMOKE_API_KEY", "omlx"),
    )


@pytest.mark.skipif(not os.environ.get("BIKESHED_SMOKE_MODEL"), reason="set BIKESHED_SMOKE_MODEL to an oMLX model id")
async def test_omlx_returns_a_parseable_action():
    client = OpenAICompatClient(omlx_backend())
    assert os.environ["BIKESHED_SMOKE_MODEL"] in await client.list_models()
    result = await client.chat(SYSTEM, [{"role": "user", "content": VIEW}], step_schema())
    assert parse_action(result.text).type == "propose"


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
async def test_claude_is_isolated():
    ok, tokens = await leak_check(claude_client())
    assert ok, f"claude -p saw {tokens} input tokens; outside context may be leaking in"


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
async def test_claude_accepts_the_step_schema():
    result = await claude_client().chat(SYSTEM, [{"role": "user", "content": VIEW}], step_schema())
    assert parse_action(result.text).type == "propose"
