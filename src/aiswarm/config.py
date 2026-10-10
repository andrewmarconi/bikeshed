"""Configuration loading for AISwarm."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class OpenAIBackend(BaseModel):
    kind: Literal["openai"]
    model: str
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "omlx"
    context_window: int = Field(32768, gt=0)
    json_schema: bool = True
    timeout_s: float = Field(300.0, gt=0)


class ClaudeCliBackend(BaseModel):
    kind: Literal["claude_cli"]
    model: str = "haiku"
    bare: bool = False
    executable: str = "claude"
    context_window: int = Field(200_000, gt=0)
    timeout_s: float = Field(300.0, gt=0)
    leak_threshold_tokens: int = Field(3000, gt=0)


Backend = Annotated[OpenAIBackend | ClaudeCliBackend, Field(discriminator="kind")]


class RunSettings(BaseModel):
    max_rounds: int = Field(12, ge=1)
    quorum_slack: int = Field(1, ge=0)
    stagnation_rounds: int = Field(2, ge=1)
    view_entries: int = Field(15, ge=1)
    max_reads: int = Field(3, ge=0)
    model_retries: int = Field(3, ge=1)


class Config(BaseModel):
    community_dir: Path = Path("community")
    runs_dir: Path = Path("runs")
    models: dict[str, Backend]
    run: RunSettings = Field(default_factory=RunSettings)

    @property
    def db_path(self) -> Path:
        return self.community_dir / "community.db"


def load_config(path: Path) -> Config:
    """Load a config file; relative paths resolve against the file's directory."""
    cfg = Config.model_validate(tomllib.loads(path.read_text()))
    base = path.resolve().parent
    cfg.community_dir = (base / cfg.community_dir).resolve()
    cfg.runs_dir = (base / cfg.runs_dir).resolve()
    return cfg


DEFAULT_CONFIG = """\
# AISwarm configuration. Paths are relative to this file.
community_dir = "community"
runs_dir = "runs"

[models.local]
kind = "openai"                       # oMLX (OpenAI-compatible)
base_url = "http://localhost:8000/v1"
model = "CHANGE-ME"                   # an id from: curl localhost:8000/v1/models
context_window = 32768

[models.claude]
kind = "claude_cli"                   # headless `claude -p`, isolated
model = "haiku"
bare = false                          # true adds --bare (requires ANTHROPIC_API_KEY)

[run]
max_rounds = 12
quorum_slack = 1
stagnation_rounds = 2
view_entries = 15
max_reads = 3
model_retries = 3
"""
