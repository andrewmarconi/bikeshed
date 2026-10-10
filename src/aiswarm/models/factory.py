"""Build a model client for a configured backend."""

import tempfile
from pathlib import Path

from aiswarm.config import Backend, OpenAIBackend
from aiswarm.models.base import ModelClient, RetryingClient
from aiswarm.models.claude_cli import ClaudeCliClient
from aiswarm.models.openai_compat import OpenAICompatClient


def make_client(backend: Backend, retries: int) -> RetryingClient:
    inner: ModelClient
    if isinstance(backend, OpenAIBackend):
        inner = OpenAICompatClient(backend)
    else:
        # An empty directory outside $HOME so no CLAUDE.md or project settings are discovered.
        inner = ClaudeCliClient(backend, workdir=Path(tempfile.mkdtemp(prefix="aiswarm-claude-")))
    return RetryingClient(inner, attempts=retries)
