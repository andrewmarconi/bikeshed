"""Configuration loading for AISwarm."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class OpenAIBackend(BaseModel):
    kind: Literal["openai"]
    model: str
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "omlx"
    context_window: int = Field(32768, gt=0)
    json_schema: bool = True
    thinking: bool | None = None  # None leaves it to the server's per-model setting
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
    process: Literal[1, 2] = 1  # 1: all act at once; 2: think, then react in turn (with a poll)
    quorum_slack: int = Field(1, ge=0)
    stagnation_rounds: int = Field(2, ge=1)
    view_entries: int = Field(15, ge=1)
    max_reads: int = Field(3, ge=0)
    model_retries: int = Field(3, ge=1)
    max_concurrent_calls: int = Field(4, ge=1)
    reputation_half_life_runs: float = Field(5.0, gt=0)


class ReportSettings(BaseModel):
    narrator: str | None = None  # a backend that writes report/narrative.md for the user


class LibrarySettings(BaseModel):
    embeddings: str | None = None  # an openai backend serving an embeddings model
    top_k: int = Field(3, ge=0)


class ObserverSettings(BaseModel):
    judge: str | None = None  # a backend that judges text for `aiswarm observe`; agents never see its output
    dir: Path = Path("observer")


class ScorerSettings(BaseModel):
    judge: str | None = None  # the backend that rules whether a passing call answers the goal; agents see its rulings


class Config(BaseModel):
    community_dir: Path = Path("community")
    runs_dir: Path = Path("runs")
    models: dict[str, Backend]
    run: RunSettings = Field(default_factory=RunSettings)
    report: ReportSettings = Field(default_factory=ReportSettings)
    library: LibrarySettings = Field(default_factory=LibrarySettings)
    observer: ObserverSettings = Field(default_factory=ObserverSettings)
    scorer: ScorerSettings = Field(default_factory=ScorerSettings)

    @model_validator(mode="after")
    def _known_backends(self) -> "Config":
        for label, name in (("report.narrator", self.report.narrator), ("library.embeddings", self.library.embeddings),
                            ("observer.judge", self.observer.judge), ("scorer.judge", self.scorer.judge)):
            if name is not None and name not in self.models:
                raise ValueError(f"{label} names unknown model backend {name!r}")
        if self.library.embeddings and not isinstance(self.models[self.library.embeddings], OpenAIBackend):
            raise ValueError("library.embeddings must name an openai backend")
        return self

    @property
    def db_path(self) -> Path:
        return self.community_dir / "community.db"


def load_config(path: Path) -> Config:
    """Load a config file; relative paths resolve against the file's directory."""
    cfg = Config.model_validate(tomllib.loads(path.read_text()))
    base = path.resolve().parent
    cfg.community_dir = (base / cfg.community_dir).resolve()
    cfg.runs_dir = (base / cfg.runs_dir).resolve()
    cfg.observer.dir = (base / cfg.observer.dir).resolve()
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
# thinking = true                     # true: reason first (slower; JSON not schema-enforced, since oMLX
#                                       # skips thinking under a schema). false: no reasoning. Omitted: schema
#                                       # is enforced, which in practice means no thinking.

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
max_concurrent_calls = 4               # simultaneous model calls across all agents
reputation_half_life_runs = 5          # a rating this many runs old counts half

[report]
# narrator = "claude"                  # a backend that writes report/narrative.md (for you; agents never see it)

[library]
# embeddings = "embed"                 # an openai backend serving an embeddings model, e.g.:
#   [models.embed]
#   kind = "openai"
#   base_url = "http://localhost:8000/v1"
#   model = "mlx-community--all-MiniLM-L6-v2-4bit"
top_k = 3                              # methods from earlier runs shown in each View

[observer]
# judge = "claude"                     # a backend that judges text for `aiswarm observe` (agents never see it)
dir = "observer"                       # Observer reports and its own database

[scorer]
# the backend that rules whether a passing call for consensus answers the goal (agents see its rulings)
judge = "claude"
"""
