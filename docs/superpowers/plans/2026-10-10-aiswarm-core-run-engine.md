# AISwarm Core Run Engine Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `aiswarm` CLI where persistent, self-named agents (on oMLX or `claude -p`) self-organise on a shared board and workspace to answer a goal prompt, ending by consensus, stagnation, or budget, with a report and a live event stream.

**Architecture:** Plain-code Runner (no orchestrator model) ticks agents concurrently each round. Each agent receives a privacy-filtered, budget-trimmed View and returns one validated JSON action. Board entries, rounds, files, failures, and reasoning traces live in SQLite; agent profiles and per-run workspaces are git repos. Rounds are atomic so runs can pause and resume.

**Tech Stack:** Python 3.14, uv, openai SDK (AsyncOpenAI against oMLX), Claude Code CLI (`claude -p`), pydantic v2, typer, sqlite3, git, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-10-aiswarm-design.md`

**Later plans (not this one):** Plan 2 — post-run cycle (account narrative, Method Library, reflection, reviews, ratings/reputation, norms). Plan 3 — experiment & observation (provenance, control agent, goal queue, timeline, Textual TUI, Observer).

## Global Constraints

- Python `>=3.14`, managed by `uv`. Add dependencies with bare `uv add <pkg>` (let uv resolve the version); verified current at plan time: openai 3.28.0, pydantic 2.14.0, typer 0.27.3, pytest 9.1.1, pytest-asyncio 1.4.0.
- openai ≥ 3.0 uses HTTPX2 internally; never pass a custom `httpx` client.
- No agent framework (no CAMEL, LangChain, etc.).
- No orchestrator model: the Runner never reads content to decide or steer.
- Observation never feeds back: reasoning traces and any analysis never appear in any View.
- Privacy invariant: an agent's `temperament.md`, `self.md`, `lessons.md`, `peer-notes.md` and journal never appear in any *other* agent's View or system prompt.
- Prompts are values-neutral. Use the prompt text in this plan verbatim; do not add guidance about how agents should behave.
- Public profile limits: name 1–40 printable chars, unique case-insensitively; headline ≤ 254 chars, single line; introduction ≤ 600 chars.
- Defaults: `max_rounds = 12`, `quorum_slack = 1`, `stagnation_rounds = 2`, `view_entries = 15`, `max_reads = 3`.
- Consensus: a `call_consensus` passes when it has ≥ `n_agents - quorum_slack` supporters (caller counts) **and** at least one critique from a member other than the caller.
- `claude -p` isolation flags: `--system-prompt`, `--tools ""`, `--setting-sources ""`, `--no-session-persistence`, `--output-format json`, `--json-schema`, run from an empty temp directory outside `$HOME`; optional `--bare`.
- All git commits made by AISwarm must ignore the user's global git config for signing and hooks.
- Markdown files: never hard-wrap lines.

## Review Focus

1. **Hostile global git config** (commit signing on, a global hooks path with a failing hook): AISwarm commits must still succeed. Test in Task 2.
2. **Model wraps its JSON in a code fence or prose:** the action is still parsed. Test in Task 6.
3. **Agent-supplied workspace path escapes the workspace** (`../`, absolute, `.git/`): rejected as an invalid action, nothing written. Tests in Tasks 8 and 12.
4. **Crash or Ctrl-C mid-round:** no partial round on the board, workspace restored to the last completed round, resume continues from there. Test in Task 14.
5. **Two agents write the same file in the same round:** one wins, the other's write is rejected and a `write_rejected` entry is visible to all. Test in Task 14.

---

## File Structure

```
pyproject.toml
src/aiswarm/
  __init__.py
  config.py             # Config models, load_config, DEFAULT_CONFIG
  db.py                 # SQLite connect/migrations/transaction, now_iso
  gitrepo.py            # GitRepo: init, commit (isolated from user config), reset
  actions.py            # Action models, parse_action, extract_json, step_schema, simplify_schema
  board.py              # Entry, Board, SYSTEM
  events.py             # EventLog (events.jsonl), read_events
  workspace.py          # Workspace (versioned files), normalise_path, WriteConflict, InvalidPath
  profiles.py           # ProfileStore, PublicProfile, AgentRecord, ProfileError
  introduce.py          # introduce(): agent writes its first public profile
  prompts.py            # render() for prompt templates
  prompts/
    __init__.py
    agent_system.md
    introduction_system.md
    introduction_request.md
  consensus.py          # call_status, stop_decision, StopDecision
  view.py               # ViewInputs, collect_inputs, build_view, render_entry, make_who
  agent.py              # take_step, make_validator, make_reader, StepOutcome
  report.py             # write_report (answer.md, minority.md)
  runner.py             # Runner, RunResult
  cli.py                # typer app
  models/
    __init__.py
    base.py             # ChatResult, ModelError, ModelClient, RetryingClient
    fake.py             # FakeModel
    openai_compat.py    # OpenAICompatClient (oMLX)
    claude_cli.py       # ClaudeCliClient, build_argv, render_prompt, parse_output, leak_check
    factory.py          # make_client
tests/
  conftest.py
  test_config.py  test_storage.py  test_models_base.py  test_openai_compat.py
  test_claude_cli.py  test_actions.py  test_board.py  test_workspace.py
  test_profiles.py  test_consensus.py  test_view.py  test_agent.py
  test_report.py  test_runner.py  test_cli.py
  smoke/test_live.py
```

---

### Task 1: Project scaffold and configuration

**Files:**
- Create: `pyproject.toml` (via uv), `src/aiswarm/__init__.py`, `src/aiswarm/config.py`, `.gitignore`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `OpenAIBackend`, `ClaudeCliBackend`, `Backend`, `RunSettings`, `Config` (with `.db_path`), `load_config(path: Path) -> Config`, `DEFAULT_CONFIG: str`.

- [ ] **Step 1: Initialise the package with uv**

```bash
cd /Users/andrew/Develop/90_PLAYGROUND/AISwarm
uv init --package --name aiswarm --python 3.14 --build-backend uv --no-readme .
uv add openai pydantic typer
uv add --dev pytest pytest-asyncio
```

Then edit `pyproject.toml`: set the script entry and pytest options (keep the dependency versions uv wrote):

```toml
[project.scripts]
aiswarm = "aiswarm.cli:app"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["smoke: needs a live oMLX server or the claude CLI"]
addopts = "-m 'not smoke'"
```

Replace `src/aiswarm/__init__.py` with:

```python
"""AISwarm: a self-organising community of LLM agents."""
```

Create `.gitignore`:

```
.venv/
__pycache__/
*.pyc
/community/
/runs/
/aiswarm.toml
```

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:

```python
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiswarm.config import DEFAULT_CONFIG, ClaudeCliBackend, OpenAIBackend, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "aiswarm.toml"
    path.write_text(text)
    return path


def test_loads_backends_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, '''
[models.local]
kind = "openai"
model = "qwen3-8b"

[models.claude]
kind = "claude_cli"
'''))
    assert isinstance(cfg.models["local"], OpenAIBackend)
    assert cfg.models["local"].base_url == "http://localhost:8000/v1"
    assert isinstance(cfg.models["claude"], ClaudeCliBackend)
    assert cfg.models["claude"].model == "haiku"
    assert cfg.run.max_rounds == 12
    assert cfg.run.quorum_slack == 1
    assert cfg.run.stagnation_rounds == 2
    assert cfg.community_dir == (tmp_path / "community").resolve()
    assert cfg.runs_dir == (tmp_path / "runs").resolve()
    assert cfg.db_path == cfg.community_dir / "community.db"


def test_rejects_unknown_backend_kind(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.x]\nkind = "nope"\nmodel = "m"\n'))


def test_run_settings_override(tmp_path):
    cfg = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n[run]\nmax_rounds = 3\n'))
    assert cfg.run.max_rounds == 3


def test_default_config_is_valid(tmp_path):
    assert tomllib.loads(DEFAULT_CONFIG)
    cfg = load_config(write(tmp_path, DEFAULT_CONFIG))
    assert set(cfg.models) == {"local", "claude"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.config'`

- [ ] **Step 4: Implement `src/aiswarm/config.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore src/aiswarm/__init__.py src/aiswarm/config.py tests/test_config.py
git commit -m "feat: scaffold aiswarm package and config loading"
```

---

### Task 2: Storage — git repos and SQLite

**Files:**
- Create: `src/aiswarm/gitrepo.py`, `src/aiswarm/db.py`, `tests/conftest.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `GitRepo.init(path) -> GitRepo`, `GitRepo.commit(paths: list[str], message: str, author: str) -> str`, `GitRepo.head() -> str | None`, `GitRepo.root_commit() -> str`, `GitRepo.reset_hard(commit: str) -> None`, `GitError`.
- Produces: `connect(path: Path) -> sqlite3.Connection` (row_factory = `sqlite3.Row`, autocommit mode), `transaction(conn)` context manager, `MIGRATIONS: list[str]`, `now_iso() -> str`.
- Produces fixture `make_run(conn, run_id="r1", agent_ids=("a", "b"))` in `tests/conftest.py`.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import json

import pytest


@pytest.fixture
def make_run():
    def _make_run(conn, run_id="r1", agent_ids=("a", "b")):
        conn.execute(
            "INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'running', ?, 't')",
            (run_id, "goal", json.dumps(list(agent_ids))),
        )
        return run_id

    return _make_run
```

`tests/test_storage.py`:

```python
import subprocess

import pytest

from aiswarm.db import MIGRATIONS, connect, transaction
from aiswarm.gitrepo import GitRepo


def git_log(path, fmt):
    return subprocess.run(["git", "log", "-1", f"--format={fmt}"], cwd=path, capture_output=True, text=True).stdout.strip()


def test_head_is_none_before_first_commit(tmp_path):
    assert GitRepo.init(tmp_path / "r").head() is None


def test_commit_records_author_and_returns_sha(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a.txt").write_text("x")
    sha = repo.commit(["a.txt"], "add a", author="wren")
    assert len(sha) == 40
    assert repo.head() == sha
    assert git_log(repo.path, "%an <%ae>|%s") == "wren <wren@aiswarm.local>|add a"


def test_init_is_idempotent(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("1")
    sha = repo.commit(["a"], "m", author="x")
    assert GitRepo.init(tmp_path / "r").head() == sha


def test_commit_ignores_hostile_global_git_config(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[commit]\n\tgpgsign = true\n[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("1")
    assert repo.commit(["a"], "m", author="x")


def test_reset_hard_restores_and_cleans(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("v1")
    first = repo.commit(["a"], "v1", author="x")
    (repo.path / "a").write_text("v2")
    repo.commit(["a"], "v2", author="x")
    (repo.path / "stray").write_text("untracked")
    repo.reset_hard(first)
    assert (repo.path / "a").read_text() == "v1"
    assert not (repo.path / "stray").exists()
    assert repo.root_commit() == first


def test_connect_migrates_fresh_db(tmp_path):
    conn = connect(tmp_path / "c.db")
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"runs", "rounds", "entries", "files", "failures", "traces"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_connect_is_idempotent(tmp_path):
    connect(tmp_path / "c.db").close()
    conn = connect(tmp_path / "c.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_transaction_rolls_back_on_error(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    with pytest.raises(RuntimeError):
        with transaction(conn):
            make_run(conn)
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_transaction_commits(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    with transaction(conn):
        make_run(conn)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.db'`

- [ ] **Step 3: Implement `src/aiswarm/gitrepo.py`**

```python
"""Minimal git wrapper. Commits are isolated from the user's signing and hook config."""

import subprocess
from pathlib import Path

_ISOLATION = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def init(cls, path: Path) -> "GitRepo":
        path.mkdir(parents=True, exist_ok=True)
        repo = cls(path)
        if not (path / ".git").exists():
            repo._git("init", "-q", "-b", "main")
        return repo

    def _git(self, *args: str) -> str:
        result = subprocess.run(["git", *_ISOLATION, *args], cwd=self.path, capture_output=True, text=True)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout.strip()

    def commit(self, paths: list[str], message: str, author: str) -> str:
        email = f"{author}@aiswarm.local"
        if paths:
            self._git("add", "-A", "--", *paths)
        self._git(
            "-c", f"user.name={author}", "-c", f"user.email={email}",
            "commit", "-q", "--allow-empty", "-m", message, "--author", f"{author} <{email}>",
        )
        return self._git("rev-parse", "HEAD")

    def head(self) -> str | None:
        try:
            return self._git("rev-parse", "--verify", "-q", "HEAD")
        except GitError:
            return None

    def root_commit(self) -> str:
        return self._git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]

    def reset_hard(self, commit: str) -> None:
        self._git("reset", "-q", "--hard", commit)
        self._git("clean", "-q", "-f", "-d")
```

- [ ] **Step 4: Implement `src/aiswarm/db.py`**

```python
"""SQLite storage: connection, migrations, transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'closed')),
        ended_reason TEXT,
        agent_ids TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE rounds (
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        workspace_commit TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (run_id, round)
    );
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        author TEXT NOT NULL,
        type TEXT NOT NULL,
        to_ TEXT NOT NULL DEFAULT 'all',
        refs TEXT NOT NULL DEFAULT '[]',
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX entries_by_run ON entries (run_id, id);
    CREATE TABLE files (
        run_id TEXT NOT NULL REFERENCES runs(id),
        path TEXT NOT NULL,
        version INTEGER NOT NULL,
        author TEXT NOT NULL,
        git_commit TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, path, version)
    );
    CREATE TABLE failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        round INTEGER NOT NULL,
        agent_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE traces (
        entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
        run_id TEXT NOT NULL REFERENCES runs(id),
        agent_id TEXT NOT NULL,
        model TEXT,
        reasoning TEXT NOT NULL
    );
    """,
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/gitrepo.py src/aiswarm/db.py tests/conftest.py tests/test_storage.py
git commit -m "feat: add isolated git wrapper and SQLite storage"
```

---

### Task 3: Model interface, retries, and fake model

**Files:**
- Create: `src/aiswarm/models/__init__.py` (empty), `src/aiswarm/models/base.py`, `src/aiswarm/models/fake.py`
- Test: `tests/test_models_base.py`

**Interfaces:**
- Produces: `Message = dict[str, str]`; `ChatResult(text: str, reasoning: str | None = None, model: str | None = None, input_tokens: int | None = None)`; `ModelError`; `ModelClient` protocol with `context_window: int` and `async chat(system: str, messages: list[Message], json_schema: dict | None = None) -> ChatResult`; `RetryingClient(inner, attempts, base_delay=1.0, sleep=asyncio.sleep)`.
- Produces: `FakeModel(script=(), *, fail_times=0, context_window=32768, model="fake", reasoning=None)` with `.calls: list[tuple[str, list[Message], dict | None]]`. `script` is a list of strings/callables (consumed in order, then `{"action": {"type": "pass"}}`) or a single callable `(system, messages) -> str` used for every call.

- [ ] **Step 1: Write the failing tests**

`tests/test_models_base.py`:

```python
import json

import pytest

from aiswarm.models.base import ModelError, RetryingClient
from aiswarm.models.fake import FakeModel


async def test_fake_returns_script_in_order_then_pass():
    model = FakeModel(["a", lambda system, messages: f"echo:{messages[-1]['content']}"])
    assert (await model.chat("sys", [{"role": "user", "content": "x"}])).text == "a"
    assert (await model.chat("sys", [{"role": "user", "content": "y"}])).text == "echo:y"
    assert json.loads((await model.chat("sys", [])).text) == {"action": {"type": "pass"}}
    assert model.calls[0][0] == "sys"


async def test_fake_with_single_callable_answers_every_call():
    model = FakeModel(lambda system, messages: "same", reasoning="why")
    first = await model.chat("s", [])
    second = await model.chat("s", [])
    assert first.text == second.text == "same"
    assert first.reasoning == "why"


async def test_fake_can_fail():
    model = FakeModel(["ok"], fail_times=1)
    with pytest.raises(ModelError):
        await model.chat("s", [])
    assert (await model.chat("s", [])).text == "ok"


async def test_retrying_client_retries_with_backoff_then_succeeds():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    client = RetryingClient(FakeModel(["ok"], fail_times=2), attempts=3, base_delay=1.0, sleep=fake_sleep)
    assert (await client.chat("s", [])).text == "ok"
    assert sleeps == [1.0, 2.0]
    assert client.context_window == 32768


async def test_retrying_client_gives_up():
    async def fake_sleep(seconds):
        pass

    client = RetryingClient(FakeModel(fail_times=5), attempts=3, sleep=fake_sleep)
    with pytest.raises(ModelError):
        await client.chat("s", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.models'`

- [ ] **Step 3: Implement `src/aiswarm/models/base.py`**

```python
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
```

- [ ] **Step 4: Implement `src/aiswarm/models/fake.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models_base.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/models tests/test_models_base.py
git commit -m "feat: add model client interface, retries, and fake model"
```

---

### Task 4: oMLX backend (OpenAI-compatible)

**Files:**
- Create: `src/aiswarm/models/openai_compat.py`
- Test: `tests/test_openai_compat.py`

**Interfaces:**
- Consumes: `OpenAIBackend` (Task 1), `ChatResult`, `ModelError` (Task 3).
- Produces: `OpenAICompatClient(backend: OpenAIBackend, client: AsyncOpenAI | None = None)` with `context_window`, `async chat(...)`, `async list_models() -> list[str]`. Reasoning is read from the message's `reasoning_content` or `reasoning` extra field.

- [ ] **Step 1: Write the failing tests**

`tests/test_openai_compat.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aiswarm.config import OpenAIBackend
from aiswarm.models.base import ModelError
from aiswarm.models.openai_compat import OpenAICompatClient

CHAT_REPLY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "qwen-test",
    "choices": [{
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": '{"action": {"type": "pass"}}', "reasoning_content": "thinking..."},
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
}
MODELS_REPLY = {"object": "list", "data": [{"id": "qwen-test", "object": "model", "created": 0, "owned_by": "omlx"}]}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, body))
        self._send(CHAT_REPLY)

    def do_GET(self):
        self._send(MODELS_REPLY)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def backend(srv, **kwargs):
    return OpenAIBackend(kind="openai", model="qwen-test", base_url=f"http://127.0.0.1:{srv.server_port}/v1", **kwargs)


async def test_chat_sends_system_messages_and_schema(server):
    client = OpenAICompatClient(backend(server))
    result = await client.chat("SYS", [{"role": "user", "content": "hi"}], json_schema={"type": "object"})
    path, body = server.requests[0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "qwen-test"
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert result.text == '{"action": {"type": "pass"}}'
    assert result.reasoning == "thinking..."
    assert result.model == "qwen-test"
    assert result.input_tokens == 10


async def test_schema_omitted_when_disabled(server):
    client = OpenAICompatClient(backend(server, json_schema=False))
    await client.chat("S", [], json_schema={"type": "object"})
    assert "response_format" not in server.requests[0][1]


async def test_list_models(server):
    assert await OpenAICompatClient(backend(server)).list_models() == ["qwen-test"]


async def test_unreachable_server_raises_model_error():
    client = OpenAICompatClient(OpenAIBackend(kind="openai", model="m", base_url="http://127.0.0.1:9/v1", timeout_s=2))
    with pytest.raises(ModelError):
        await client.chat("S", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.models.openai_compat'`

- [ ] **Step 3: Implement `src/aiswarm/models/openai_compat.py`**

```python
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
        if json_schema is not None and self.backend.json_schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "reply", "schema": json_schema}}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_openai_compat.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/models/openai_compat.py tests/test_openai_compat.py
git commit -m "feat: add OpenAI-compatible backend for oMLX"
```

---

### Task 5: Claude backend (`claude -p`), leak check, and client factory

**Files:**
- Create: `src/aiswarm/models/claude_cli.py`, `src/aiswarm/models/factory.py`
- Test: `tests/test_claude_cli.py`

**Interfaces:**
- Consumes: `ClaudeCliBackend`, `OpenAIBackend`, `Backend` (Task 1); `ChatResult`, `ModelError`, `RetryingClient` (Task 3); `OpenAICompatClient` (Task 4).
- Produces: `build_argv(backend, system, json_schema) -> list[str]`, `render_prompt(messages) -> str`, `parse_output(stdout) -> ChatResult`, `ClaudeCliClient(backend, workdir: Path, runner=_run_subprocess)`, `async leak_check(client: ClaudeCliClient) -> tuple[bool, int | None]`.
- Produces: `make_client(backend: Backend, retries: int) -> RetryingClient`.
- Runner signature (injectable for tests): `async (argv: list[str], stdin_text: str, timeout: float, cwd: Path) -> tuple[int, str, str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_cli.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.models.claude_cli'`

- [ ] **Step 3: Implement `src/aiswarm/models/claude_cli.py`**

```python
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
```

- [ ] **Step 4: Implement `src/aiswarm/models/factory.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_cli.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/models/claude_cli.py src/aiswarm/models/factory.py tests/test_claude_cli.py
git commit -m "feat: add isolated claude -p backend, leak check, and client factory"
```

---

### Task 6: Actions

**Files:**
- Create: `src/aiswarm/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Produces action models (all `extra="forbid"`, each with a `type` literal): `Propose(answer_summary, method)`, `Critique(target: int, about: "answer"|"method", points)`, `Question(target, text)`, `Adopt(target, reason)`, `Refine(target, change, reason)`, `WriteFile(path, base_version: int >= 0, content, note)`, `Credit(agent_id, contribution)`, `CallConsensus(synthesis, refs: list[int], credits: list[Credit])`, `Vote(target, stance: "support"|"object", reason)`, `Pass`, `ReadFile(path)`, `ListFiles`, `ReadEntry(id: int)`. Construct a pass as `Pass(type="pass")`.
- Produces: `READ_ACTIONS = (ReadFile, ListFiles, ReadEntry)`, `ActionError(ValueError)`, `extract_json(text) -> Any`, `parse_action(text)`, `target_refs(action) -> list[int]`, `simplify_schema(schema) -> dict`, `step_schema() -> dict` (root object with required `action`).

- [ ] **Step 1: Write the failing tests**

`tests/test_actions.py`:

```python
import json

import pytest

from aiswarm.actions import (
    ActionError, CallConsensus, Critique, ListFiles, Pass, Propose, extract_json, parse_action,
    simplify_schema, step_schema, target_refs,
)


def test_parses_enveloped_action():
    action = parse_action('{"action": {"type": "propose", "answer_summary": "A", "method": "M"}}')
    assert isinstance(action, Propose) and action.method == "M"


def test_accepts_bare_action_object():
    assert isinstance(parse_action('{"type": "pass"}'), Pass)


def test_strips_code_fence():
    assert isinstance(parse_action('```json\n{"action": {"type": "pass"}}\n```'), Pass)


def test_extracts_json_after_prose():
    assert isinstance(parse_action('Sure! {"action": {"type": "list_files"}} Hope that helps.'), ListFiles)


def test_extract_json_without_object_raises():
    with pytest.raises(ActionError):
        extract_json("no json here")


@pytest.mark.parametrize("text", [
    '{"action": {"type": "dance"}}',
    '{"action": {"type": "propose", "answer_summary": "A"}}',
    '{"action": {"type": "pass", "extra": 1}}',
    '{"action": {"type": "critique", "target": 1, "about": "answer", "points": ""}}',
    '{"action": {"type": "call_consensus", "synthesis": "S", "refs": []}}',
    '{"action": {"type": "write_file", "path": "a", "base_version": -1, "content": "", "note": "n"}}',
])
def test_rejects_invalid_actions(text):
    with pytest.raises(ActionError):
        parse_action(text)


def test_error_message_names_the_field():
    with pytest.raises(ActionError, match="method"):
        parse_action('{"action": {"type": "propose", "answer_summary": "A"}}')


def test_call_consensus_accepts_empty_credits():
    action = parse_action('{"action": {"type": "call_consensus", "synthesis": "S", "refs": [2], "credits": []}}')
    assert isinstance(action, CallConsensus) and action.credits == []


def test_target_refs():
    assert target_refs(Critique(type="critique", target=5, about="answer", points="p")) == [5]
    assert target_refs(CallConsensus(type="call_consensus", synthesis="s", refs=[2, 3], credits=[])) == [2, 3]
    assert target_refs(Propose(type="propose", answer_summary="a", method="m")) == []


def test_step_schema_is_simplified_for_strict_backends():
    schema = step_schema()
    text = json.dumps(schema)
    for word in ('"oneOf"', '"discriminator"', '"const"', '"minLength"', '"title"'):
        assert word not in text
    assert schema["required"] == ["action"]
    assert "anyOf" in json.dumps(schema["properties"]["action"])


def test_simplify_schema_keeps_property_names():
    schema = {"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert simplify_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.actions'`

- [ ] **Step 3: Implement `src/aiswarm/actions.py`**

```python
"""Agent actions: the only things an agent can do in a step."""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Text = Annotated[str, Field(min_length=1)]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Propose(_Action):
    type: Literal["propose"]
    answer_summary: Text
    method: Text


class Critique(_Action):
    type: Literal["critique"]
    target: int
    about: Literal["answer", "method"]
    points: Text


class Question(_Action):
    type: Literal["question"]
    target: int
    text: Text


class Adopt(_Action):
    type: Literal["adopt"]
    target: int
    reason: Text


class Refine(_Action):
    type: Literal["refine"]
    target: int
    change: Text
    reason: Text


class WriteFile(_Action):
    type: Literal["write_file"]
    path: Text
    base_version: int = Field(ge=0)
    content: str
    note: Text


class Credit(_Action):
    agent_id: Text
    contribution: Text


class CallConsensus(_Action):
    type: Literal["call_consensus"]
    synthesis: Text
    refs: list[int]
    credits: list[Credit]


class Vote(_Action):
    type: Literal["vote"]
    target: int
    stance: Literal["support", "object"]
    reason: Text


class Pass(_Action):
    type: Literal["pass"]


class ReadFile(_Action):
    type: Literal["read_file"]
    path: Text


class ListFiles(_Action):
    type: Literal["list_files"]


class ReadEntry(_Action):
    type: Literal["read_entry"]
    id: int


PostingAction = Propose | Critique | Question | Adopt | Refine | WriteFile | CallConsensus | Vote | Pass
READ_ACTIONS = (ReadFile, ListFiles, ReadEntry)

StepAction = Annotated[
    Propose | Critique | Question | Adopt | Refine | WriteFile | CallConsensus | Vote | Pass
    | ReadFile | ListFiles | ReadEntry,
    Field(discriminator="type"),
]


class _Envelope(_Action):
    action: StepAction


class ActionError(ValueError):
    """The model's reply was not a valid action."""


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from a reply, tolerating a code fence or surrounding prose."""
    raw = text.strip()
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start == -1:
        raise ActionError("No JSON object found in the reply.")
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise ActionError(f"The reply is not valid JSON: {exc.msg}.") from exc
    return value


def parse_action(text: str) -> Any:
    data = extract_json(text)
    if isinstance(data, dict) and "action" not in data and "type" in data:
        data = {"action": data}
    try:
        return _Envelope.model_validate(data).action
    except ValidationError as exc:
        raise ActionError(summarise_validation(exc)) from exc


def summarise_validation(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()[:5]
    )


def target_refs(action: Any) -> list[int]:
    if isinstance(action, CallConsensus):
        return list(action.refs)
    target = getattr(action, "target", None)
    return [target] if target is not None else []


_DROP = {
    "discriminator", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minItems", "maxItems", "title", "default",
}


def simplify_schema(node: Any) -> Any:
    """Reduce a pydantic JSON schema to the subset strict structured-output backends accept.

    Pydantic still validates the full constraints after parsing.
    """
    if isinstance(node, list):
        return [simplify_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "$defs"):
            out[key] = {name: simplify_schema(sub) for name, sub in value.items()}
        elif key in _DROP:
            continue
        elif key == "oneOf":
            out["anyOf"] = simplify_schema(value)
        elif key == "const":
            out["enum"] = [value]
        else:
            out[key] = simplify_schema(value)
    return out


def step_schema() -> dict[str, Any]:
    return simplify_schema(_Envelope.model_json_schema())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_actions.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/actions.py tests/test_actions.py
git commit -m "feat: add agent action models, tolerant parsing, and simplified schema"
```

---

### Task 7: Board and event log

**Files:**
- Create: `src/aiswarm/board.py`, `src/aiswarm/events.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Consumes: `connect`, `now_iso` (Task 2); `make_run` fixture.
- Produces: `SYSTEM = "system"`; `Entry(id, run_id, round, author, type, payload: dict, refs: list[int], to: str, created_at: str)` (frozen dataclass); `Board(conn, run_id)` with `append(round, author, type, payload, refs=(), to="all") -> Entry`, `entries() -> list[Entry]` (ordered by id), `get(entry_id) -> Entry | None` (scoped to the run). `append` does not open its own transaction.
- Produces: `EventLog(path: Path, sink: Callable[[dict], None] | None = None)` with `emit(type: str, **data) -> dict`; `read_events(path) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_board.py`:

```python
from aiswarm.board import SYSTEM, Board
from aiswarm.db import connect
from aiswarm.events import EventLog, read_events


def test_append_and_list_in_order(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    board = Board(conn, "r1")
    goal = board.append(0, SYSTEM, "goal", {"goal": "G"})
    prop = board.append(1, "a", "propose", {"answer_summary": "A", "method": "M"})
    crit = board.append(2, "b", "critique", {"target": prop.id, "about": "method", "points": "p"}, refs=[prop.id])
    assert [e.id for e in board.entries()] == [goal.id, prop.id, crit.id]
    assert board.get(crit.id).refs == [prop.id]
    assert board.get(crit.id).payload["points"] == "p"
    assert board.get(crit.id).to == "all"


def test_get_is_scoped_to_run(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1")
    make_run(conn, "r2")
    other = Board(conn, "r2").append(0, SYSTEM, "goal", {"goal": "x"})
    assert Board(conn, "r1").get(other.id) is None
    assert Board(conn, "r1").entries() == []


def test_event_log_appends_jsonl_and_calls_sink(tmp_path):
    seen = []
    log = EventLog(tmp_path / "run" / "events.jsonl", sink=seen.append)
    log.emit("round_started", round=1)
    log.emit("action_posted", agent="a", type="propose")
    events = read_events(tmp_path / "run" / "events.jsonl")
    assert [e["type"] for e in events] == ["round_started", "action_posted"]
    assert events[1]["agent"] == "a" and "ts" in events[1]
    assert seen == events
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.board'`

- [ ] **Step 3: Implement `src/aiswarm/board.py`**

```python
"""The board: an append-only log of typed, attributed entries for one run."""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aiswarm.db import now_iso

SYSTEM = "system"


@dataclass(frozen=True)
class Entry:
    id: int
    run_id: str
    round: int
    author: str
    type: str
    payload: dict[str, Any]
    refs: list[int]
    to: str
    created_at: str


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"], run_id=row["run_id"], round=row["round"], author=row["author"], type=row["type"],
        payload=json.loads(row["payload"]), refs=json.loads(row["refs"]), to=row["to_"],
        created_at=row["created_at"],
    )


class Board:
    def __init__(self, conn: sqlite3.Connection, run_id: str):
        self._conn = conn
        self.run_id = run_id

    def append(
        self, round: int, author: str, type: str, payload: dict[str, Any],
        refs: Sequence[int] = (), to: str = "all",
    ) -> Entry:
        created = now_iso()
        cursor = self._conn.execute(
            "INSERT INTO entries (run_id, round, author, type, to_, refs, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, round, author, type, to, json.dumps(list(refs)),
             json.dumps(payload, ensure_ascii=False), created),
        )
        return Entry(cursor.lastrowid, self.run_id, round, author, type, payload, list(refs), to, created)

    def entries(self) -> list[Entry]:
        rows = self._conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (self.run_id,))
        return [_entry(row) for row in rows]

    def get(self, entry_id: int) -> Entry | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE run_id = ? AND id = ?", (self.run_id, entry_id)
        ).fetchone()
        return _entry(row) if row else None
```

- [ ] **Step 4: Implement `src/aiswarm/events.py`**

```python
"""Live event stream for a run: one JSON object per line, for read-only consumers."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiswarm.db import now_iso


class EventLog:
    def __init__(self, path: Path, sink: Callable[[dict[str, Any]], None] | None = None):
        self.path = path
        self._sink = sink
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, **data: Any) -> dict[str, Any]:
        event = {"ts": now_iso(), "type": type, **data}
        with self.path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self._sink:
            self._sink(event)
        return event


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/board.py src/aiswarm/events.py tests/test_board.py
git commit -m "feat: add append-only board and event log"
```

---

### Task 8: Workspace

**Files:**
- Create: `src/aiswarm/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `GitRepo` (Task 2), `now_iso`, `make_run` fixture.
- Produces: `InvalidPath(ValueError)`, `WriteConflict(Exception)` with `.path` and `.current_version`, `normalise_path(path: str) -> str`, `Workspace(conn, run_id, root: Path)` with `current_version(path) -> int`, `list_files() -> list[tuple[str, int]]`, `read(path) -> tuple[str, int]` (raises `FileNotFoundError`), `write(path, base_version, content, author, note) -> int` (new version), `head() -> str`, `initial_commit() -> str`, `reset_to(commit: str) -> None`. The constructor guarantees at least one commit exists.

- [ ] **Step 1: Write the failing tests**

`tests/test_workspace.py`:

```python
import subprocess

import pytest

from aiswarm.db import connect
from aiswarm.workspace import InvalidPath, Workspace, WriteConflict, normalise_path


@pytest.fixture
def ws(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    return Workspace(conn, "r1", tmp_path / "ws")


def test_new_workspace_has_an_initial_commit(ws):
    assert ws.head() == ws.initial_commit()
    assert ws.list_files() == []


def test_write_new_file_then_read(ws):
    assert ws.write("draft.md", 0, "hello", author="a", note="start draft") == 1
    assert ws.read("draft.md") == ("hello", 1)
    assert ws.list_files() == [("draft.md", 1)]


def test_successive_versions_and_git_author(ws):
    ws.write("draft.md", 0, "v1", author="a", note="n1")
    assert ws.write("draft.md", 1, "v2", author="b", note="tighten") == 2
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=ws.root, capture_output=True, text=True)
    assert log.stdout.strip() == "b|tighten"


def test_stale_base_version_conflicts(ws):
    ws.write("draft.md", 0, "v1", author="a", note="n")
    with pytest.raises(WriteConflict) as info:
        ws.write("draft.md", 0, "mine", author="b", note="n")
    assert info.value.current_version == 1
    assert ws.read("draft.md") == ("v1", 1)


def test_read_missing_file(ws):
    with pytest.raises(FileNotFoundError):
        ws.read("nope.md")


@pytest.mark.parametrize("bad", ["../escape.md", "/etc/passwd", ".git/config", "", "   ", ".", "a\\b.md", "x" * 201])
def test_invalid_paths_are_rejected(ws, bad):
    with pytest.raises(InvalidPath):
        ws.write(bad, 0, "x", author="a", note="n")
    assert ws.list_files() == []


def test_normalise_path():
    assert normalise_path("notes/./a.md") == "notes/a.md"
    assert normalise_path("agents/b/scratch.md") == "agents/b/scratch.md"


def test_reset_to_restores_files(ws):
    ws.write("a.md", 0, "one", author="a", note="n")
    checkpoint = ws.head()
    ws.write("a.md", 1, "two", author="a", note="n")
    (ws.root / "stray.md").write_text("junk")
    ws.reset_to(checkpoint)
    assert (ws.root / "a.md").read_text() == "one"
    assert not (ws.root / "stray.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.workspace'`

- [ ] **Step 3: Implement `src/aiswarm/workspace.py`**

```python
"""Per-run shared workspace: versioned files in a git repo, writes serialised by the Runner."""

import sqlite3
from pathlib import Path, PurePosixPath

from aiswarm.db import now_iso
from aiswarm.gitrepo import GitRepo

MAX_PATH = 200


class InvalidPath(ValueError):
    pass


class WriteConflict(Exception):
    def __init__(self, path: str, current_version: int):
        super().__init__(f"{path} is now at version {current_version}")
        self.path = path
        self.current_version = current_version


def normalise_path(path: str) -> str:
    stripped = path.strip()
    invalid = InvalidPath(f"Invalid workspace path: {path!r}")
    if not stripped or len(stripped) > MAX_PATH or "\\" in stripped or "\x00" in stripped:
        raise invalid
    pure = PurePosixPath(stripped)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or pure.parts[0] == ".git":
        raise invalid
    return str(pure)


class Workspace:
    def __init__(self, conn: sqlite3.Connection, run_id: str, root: Path):
        self._conn = conn
        self.run_id = run_id
        self.root = root
        self._repo = GitRepo.init(root)
        if self._repo.head() is None:
            self._repo.commit([], "workspace created", author="system")

    def current_version(self, path: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM files WHERE run_id = ? AND path = ?", (self.run_id, path)
        ).fetchone()
        return row[0] or 0

    def list_files(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT path, MAX(version) FROM files WHERE run_id = ? GROUP BY path ORDER BY path", (self.run_id,)
        )
        return [(row[0], row[1]) for row in rows]

    def read(self, path: str) -> tuple[str, int]:
        rel = normalise_path(path)
        version = self.current_version(rel)
        if version == 0:
            raise FileNotFoundError(rel)
        return (self.root / rel).read_text(), version

    def write(self, path: str, base_version: int, content: str, author: str, note: str) -> int:
        rel = normalise_path(path)
        current = self.current_version(rel)
        if base_version != current:
            raise WriteConflict(rel, current)
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        version = current + 1
        sha = self._repo.commit([rel], f"{note}\n\n{rel} v{version}", author=author)
        self._conn.execute(
            "INSERT INTO files (run_id, path, version, author, git_commit, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, rel, version, author, sha, now_iso()),
        )
        return version

    def head(self) -> str:
        head = self._repo.head()
        assert head is not None
        return head

    def initial_commit(self) -> str:
        return self._repo.root_commit()

    def reset_to(self, commit: str) -> None:
        self._repo.reset_hard(commit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/workspace.py tests/test_workspace.py
git commit -m "feat: add versioned git-backed run workspace"
```

---

### Task 9: Agent profiles and first introduction

**Files:**
- Create: `src/aiswarm/profiles.py`, `src/aiswarm/prompts.py`, `src/aiswarm/prompts/__init__.py` (empty), `src/aiswarm/prompts/introduction_system.md`, `src/aiswarm/prompts/introduction_request.md`, `src/aiswarm/introduce.py`
- Modify: `tests/conftest.py` (add `community` fixture)
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `GitRepo`, `now_iso`, `connect` (Task 2); `FakeModel`, `ModelClient` (Task 3); `extract_json`, `simplify_schema`, `ActionError`, `summarise_validation` (Task 6).
- Produces: constants `NAME_MAX = 40`, `HEADLINE_MAX = 254`, `INTRO_MAX = 600`, `PRIVATE_FILES = ("temperament.md", "self.md", "lessons.md", "peer-notes.md")`, `PRIVATE_MAX = 4000`; `ProfileError(ValueError)`; `PublicProfile(name, headline, introduction, former_names: list[str])`; `AgentRecord(id, model, control: bool, created_at)`.
- Produces: `ProfileStore(community_dir)` with `ids() -> list[str]`, `create(agent_id, model, temperament, control=False) -> AgentRecord`, `record(agent_id) -> AgentRecord`, `private(agent_id) -> dict[str, str]`, `write_private(agent_id, filename, content) -> None`, `public(agent_id) -> PublicProfile | None`, `set_public(agent_id, name, headline, introduction) -> PublicProfile`. The store writes `community/.gitignore` ignoring the database.
- Produces: `render(name: str, **values) -> str` (string.Template substitution of `$names`).
- Produces: `async introduce(store, agent_id, client) -> PublicProfile` (one retry on invalid reply, then `ProfileError`).
- Produces fixture `community(tmp_path) -> (ProfileStore, sqlite3.Connection)` with agents `a`/`b`/`c` named `Alpha`/`Bea`/`Cyd`, backend name `"fake"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/conftest.py`:

```python
from aiswarm.db import connect
from aiswarm.profiles import ProfileStore


@pytest.fixture
def community(tmp_path):
    store = ProfileStore(tmp_path / "community")
    for agent_id, name in [("a", "Alpha"), ("b", "Bea"), ("c", "Cyd")]:
        store.create(agent_id, "fake", f"Temperament of {agent_id}.")
        store.set_public(agent_id, name, f"{name} headline", f"I am {name}.")
    conn = connect(tmp_path / "community" / "community.db")
    return store, conn
```

`tests/test_profiles.py`:

```python
import json
import subprocess

import pytest

from aiswarm.introduce import introduce
from aiswarm.models.fake import FakeModel
from aiswarm.profiles import PRIVATE_MAX, ProfileError, ProfileStore


@pytest.fixture
def store(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("a", "local", "Curious and blunt.")
    s.create("b", "claude", "Patient.")
    return s


def test_create_writes_private_files_and_commits(store):
    assert store.ids() == ["a", "b"]
    private = store.private("a")
    assert private["temperament.md"].strip() == "Curious and blunt."
    assert private["self.md"] == private["lessons.md"] == private["peer-notes.md"] == ""
    assert store.record("a").model == "local"
    assert store.record("a").control is False
    log = subprocess.run(["git", "log", "--format=%s"], cwd=store.root, capture_output=True, text=True).stdout
    assert "create agent a" in log
    assert "community.db" in (store.root / ".gitignore").read_text()


@pytest.mark.parametrize("agent_id", ["A", "1x", "has space", "x" * 25, ""])
def test_create_rejects_bad_ids(store, agent_id):
    with pytest.raises(ProfileError):
        store.create(agent_id, "local", "t")


def test_create_rejects_duplicates_and_empty_temperament(store):
    with pytest.raises(ProfileError):
        store.create("a", "local", "again")
    with pytest.raises(ProfileError):
        store.create("c", "local", "   ")


def test_set_public_happy_path(store):
    profile = store.set_public("a", "Wren", "Asks the awkward question.", "I like to start from first principles.")
    assert store.public("a") == profile
    assert profile.former_names == []


@pytest.mark.parametrize("name,headline,intro", [
    ("", "h", "i"),
    ("x" * 41, "h", "i"),
    ("Wren", "x" * 255, "i"),
    ("Wren", "two\nlines", "i"),
    ("Wren", "h", ""),
    ("Wren", "h", "x" * 601),
    ("bad\tname", "h", "i"),
])
def test_set_public_enforces_limits(store, name, headline, intro):
    with pytest.raises(ProfileError):
        store.set_public("a", name, headline, intro)
    assert store.public("a") is None


def test_names_are_unique_case_insensitively(store):
    store.set_public("a", "Wren", "h", "i")
    with pytest.raises(ProfileError, match="taken"):
        store.set_public("b", "wREN", "h", "i")


def test_rename_keeps_public_history(store):
    store.set_public("a", "Wren", "h", "i")
    store.set_public("a", "Kestrel", "h2", "i2")
    assert store.public("a").former_names == ["Wren"]
    store.set_public("a", "Kestrel", "h3", "i3")
    assert store.public("a").former_names == ["Wren"]


def test_unicode_name_round_trips(store):
    store.set_public("a", "Zoë 🌿", "Ça va", "Héllo")
    assert store.public("a").name == "Zoë 🌿"


def test_write_private_validates_and_commits(store):
    store.write_private("a", "lessons.md", "Start small.")
    assert store.private("a")["lessons.md"] == "Start small."
    with pytest.raises(ProfileError):
        store.write_private("a", "lessons.md", "x" * (PRIVATE_MAX + 1))
    with pytest.raises(ProfileError):
        store.write_private("a", "journal.md", "nope")
    assert store.private("a")["lessons.md"] == "Start small."


def intro(name, headline="A headline.", introduction="Hello, community."):
    return json.dumps({"name": name, "headline": headline, "introduction": introduction})


async def test_introduce_sets_profile(store):
    model = FakeModel([intro("Wren")])
    profile = await introduce(store, "a", model)
    assert profile.name == "Wren"
    system, messages, schema = model.calls[0]
    assert "Curious and blunt." in system
    assert "introduce yourself" in messages[0]["content"].lower()
    assert schema["required"] == ["name", "headline", "introduction"]


async def test_introduce_retries_once_with_the_error(store):
    model = FakeModel([intro("Wren", headline="x" * 300), intro("Wren")])
    assert (await introduce(store, "a", model)).name == "Wren"
    assert "could not be accepted" in model.calls[1][1][-1]["content"]


async def test_introduce_gives_up_after_two_invalid_replies(store):
    with pytest.raises(ProfileError):
        await introduce(store, "a", FakeModel(["garbage", "more garbage"]))
    assert store.public("a") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.introduce'`

- [ ] **Step 3: Implement `src/aiswarm/profiles.py`**

```python
"""Persistent agent profiles: private working files and a public self-presentation."""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from aiswarm.actions import summarise_validation
from aiswarm.db import now_iso
from aiswarm.gitrepo import GitRepo

AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
NAME_MAX = 40
HEADLINE_MAX = 254
INTRO_MAX = 600
PRIVATE_FILES = ("temperament.md", "self.md", "lessons.md", "peer-notes.md")
PRIVATE_MAX = 4000
_GITIGNORE = "community.db\ncommunity.db-*\n"


class ProfileError(ValueError):
    pass


class PublicProfile(BaseModel):
    name: str
    headline: str
    introduction: str
    former_names: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= NAME_MAX or not value.isprintable():
            raise ValueError(f"name must be 1-{NAME_MAX} printable characters")
        return value

    @field_validator("headline")
    @classmethod
    def _headline(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= HEADLINE_MAX or not value.isprintable():
            raise ValueError(f"headline must be a single line of 1-{HEADLINE_MAX} characters")
        return value

    @field_validator("introduction")
    @classmethod
    def _introduction(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= INTRO_MAX:
            raise ValueError(f"introduction must be 1-{INTRO_MAX} characters")
        return value


class AgentRecord(BaseModel):
    id: str
    model: str
    control: bool = False
    created_at: str


def _toml(data: dict[str, Any]) -> str:
    def value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(value(item) for item in v) + "]"
        return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings

    return "".join(f"{key} = {value(v)}\n" for key, v in data.items())


def _check_private(filename: str, content: str) -> None:
    if filename not in PRIVATE_FILES:
        raise ProfileError(f"{filename} is not a private profile file")
    if len(content) > PRIVATE_MAX:
        raise ProfileError(f"{filename} exceeds {PRIVATE_MAX} characters")
    if filename == "temperament.md" and not content.strip():
        raise ProfileError("temperament must not be empty")


class ProfileStore:
    def __init__(self, community_dir: Path):
        self.root = community_dir
        self.agents_dir = community_dir / "agents"
        self._repo = GitRepo.init(community_dir)
        gitignore = community_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE)
            self._repo.commit([".gitignore"], "ignore database", author="aiswarm")

    def _dir(self, agent_id: str) -> Path:
        return self.agents_dir / agent_id

    def ids(self) -> list[str]:
        if not self.agents_dir.exists():
            return []
        return sorted(p.name for p in self.agents_dir.iterdir() if (p / "agent.toml").exists())

    def create(self, agent_id: str, model: str, temperament: str, control: bool = False) -> AgentRecord:
        if not AGENT_ID.match(agent_id):
            raise ProfileError("agent id must be lowercase letters, digits or '-', starting with a letter, max 24")
        if agent_id in self.ids():
            raise ProfileError(f"agent {agent_id!r} already exists")
        _check_private("temperament.md", temperament)
        directory = self._dir(agent_id)
        directory.mkdir(parents=True)
        record = AgentRecord(id=agent_id, model=model, control=control, created_at=now_iso())
        (directory / "agent.toml").write_text(_toml(record.model_dump()))
        (directory / "temperament.md").write_text(temperament.strip() + "\n")
        for filename in PRIVATE_FILES[1:]:
            (directory / filename).write_text("")
        self._repo.commit([f"agents/{agent_id}"], f"create agent {agent_id}", author="aiswarm")
        return record

    def record(self, agent_id: str) -> AgentRecord:
        return AgentRecord.model_validate(tomllib.loads((self._dir(agent_id) / "agent.toml").read_text()))

    def private(self, agent_id: str) -> dict[str, str]:
        return {name: (self._dir(agent_id) / name).read_text() for name in PRIVATE_FILES}

    def write_private(self, agent_id: str, filename: str, content: str) -> None:
        _check_private(filename, content)
        (self._dir(agent_id) / filename).write_text(content)
        self._repo.commit([f"agents/{agent_id}/{filename}"], f"{agent_id} updated {filename}", author=agent_id)

    def public(self, agent_id: str) -> PublicProfile | None:
        path = self._dir(agent_id) / "public.toml"
        if not path.exists():
            return None
        return PublicProfile.model_validate(tomllib.loads(path.read_text()))

    def set_public(self, agent_id: str, name: str, headline: str, introduction: str) -> PublicProfile:
        current = self.public(agent_id)
        former = list(current.former_names) if current else []
        try:
            candidate = PublicProfile(name=name, headline=headline, introduction=introduction, former_names=former)
        except ValidationError as exc:
            raise ProfileError(summarise_validation(exc)) from exc
        for other in self.ids():
            other_profile = self.public(other) if other != agent_id else None
            if other_profile and other_profile.name.casefold() == candidate.name.casefold():
                raise ProfileError(f"the name {candidate.name!r} is taken")
        if current and current.name != candidate.name:
            candidate.former_names.append(current.name)
        (self._dir(agent_id) / "public.toml").write_text(_toml(candidate.model_dump()))
        self._repo.commit([f"agents/{agent_id}/public.toml"], f"{agent_id} updated public profile", author=agent_id)
        return candidate
```

- [ ] **Step 4: Implement `src/aiswarm/prompts.py` and the introduction templates**

`src/aiswarm/prompts.py`:

```python
"""Prompt templates. Text lives in prompts/*.md; placeholders use $name."""

from importlib.resources import files
from string import Template


def render(name: str, **values: object) -> str:
    text = files("aiswarm.prompts").joinpath(name).read_text()
    return Template(text).substitute(values)
```

`src/aiswarm/prompts/introduction_system.md`:

```
You are a new member joining a small community of agents who work together on shared goals.

Your temperament:
$temperament
```

`src/aiswarm/prompts/introduction_request.md`:

```
Before your first piece of work, introduce yourself to the community. Other members will read this before working with you.

Reply with JSON only: {"name": "...", "headline": "...", "introduction": "..."}

- name: choose the name the community will know you by ($name_max characters at most).
- headline: one line other members see beside your name ($headline_max characters at most).
- introduction: introduce yourself to the community. Write it however you like ($intro_max characters at most).
```

- [ ] **Step 5: Implement `src/aiswarm/introduce.py`**

```python
"""An agent's first introduction: it writes its own public profile."""

from pydantic import BaseModel, ConfigDict, ValidationError

from aiswarm.actions import ActionError, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient
from aiswarm.profiles import HEADLINE_MAX, INTRO_MAX, NAME_MAX, ProfileError, ProfileStore, PublicProfile
from aiswarm.prompts import render


class _IntroReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    headline: str
    introduction: str


INTRO_SCHEMA = simplify_schema(_IntroReply.model_json_schema())


async def introduce(store: ProfileStore, agent_id: str, client: ModelClient) -> PublicProfile:
    system = render("introduction_system.md", temperament=store.private(agent_id)["temperament.md"].strip())
    messages: list[Message] = [{"role": "user", "content": render(
        "introduction_request.md", name_max=NAME_MAX, headline_max=HEADLINE_MAX, intro_max=INTRO_MAX,
    )}]
    for attempt in range(2):
        result = await client.chat(system, messages, INTRO_SCHEMA)
        try:
            reply = _IntroReply.model_validate(extract_json(result.text))
            return store.set_public(agent_id, reply.name, reply.headline, reply.introduction)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except (ProfileError, ActionError) as exc:
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": f"That could not be accepted: {problem}. Reply again with JSON only."},
        ]
    raise ProfileError(f"{agent_id} did not produce a valid introduction")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_profiles.py -v`
Expected: 22 passed

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/profiles.py src/aiswarm/prompts.py src/aiswarm/prompts src/aiswarm/introduce.py tests/conftest.py tests/test_profiles.py
git commit -m "feat: add persistent agent profiles and self-written introductions"
```

---

### Task 10: Stop rules

**Files:**
- Create: `src/aiswarm/consensus.py`
- Test: `tests/test_consensus.py`

**Interfaces:**
- Consumes: `Entry` (Task 7), `RunSettings` (Task 1).
- Produces: `CallStatus(call_id, caller, supporters: frozenset[str], objectors: frozenset[str], critiqued: bool)`; `call_status(entries, call_id) -> CallStatus`; `StopDecision(reason: "consensus"|"stagnation"|"budget", call_id: int | None = None)`; `stop_decision(entries, agent_ids, round, settings) -> StopDecision | None`. The caller counts as a supporter unless they later vote `object`; each member's latest vote counts; only critiques from members other than the caller count.

- [ ] **Step 1: Write the failing tests**

`tests/test_consensus.py`:

```python
from aiswarm.board import Entry
from aiswarm.config import RunSettings
from aiswarm.consensus import StopDecision, call_status, stop_decision

AGENTS = ["a", "b", "c"]


def e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else []
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


def base():
    return [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="A", method="M"),
        e(3, 1, "b", "propose", answer_summary="B", method="M"),
        e(4, 1, "c", "propose", answer_summary="C", method="M"),
        e(5, 2, "a", "call_consensus", synthesis="S", refs=[], credits=[]),
    ]


def test_passes_with_quorum_and_a_critique():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    status = call_status(entries, 5)
    assert status.supporters == {"a", "c"} and status.critiqued
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("consensus", 5)


def test_unchallenged_agreement_does_not_pass():
    entries = base() + [
        e(6, 3, "b", "vote", target=5, stance="support", reason="r"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_callers_own_critique_does_not_count():
    entries = base() + [
        e(6, 3, "a", "critique", target=5, about="answer", points="self"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert not call_status(entries, 5).critiqued


def test_latest_vote_wins():
    entries = base() + [
        e(6, 3, "b", "critique", target=5, about="answer", points="p"),
        e(7, 3, "c", "vote", target=5, stance="support", reason="r"),
        e(8, 4, "c", "vote", target=5, stance="object", reason="changed mind"),
    ]
    status = call_status(entries, 5)
    assert status.objectors == {"c"}
    assert stop_decision(entries, AGENTS, 4, RunSettings()) is None


def test_votes_on_other_calls_do_not_count():
    entries = base() + [
        e(6, 3, "b", "call_consensus", synthesis="T", refs=[], credits=[]),
        e(7, 3, "c", "vote", target=6, stance="support", reason="r"),
    ]
    assert call_status(entries, 5).supporters == {"a"}


def test_stagnation_after_consecutive_all_pass_rounds():
    entries = base()[:4] + [e(5, 2, "a", "pass"), e(6, 2, "b", "pass"), e(7, 2, "c", "pass")] + [
        e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("stagnation")


def test_one_active_member_prevents_stagnation():
    entries = base()[:4] + [e(5, 2, "a", "pass"), e(6, 2, "b", "pass"), e(7, 2, "c", "question", target=2, text="?")] + [
        e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)
    ]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None


def test_budget_at_max_rounds_and_consensus_takes_precedence():
    assert stop_decision(base(), AGENTS, 12, RunSettings()) == StopDecision("budget")
    entries = base() + [
        e(6, 12, "b", "critique", target=5, about="answer", points="p"),
        e(7, 12, "c", "vote", target=5, stance="support", reason="r"),
    ]
    assert stop_decision(entries, AGENTS, 12, RunSettings()) == StopDecision("consensus", 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_consensus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.consensus'`

- [ ] **Step 3: Implement `src/aiswarm/consensus.py`**

```python
"""Stop rules for a run. Pure functions over board entries; no model involved."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from aiswarm.board import Entry
from aiswarm.config import RunSettings


@dataclass(frozen=True)
class CallStatus:
    call_id: int
    caller: str
    supporters: frozenset[str]
    objectors: frozenset[str]
    critiqued: bool


@dataclass(frozen=True)
class StopDecision:
    reason: Literal["consensus", "stagnation", "budget"]
    call_id: int | None = None


def call_status(entries: Sequence[Entry], call_id: int) -> CallStatus:
    call = next(entry for entry in entries if entry.id == call_id)
    stances = {call.author: "support"}
    critiqued = False
    for entry in entries:
        if entry.id <= call_id or entry.payload.get("target") != call_id:
            continue
        if entry.type == "vote":
            stances[entry.author] = entry.payload["stance"]
        elif entry.type == "critique" and entry.author != call.author:
            critiqued = True
    return CallStatus(
        call_id=call_id,
        caller=call.author,
        supporters=frozenset(a for a, s in stances.items() if s == "support"),
        objectors=frozenset(a for a, s in stances.items() if s == "object"),
        critiqued=critiqued,
    )


def _all_passed(entries: Sequence[Entry], agent_ids: Sequence[str], round: int) -> bool:
    posted = [e for e in entries if e.round == round and e.author in agent_ids]
    return bool(posted) and all(e.type == "pass" for e in posted)


def stop_decision(
    entries: Sequence[Entry], agent_ids: Sequence[str], round: int, settings: RunSettings
) -> StopDecision | None:
    members = set(agent_ids)
    needed = max(1, len(members) - settings.quorum_slack)
    for entry in entries:
        if entry.type == "call_consensus":
            status = call_status(entries, entry.id)
            if status.critiqued and len(status.supporters & members) >= needed:
                return StopDecision("consensus", entry.id)
    window = range(round - settings.stagnation_rounds + 1, round + 1)
    if round >= settings.stagnation_rounds and all(_all_passed(entries, agent_ids, r) for r in window):
        return StopDecision("stagnation")
    if round >= settings.max_rounds:
        return StopDecision("budget")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consensus.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/consensus.py tests/test_consensus.py
git commit -m "feat: add consensus, stagnation, and budget stop rules"
```

---

### Task 11: The View

**Files:**
- Create: `src/aiswarm/view.py`
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `Entry`, `Board`, `SYSTEM` (Task 7); `Workspace` (Task 8); `ProfileStore`, `PublicProfile` (Task 9); `call_status` (Task 10).
- Produces: `make_who(profiles: dict[str, PublicProfile | None]) -> Callable[[str], str]` returning `"Name (id)"` (or `"id (id)"` without a profile, `"system"` for system); `render_entry(entry, who) -> str` whose first line is `#<id> [r<round>] <who> <type>` plus ` → #x, #y` when it has refs; `ViewInputs` dataclass; `collect_inputs(*, agent_id, goal, round, board, store, workspace, view_entries) -> ViewInputs`; `build_view(inputs, budget_chars) -> str`.
- Section order (also trimming priority): goal + round header, your private notes, open calls for consensus, entries that respond to you, recent board entries, workspace files, community members. List sections keep their newest items first when trimming.

- [ ] **Step 1: Write the failing tests**

`tests/test_view.py`:

```python
from aiswarm.board import SYSTEM, Board
from aiswarm.view import build_view, collect_inputs, make_who, render_entry
from aiswarm.workspace import Workspace


def setup(tmp_path, community, make_run):
    store, conn = community
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    ws = Workspace(conn, "r1", tmp_path / "ws")
    return store, conn, board, ws


def view_for(agent_id, round, store, board, ws, budget=50_000, view_entries=15):
    inputs = collect_inputs(agent_id=agent_id, goal="Pick a colour", round=round, board=board,
                            store=store, workspace=ws, view_entries=view_entries)
    return build_view(inputs, budget_chars=budget)


def test_private_files_of_others_never_appear(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.write_private("b", "temperament.md", "TEMPERAMENT-B-SECRET")
    store.write_private("b", "lessons.md", "LESSON-B-SECRET")
    store.write_private("b", "peer-notes.md", "NOTES-B-SECRET")
    store.write_private("a", "lessons.md", "LESSON-A-OWN")
    entry = board.append(2, "b", "propose", {"answer_summary": "Blue", "method": "M"})
    conn.execute("INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, 'r1', 'b', 'm', 'TRACE-B-SECRET')", (entry.id,))
    view = view_for("a", 2, store, board, ws)
    for secret in ("TEMPERAMENT-B-SECRET", "LESSON-B-SECRET", "NOTES-B-SECRET", "TRACE-B-SECRET"):
        assert secret not in view
    assert "LESSON-A-OWN" in view
    assert "TRACE-B-SECRET" not in view_for("b", 2, store, board, ws)


def test_round_one_hides_other_opening_proposals(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    board.append(1, "b", "propose", {"answer_summary": "B-OPENING", "method": "M"})
    view = view_for("a", 1, store, board, ws)
    assert "B-OPENING" not in view
    assert "opening round" in view
    assert "B-OPENING" in view_for("a", 2, store, board, ws)


def test_shows_goal_profiles_and_open_calls(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    store.set_public("b", "Beatrix", "Bea headline", "I am Bea.")
    call = board.append(2, "b", "call_consensus", {"synthesis": "Blue", "refs": [], "credits": []})
    board.append(3, "c", "vote", {"target": call.id, "stance": "support", "reason": "ok"}, refs=[call.id])
    ws.write("draft.md", 0, "text", author="c", note="n")
    view = view_for("a", 4, store, board, ws)
    assert "Pick a colour" in view and "# Round 4" in view
    assert "Beatrix (b) — Bea headline" in view
    assert "formerly: Bea" in view
    assert f"#{call.id} by Beatrix (b): support: Beatrix (b), Cyd (c)" in view
    assert "critiqued: no" in view
    assert "draft.md (v1)" in view


def test_trimming_keeps_newest_entries_and_header(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    for i in range(60):
        board.append(2, "b", "question", {"target": 1, "text": f"QUESTION-{i:02d} " + "x" * 200}, refs=[1])
    view = view_for("a", 3, store, board, ws, budget=2_500, view_entries=60)
    assert "Pick a colour" in view
    assert "QUESTION-59" in view
    assert "QUESTION-00" not in view
    assert len(view) <= 2_500


def test_entries_responding_to_me_survive_outside_recent_window(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    mine = board.append(2, "a", "propose", {"answer_summary": "Red", "method": "M"})
    board.append(2, "b", "critique", {"target": mine.id, "about": "method", "points": "OLD-CRITIQUE"}, refs=[mine.id])
    for i in range(20):
        board.append(3, "c", "pass", {})
    view = view_for("a", 4, store, board, ws, view_entries=5)
    assert "Entries that respond to you" in view
    assert "OLD-CRITIQUE" in view


def test_render_entry_and_who(community):
    store, conn = community
    who = make_who({"a": store.public("a"), "z": None})
    assert who("a") == "Alpha (a)" and who("z") == "z (z)" and who(SYSTEM) == "system"
    from aiswarm.board import Entry
    text = render_entry(Entry(7, "r1", 2, "a", "critique", {"target": 3, "points": "p"}, [3], "all", "t"), who)
    assert text.splitlines()[0] == "#7 [r2] Alpha (a) critique → #3"
    assert "  points: p" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.view'`

- [ ] **Step 3: Implement `src/aiswarm/view.py`**

```python
"""Builds what one agent sees in one step: privacy-filtered and trimmed to its context budget."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiswarm.board import SYSTEM, Board, Entry
from aiswarm.consensus import call_status
from aiswarm.profiles import ProfileStore, PublicProfile
from aiswarm.workspace import Workspace

Who = Callable[[str], str]
_OWN_NOTES = ("self.md", "lessons.md", "peer-notes.md")


def make_who(profiles: dict[str, PublicProfile | None]) -> Who:
    def who(author: str) -> str:
        if author == SYSTEM:
            return "system"
        profile = profiles.get(author)
        return f"{profile.name if profile else author} ({author})"

    return who


def _fmt(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render_entry(entry: Entry, who: Who) -> str:
    head = f"#{entry.id} [r{entry.round}] {who(entry.author)} {entry.type}"
    if entry.refs:
        head += " → " + ", ".join(f"#{ref}" for ref in entry.refs)
    body = [f"  {key}: {_fmt(value)}" for key, value in entry.payload.items()]
    return "\n".join([head, *body])


@dataclass(frozen=True)
class ViewInputs:
    agent_id: str
    goal: str
    round: int
    entries: list[Entry]
    own_notes: dict[str, str]
    profiles: dict[str, PublicProfile | None]
    files: list[tuple[str, int]]
    view_entries: int


def collect_inputs(
    *, agent_id: str, goal: str, round: int, board: Board, store: ProfileStore,
    workspace: Workspace, view_entries: int,
) -> ViewInputs:
    entries = board.entries()
    if round == 1:
        entries = [e for e in entries if not (e.round == 1 and e.author != agent_id)]
    private = store.private(agent_id)
    return ViewInputs(
        agent_id=agent_id, goal=goal, round=round, entries=entries,
        own_notes={name: private[name] for name in _OWN_NOTES},
        profiles={i: store.public(i) for i in store.ids()},
        files=workspace.list_files(), view_entries=view_entries,
    )


def _fit(title: str, items: list[str], remaining: int) -> tuple[str | None, int]:
    """Keep the newest items (end of list) that fit; render them in original order."""
    used = len(title) + 2
    kept: list[str] = []
    for item in reversed(items):
        if used + len(item) + 1 > remaining:
            break
        kept.append(item)
        used += len(item) + 1
    if not kept:
        return None, remaining
    return title + "\n" + "\n".join(reversed(kept)), remaining - used


def build_view(inputs: ViewInputs, budget_chars: int) -> str:
    who = make_who(inputs.profiles)
    header = f"# Goal\n{inputs.goal}\n\n# Round {inputs.round}\nYou are {who(inputs.agent_id)}."
    if inputs.round == 1:
        header += (
            "\nThis is the opening round. Propose your own position (action `propose`). "
            "Other members' opening proposals are hidden until the round ends."
        )
    fixed = [header]
    notes = [f"## {name}\n{text.strip()}" for name, text in inputs.own_notes.items() if text.strip()]
    if notes:
        fixed.append("# Your private notes (only you can see these)\n" + "\n\n".join(notes))
    calls = [e for e in inputs.entries if e.type == "call_consensus"]
    if calls:
        lines = []
        for call in calls:
            status = call_status(inputs.entries, call.id)
            support = ", ".join(sorted(who(a) for a in status.supporters)) or "none"
            objection = ", ".join(sorted(who(a) for a in status.objectors)) or "none"
            critiqued = "yes" if status.critiqued else "no"
            lines.append(
                f"- #{call.id} by {who(call.author)}: support: {support} · object: {objection} · critiqued: {critiqued}"
            )
        fixed.append("# Open calls for consensus\n" + "\n".join(lines))

    remaining = budget_chars - sum(len(part) + 2 for part in fixed)
    recent = inputs.entries[-inputs.view_entries:]
    recent_ids = {e.id for e in recent}
    mine = {e.id for e in inputs.entries if e.author == inputs.agent_id}
    about_me = [e for e in inputs.entries if e.id not in recent_ids and set(e.refs) & mine]
    files = [f"- {path} (v{version})" for path, version in inputs.files] or ["(no files yet)"]
    members = []
    for agent_id, profile in inputs.profiles.items():
        if profile is None:
            members.append(f"- {agent_id} ({agent_id}) — (no profile yet)")
            continue
        block = f"- {profile.name} ({agent_id}) — {profile.headline}\n  {profile.introduction}"
        if profile.former_names:
            block += f"\n  formerly: {', '.join(profile.former_names)}"
        members.append(block)

    parts = list(fixed)
    for title, items in (
        ("# Entries that respond to you", [render_entry(e, who) for e in about_me]),
        ("# Recent board entries", [render_entry(e, who) for e in recent]),
        ("# Workspace files", files),
        ("# Community members", members),
    ):
        section, remaining = _fit(title, items, remaining)
        if section:
            parts.append(section)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_view.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/view.py tests/test_view.py
git commit -m "feat: add privacy-filtered, budget-trimmed agent view"
```

---

### Task 12: Agent step

**Files:**
- Create: `src/aiswarm/agent.py`, `src/aiswarm/prompts/agent_system.md`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: actions (Task 6), `ModelClient`, `ModelError` (Task 3), `Entry` (Task 7), `Workspace`, `normalise_path`, `InvalidPath` (Task 8), `render_entry`, `Who` (Task 11), `render` (Task 9).
- Produces: `StepOutcome(action, failure: str | None = None, detail: str = "", reasoning: str | None = None, model: str | None = None)`; `async take_step(*, client, system, view, round, max_reads, reader, validate) -> StepOutcome`; `make_validator(entries, agent_ids) -> Callable[[action], None]` (raises `ActionError`); `make_reader(workspace, entries, who) -> Callable[[read action], str]`; `system_prompt(display: str, temperament: str, max_reads: int) -> str`.
- Failure kinds: `"model_error"`, `"invalid_action"`. A step makes at most `max_reads + 2` model calls.

- [ ] **Step 1: Write the failing tests**

`tests/test_agent.py`:

```python
import json

import pytest

from aiswarm.actions import ActionError, CallConsensus, ListFiles, Pass, Propose, ReadEntry, ReadFile, Vote, WriteFile, Credit
from aiswarm.agent import make_reader, make_validator, system_prompt, take_step
from aiswarm.board import Entry
from aiswarm.db import connect
from aiswarm.models.fake import FakeModel
from aiswarm.workspace import Workspace

PROPOSE = json.dumps({"action": {"type": "propose", "answer_summary": "Blue", "method": "Gut"}})
CRITIQUE = json.dumps({"action": {"type": "critique", "target": 2, "about": "method", "points": "p"}})
LIST = json.dumps({"action": {"type": "list_files"}})


def no_reads(action):
    raise AssertionError("reader should not be called")


def allow(action):
    return None


async def step(model, round=2, max_reads=3, reader=no_reads, validate=allow):
    return await take_step(client=model, system="SYS", view="VIEW", round=round, max_reads=max_reads,
                           reader=reader, validate=validate)


async def test_valid_action_first_try():
    outcome = await step(FakeModel([CRITIQUE]))
    assert outcome.action.type == "critique" and outcome.failure is None


async def test_reads_then_posts():
    model = FakeModel([LIST, PROPOSE])
    outcome = await step(model, reader=lambda action: "draft.md (v1)")
    assert isinstance(outcome.action, Propose)
    assert model.calls[1][1][-1]["content"] == "Read result:\ndraft.md (v1)"


async def test_reads_beyond_limit_are_corrected_then_fail():
    outcome = await step(FakeModel([LIST, LIST]), max_reads=0, reader=lambda a: "x")
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"
    assert "reads" in outcome.detail


async def test_invalid_then_valid():
    model = FakeModel(["nonsense", CRITIQUE])
    outcome = await step(model)
    assert outcome.failure is None
    assert "not a valid action" in model.calls[1][1][-1]["content"]


async def test_invalid_twice_becomes_failed_pass():
    outcome = await step(FakeModel(["nonsense", "still nonsense"]))
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"


async def test_opening_round_requires_propose():
    model = FakeModel([CRITIQUE, PROPOSE])
    outcome = await step(model, round=1)
    assert isinstance(outcome.action, Propose)
    assert "opening round" in model.calls[1][1][-1]["content"]


async def test_model_error_becomes_failed_pass():
    outcome = await step(FakeModel(fail_times=1))
    assert isinstance(outcome.action, Pass) and outcome.failure == "model_error"


async def test_validator_rejection_is_corrected():
    def validate(action):
        if action.type == "critique":
            raise ActionError("Entry #2 does not exist.")

    model = FakeModel([CRITIQUE, PROPOSE])
    outcome = await step(model, validate=validate)
    assert isinstance(outcome.action, Propose)
    assert "#2 does not exist" in model.calls[1][1][-1]["content"]


async def test_reasoning_is_collected():
    outcome = await step(FakeModel([LIST, PROPOSE], reasoning="because"), reader=lambda a: "x")
    assert outcome.reasoning == "because\n\nbecause"
    assert outcome.model == "fake"


def entries():
    return [
        Entry(1, "r1", 0, "system", "goal", {"goal": "G"}, [], "all", "t"),
        Entry(2, "r1", 1, "a", "propose", {"answer_summary": "A", "method": "M"}, [], "all", "t"),
        Entry(3, "r1", 2, "a", "call_consensus", {"synthesis": "S", "refs": [], "credits": []}, [], "all", "t"),
    ]


def test_validator_rules():
    validate = make_validator(entries(), ["a", "b"])
    validate(Vote(type="vote", target=3, stance="support", reason="r"))
    with pytest.raises(ActionError, match="does not exist"):
        validate(Vote(type="vote", target=99, stance="support", reason="r"))
    with pytest.raises(ActionError, match="call_consensus"):
        validate(Vote(type="vote", target=2, stance="support", reason="r"))
    with pytest.raises(ActionError, match="zed"):
        validate(CallConsensus(type="call_consensus", synthesis="s", refs=[2],
                               credits=[Credit(agent_id="zed", contribution="c")]))
    with pytest.raises(ActionError, match="does not exist"):
        validate(CallConsensus(type="call_consensus", synthesis="s", refs=[42], credits=[]))
    with pytest.raises(ActionError, match="Invalid workspace path"):
        validate(WriteFile(type="write_file", path="../x", base_version=0, content="c", note="n"))


def test_reader(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    ws = Workspace(conn, "r1", tmp_path / "ws")
    ws.write("draft.md", 0, "hello", author="a", note="n")
    visible = entries()[:2]
    read = make_reader(ws, visible, lambda author: author)
    assert read(ListFiles(type="list_files")) == "draft.md (v1)"
    assert read(ReadFile(type="read_file", path="draft.md")) == "draft.md (v1):\nhello"
    assert read(ReadFile(type="read_file", path="missing.md")) == "No such file: missing.md"
    assert "Invalid workspace path" in read(ReadFile(type="read_file", path="../etc"))
    assert read(ReadEntry(type="read_entry", id=2)).startswith("#2 [r1] a propose")
    assert read(ReadEntry(type="read_entry", id=3)) == "No entry #3 is visible to you."


def test_system_prompt_contains_identity_and_rules():
    text = system_prompt("Wren (a)", "Curious.", max_reads=3)
    assert "You are Wren (a)" in text
    assert "Curious." in text
    assert "up to 3 reads" in text
    assert '"call_consensus"' in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.agent'`

- [ ] **Step 3: Create `src/aiswarm/prompts/agent_system.md`**

```
You are $display, a member of a small community of agents working together on a shared goal. There is no leader. Each member decides for themselves what to do next.

Each turn you take exactly one action. Reply with JSON only, in the form {"action": {...}}.

Before your action you may make up to $max_reads reads. A read shows you its result and you then reply again.

Reads:
- {"type": "list_files"}: list the shared workspace files and their versions.
- {"type": "read_file", "path": "..."}: read a workspace file.
- {"type": "read_entry", "id": <entry id>}: read one board entry in full.

Actions:
- {"type": "propose", "answer_summary": "...", "method": "..."}: a position on the goal and the method you used to reach it.
- {"type": "critique", "target": <entry id>, "about": "answer" or "method", "points": "..."}
- {"type": "question", "target": <entry id>, "text": "..."}
- {"type": "adopt", "target": <entry id>, "reason": "..."}: take up another member's method.
- {"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}
- {"type": "write_file", "path": "...", "base_version": <current version, 0 for a new file>, "content": "...", "note": "..."}: write a file in the shared workspace. The write is rejected if the file changed since base_version.
- {"type": "call_consensus", "synthesis": "...", "refs": [<entry ids>], "credits": [{"agent_id": "...", "contribution": "..."}]}: propose that the community has its answer.
- {"type": "vote", "target": <call_consensus entry id>, "stance": "support" or "object", "reason": "..."}
- {"type": "pass"}: nothing to add right now.

The work ends when a call_consensus has been critiqued at least once and is supported by all members but at most one. Its caller counts as a supporter.

Your temperament:
$temperament
```

- [ ] **Step 4: Implement `src/aiswarm/agent.py`**

```python
"""One agent step: view in, one validated posting action out."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from aiswarm.actions import (
    READ_ACTIONS, ActionError, CallConsensus, ListFiles, Pass, Propose, ReadFile, Vote, WriteFile,
    parse_action, step_schema,
)
from aiswarm.board import Entry
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.prompts import render
from aiswarm.view import Who, render_entry
from aiswarm.workspace import InvalidPath, Workspace, normalise_path

Reader = Callable[[Any], str]
Validator = Callable[[Any], None]


@dataclass(frozen=True)
class StepOutcome:
    action: Any
    failure: str | None = None
    detail: str = ""
    reasoning: str | None = None
    model: str | None = None


def system_prompt(display: str, temperament: str, max_reads: int) -> str:
    return render("agent_system.md", display=display, temperament=temperament.strip(), max_reads=max_reads)


async def take_step(
    *, client: ModelClient, system: str, view: str, round: int, max_reads: int,
    reader: Reader, validate: Validator,
) -> StepOutcome:
    messages: list[Message] = [{"role": "user", "content": view}]
    schema = step_schema()
    reads = 0
    corrected = False
    reasoning: list[str] = []
    model: str | None = None

    def outcome(action: Any, failure: str | None = None, detail: str = "") -> StepOutcome:
        return StepOutcome(action, failure, detail, "\n\n".join(reasoning) or None, model)

    while True:
        try:
            result = await client.chat(system, messages, schema)
        except ModelError as exc:
            return outcome(Pass(type="pass"), "model_error", str(exc))
        model = result.model or model
        if result.reasoning:
            reasoning.append(result.reasoning)
        try:
            action = parse_action(result.text)
            if isinstance(action, READ_ACTIONS):
                if reads >= max_reads:
                    raise ActionError(f"You have used all {max_reads} reads; choose a posting action.")
                reads += 1
                messages += [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": "Read result:\n" + reader(action)},
                ]
                continue
            if round == 1 and not isinstance(action, Propose):
                raise ActionError("In the opening round your action must be `propose`.")
            validate(action)
            return outcome(action)
        except ActionError as exc:
            if corrected:
                return outcome(Pass(type="pass"), "invalid_action", str(exc))
            corrected = True
            messages += [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": f"That was not a valid action: {exc}\nReply again with exactly one valid action."},
            ]


def make_validator(entries: Sequence[Entry], agent_ids: Sequence[str]) -> Validator:
    by_id = {e.id: e for e in entries}

    def validate(action: Any) -> None:
        target = getattr(action, "target", None)
        if target is not None and target not in by_id:
            raise ActionError(f"Entry #{target} does not exist.")
        if isinstance(action, Vote) and by_id[action.target].type != "call_consensus":
            raise ActionError(f"Entry #{action.target} is not a call_consensus; you can only vote on those.")
        if isinstance(action, CallConsensus):
            missing = [ref for ref in action.refs if ref not in by_id]
            if missing:
                raise ActionError(f"Entry #{missing[0]} does not exist.")
            unknown = [c.agent_id for c in action.credits if c.agent_id not in agent_ids]
            if unknown:
                raise ActionError(f"Unknown agent ids in credits: {unknown}. Members are {list(agent_ids)}.")
        if isinstance(action, WriteFile):
            try:
                normalise_path(action.path)
            except InvalidPath as exc:
                raise ActionError(str(exc)) from exc

    return validate


def make_reader(workspace: Workspace, entries: Sequence[Entry], who: Who) -> Reader:
    by_id = {e.id: e for e in entries}

    def read(action: Any) -> str:
        if isinstance(action, ListFiles):
            return "\n".join(f"{path} (v{version})" for path, version in workspace.list_files()) or "(no files yet)"
        if isinstance(action, ReadFile):
            try:
                content, version = workspace.read(action.path)
            except FileNotFoundError:
                return f"No such file: {action.path}"
            except InvalidPath as exc:
                return str(exc)
            return f"{normalise_path(action.path)} (v{version}):\n{content}"
        entry = by_id.get(action.id)
        return render_entry(entry, who) if entry else f"No entry #{action.id} is visible to you."

    return read
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/agent.py src/aiswarm/prompts/agent_system.md tests/test_agent.py
git commit -m "feat: add agent step with reads, corrections, and validation"
```

---

### Task 13: Run report

**Files:**
- Create: `src/aiswarm/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Entry` (Task 7), `StopDecision`, `call_status` (Task 10), `Who` (Task 11).
- Produces: `write_report(run_dir: Path, goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> None`, writing `run_dir/report/answer.md` and `run_dir/report/minority.md`. Credits are printed exactly as the caller gave them.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
from aiswarm.board import Entry
from aiswarm.consensus import StopDecision
from aiswarm.report import write_report


def e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else []
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


def who(author):
    return {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}[author]


def read(tmp_path, name):
    return (tmp_path / "report" / name).read_text()


def test_consensus_report(tmp_path):
    entries = [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="Blue", method="Gut"),
        e(3, 2, "a", "call_consensus", synthesis="Blue it is", refs=[2],
          credits=[{"agent_id": "b", "contribution": "framing"}]),
        e(4, 3, "b", "critique", target=3, about="answer", points="p"),
        e(5, 3, "c", "vote", target=3, stance="object", reason="Green is better"),
    ]
    write_report(tmp_path, "Pick a colour", entries, StopDecision("consensus", 3), who)
    answer = read(tmp_path, "answer.md")
    assert "**Goal:** Pick a colour" in answer
    assert "Blue it is" in answer
    assert "- Bea (b): framing" in answer
    assert "- Cyd (c): Green is better" in read(tmp_path, "minority.md")


def test_consensus_with_no_credits_or_objections(tmp_path):
    entries = [e(1, 0, "system", "goal", goal="G"), e(2, 2, "a", "call_consensus", synthesis="S", refs=[], credits=[])]
    write_report(tmp_path, "G", entries, StopDecision("consensus", 2), who)
    assert "_The caller credited no one._" in read(tmp_path, "answer.md")
    assert "_No objections._" in read(tmp_path, "minority.md")


def test_no_consensus_lists_latest_proposal_per_member(tmp_path):
    entries = [
        e(1, 0, "system", "goal", goal="G"),
        e(2, 1, "a", "propose", answer_summary="Blue", method="Gut"),
        e(3, 1, "b", "propose", answer_summary="Red", method="Survey"),
        e(4, 2, "a", "propose", answer_summary="Teal", method="Revised"),
    ]
    write_report(tmp_path, "G", entries, StopDecision("budget"), who)
    answer = read(tmp_path, "answer.md")
    assert "## Competing positions" in answer
    assert "Teal" in answer and "Red" in answer and "Blue" not in answer
    assert "_No consensus was reached._" in read(tmp_path, "minority.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.report'`

- [ ] **Step 3: Implement `src/aiswarm/report.py`**

```python
"""End-of-run report: the answer (or competing positions) and the minority report."""

from pathlib import Path

from aiswarm.board import Entry
from aiswarm.consensus import StopDecision, call_status
from aiswarm.view import Who


def write_report(run_dir: Path, goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> None:
    out = run_dir / "report"
    out.mkdir(parents=True, exist_ok=True)
    ended = decision.reason + (f" (call #{decision.call_id})" if decision.call_id else "")
    answer = ["# Answer", "", f"**Goal:** {goal}", "", f"**Ended:** {ended}", ""]
    minority = ["# Minority report", ""]

    if decision.reason == "consensus" and decision.call_id is not None:
        call = next(e for e in entries if e.id == decision.call_id)
        answer += ["## Synthesis", "", call.payload["synthesis"], "", "## Credits", ""]
        credits = call.payload.get("credits", [])
        answer += [f"- {who(c['agent_id'])}: {c['contribution']}" for c in credits] or ["_The caller credited no one._"]
        objectors = call_status(entries, call.id).objectors
        reasons = {
            e.author: e.payload["reason"]
            for e in entries
            if e.type == "vote" and e.payload.get("target") == call.id and e.author in objectors
        }
        minority += [f"- {who(author)}: {reason}" for author, reason in sorted(reasons.items())] or ["_No objections._"]
    else:
        answer += ["## Competing positions", ""]
        latest: dict[str, Entry] = {}
        for entry in entries:
            if entry.type == "propose":
                latest[entry.author] = entry
        for author, entry in latest.items():
            answer += [f"### {who(author)} (#{entry.id})", "", entry.payload["answer_summary"], "",
                       f"*Method:* {entry.payload['method']}", ""]
        if not latest:
            answer.append("_No proposals were made._")
        minority.append("_No consensus was reached._")

    (out / "answer.md").write_text("\n".join(answer).rstrip() + "\n")
    (out / "minority.md").write_text("\n".join(minority).rstrip() + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/report.py tests/test_report.py
git commit -m "feat: add end-of-run answer and minority report"
```

---

### Task 14: Runner

**Files:**
- Create: `src/aiswarm/runner.py`
- Modify: `tests/conftest.py` (add `scripted`, `find`, `runner_factory` fixtures)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `RunResult(run_id, status: "running"|"paused"|"closed", reason: str | None = None, call_id: int | None = None)`; `new_run_id() -> str`; `Runner(*, settings: RunSettings, conn, store: ProfileStore, runs_dir: Path, clients: Mapping[str, ModelClient], sink=None, rng: random.Random | None = None)` with `start(goal, agent_ids) -> str` and `async run(run_id) -> RunResult`. `clients` is keyed by agent id.
- Behaviour: each round all agents step concurrently on the same snapshot; posts are appended in shuffled order inside one transaction together with the `rounds` row (workspace head); events are emitted only after commit. If every agent's step fails with `model_error`, the run pauses without posting. On run start/resume the workspace is reset to the last completed round's commit (or the initial commit).
- Entry types written by the Runner: agent actions (`propose`, `critique`, ..., `pass`), `write_file` (payload `path`, `version`, `note`), system `goal`, `artifact_changed`, `write_rejected`, `run_closed`. Failed steps post `pass` with payload `{"failure": kind}` and a `failures` row. Reasoning goes to `traces` only.
- Events: `run_started`, `round_started`, `agent_step_started`, `agent_step_finished`, `action_posted`, `round_completed`, `run_paused`, `run_closed`.

- [ ] **Step 1: Add test fixtures**

Append to `tests/conftest.py`:

```python
import random
import re

from aiswarm.config import RunSettings
from aiswarm.models.fake import FakeModel
from aiswarm.runner import Runner


def _scripted(plan, **kwargs):
    """A FakeModel that answers per round: plan maps round -> (view -> action dict). Default: pass."""
    def respond(system, messages):
        view = messages[0]["content"]
        rnd = int(re.search(r"^# Round (\d+)", view, re.M).group(1))
        return json.dumps({"action": plan.get(rnd, lambda v: {"type": "pass"})(view)})

    return FakeModel(respond, **kwargs)


def _find(view, type_, author=None):
    pattern = re.compile(r"^#(\d+) \[r\d+\] .*?\(([a-z][a-z0-9-]*)\) " + re.escape(type_) + r"\b", re.M)
    return [int(m.group(1)) for m in pattern.finditer(view) if author is None or m.group(2) == author]


@pytest.fixture
def scripted():
    return _scripted


@pytest.fixture
def find():
    return _find


@pytest.fixture
def runner_factory(tmp_path):
    def make(store, conn, clients, rng=None, **settings):
        return Runner(settings=RunSettings(**settings), conn=conn, store=store, runs_dir=tmp_path / "runs",
                      clients=clients, rng=rng or random.Random(0))

    return make
```

- [ ] **Step 2: Write the failing tests**

`tests/test_runner.py`:

```python
import json

import pytest

from aiswarm.events import read_events
from aiswarm.models.fake import FakeModel
from aiswarm.runner import Runner

AGENTS = ["a", "b", "c"]


def propose(view):
    return {"type": "propose", "answer_summary": "Blue", "method": "Asked myself."}


def entry_types(conn, run_id):
    return [r["type"] for r in conn.execute("SELECT type FROM entries WHERE run_id = ? ORDER BY id", (run_id,))]


class NoShuffle:
    def shuffle(self, items):
        pass


async def test_run_reaches_consensus(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {
        "a": scripted({1: propose, 2: lambda v: {
            "type": "call_consensus", "synthesis": "Blue it is", "refs": find(v, "propose"),
            "credits": [{"agent_id": "b", "contribution": "framing"}]}}),
        "b": scripted({1: propose, 3: lambda v: {
            "type": "critique", "target": find(v, "call_consensus")[0], "about": "answer", "points": "Too quick."}}),
        "c": scripted({1: propose, 3: lambda v: {
            "type": "vote", "target": find(v, "call_consensus")[0], "stance": "support", "reason": "Fine."}}),
    }
    runner = runner_factory(store, conn, clients)
    run_id = runner.start("Pick a colour", AGENTS)
    result = await runner.run(run_id)
    assert (result.status, result.reason) == ("closed", "consensus")
    run_dir = tmp_path / "runs" / run_id
    answer = (run_dir / "report" / "answer.md").read_text()
    assert "Blue it is" in answer and "- Bea (b): framing" in answer
    events = read_events(run_dir / "events.jsonl")
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_closed"
    assert conn.execute("SELECT COUNT(*) FROM rounds WHERE run_id = ?", (run_id,)).fetchone()[0] == 3
    assert entry_types(conn, run_id)[-1] == "run_closed"
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "closed"


async def test_stagnation_closes_run(community, scripted, runner_factory):
    store, conn = community
    runner = runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS})
    result = await runner.run(runner.start("G", AGENTS))
    assert result.reason == "stagnation"


async def test_budget_closes_run_with_competing_positions(tmp_path, community, scripted, find, runner_factory):
    store, conn = community
    clients = {
        "a": scripted({1: propose, 2: lambda v: {"type": "question", "target": find(v, "propose")[0], "text": "Why?"}}),
        "b": scripted({1: propose}),
        "c": scripted({1: propose}),
    }
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS)
    result = await runner.run(run_id)
    assert result.reason == "budget"
    assert "## Competing positions" in (tmp_path / "runs" / run_id / "report" / "answer.md").read_text()


async def test_same_round_writes_one_wins_one_rejected(tmp_path, community, scripted, runner_factory):
    store, conn = community
    write = lambda v: {"type": "write_file", "path": "draft.md", "base_version": 0, "content": "x", "note": "start"}
    clients = {"a": scripted({1: propose, 2: write}), "b": scripted({1: propose, 2: write}), "c": scripted({1: propose})}
    runner = runner_factory(store, conn, clients, max_rounds=2)
    run_id = runner.start("G", AGENTS)
    await runner.run(run_id)
    types = entry_types(conn, run_id)
    assert types.count("write_file") == 1
    assert types.count("artifact_changed") == 1
    assert types.count("write_rejected") == 1
    rejected = conn.execute("SELECT payload FROM entries WHERE run_id = ? AND type = 'write_rejected'", (run_id,)).fetchone()
    assert json.loads(rejected[0])["current_version"] == 1
    assert (tmp_path / "runs" / run_id / "workspace" / "draft.md").read_text() == "x"
    assert conn.execute("SELECT kind FROM failures WHERE run_id = ?", (run_id,)).fetchone()[0] == "write_conflict"


async def test_outage_pauses_then_resume_completes(community, scripted, runner_factory):
    store, conn = community
    down = {a: FakeModel(fail_times=10**6) for a in AGENTS}
    run_id = runner_factory(store, conn, down).start("G", AGENTS)
    paused = await runner_factory(store, conn, down).run(run_id)
    assert paused.status == "paused"
    assert entry_types(conn, run_id) == ["goal"]
    result = await runner_factory(store, conn, {a: scripted({1: propose}) for a in AGENTS}).run(run_id)
    assert (result.status, result.reason) == ("closed", "stagnation")


async def test_crash_mid_round_rolls_back_and_resumes(tmp_path, community, scripted, runner_factory):
    store, conn = community
    write = lambda v: {"type": "write_file", "path": "x.md", "base_version": 0, "content": "x", "note": "n"}

    def clients():
        return {"a": scripted({1: propose, 2: write}), "b": scripted({1: propose}), "c": scripted({1: propose})}

    class CrashingRunner(Runner):
        def _post(self, board, ws, run_id, rnd, agent_id, outcome):
            if rnd == 2 and agent_id == "b":
                raise RuntimeError("crash")
            return super()._post(board, ws, run_id, rnd, agent_id, outcome)

    first = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=3)
    crashing = CrashingRunner(settings=first.settings, conn=conn, store=store, runs_dir=tmp_path / "runs",
                              clients=clients(), rng=NoShuffle())
    run_id = crashing.start("G", AGENTS)
    with pytest.raises(RuntimeError, match="crash"):
        await crashing.run(run_id)
    assert all(r["round"] <= 1 for r in conn.execute("SELECT round FROM entries WHERE run_id = ?", (run_id,)))
    assert conn.execute("SELECT COUNT(*) FROM files WHERE run_id = ?", (run_id,)).fetchone()[0] == 0

    result = await runner_factory(store, conn, clients(), max_rounds=3).run(run_id)
    assert result.status == "closed"
    assert entry_types(conn, run_id).count("write_file") == 1
    assert (tmp_path / "runs" / run_id / "workspace" / "x.md").read_text() == "x"


async def test_reasoning_goes_to_traces_and_never_to_views(community, scripted, runner_factory):
    store, conn = community
    store.write_private("b", "temperament.md", "TEMPERAMENT-B-SECRET")
    clients = {a: scripted({1: propose}, reasoning=f"TRACE-{a}") for a in AGENTS}
    run_id = runner_factory(store, conn, clients).start("G", AGENTS)
    await runner_factory(store, conn, clients).run(run_id)
    assert conn.execute("SELECT COUNT(*) FROM traces WHERE run_id = ?", (run_id,)).fetchone()[0] >= 3
    for agent_id, client in clients.items():
        for system, messages, _ in client.calls:
            seen = system + "".join(m["content"] for m in messages)
            assert "TRACE-" not in seen
            if agent_id != "b":
                assert "TEMPERAMENT-B-SECRET" not in seen


def test_start_rejects_unknown_agents(community, runner_factory):
    store, conn = community
    with pytest.raises(ValueError, match="zed"):
        runner_factory(store, conn, {}).start("G", ["a", "zed"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.runner'` (raised from conftest import)

- [ ] **Step 4: Implement `src/aiswarm/runner.py`**

```python
"""The Runner: plain code that ticks rounds, stores posts, and applies stop rules. It never steers."""

import asyncio
import json
import random
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiswarm.actions import WriteFile, target_refs
from aiswarm.agent import StepOutcome, make_reader, make_validator, system_prompt, take_step
from aiswarm.board import SYSTEM, Board
from aiswarm.config import RunSettings
from aiswarm.consensus import StopDecision, stop_decision
from aiswarm.db import now_iso, transaction
from aiswarm.events import EventLog
from aiswarm.models.base import ModelClient
from aiswarm.profiles import ProfileStore
from aiswarm.report import write_report
from aiswarm.view import build_view, collect_inputs, make_who
from aiswarm.workspace import Workspace, WriteConflict, normalise_path


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    reason: str | None = None
    call_id: int | None = None


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Runner:
    def __init__(
        self, *, settings: RunSettings, conn: sqlite3.Connection, store: ProfileStore, runs_dir: Path,
        clients: Mapping[str, ModelClient], sink: Callable[[dict[str, Any]], None] | None = None,
        rng: Any = None,
    ):
        self.settings = settings
        self.conn = conn
        self.store = store
        self.runs_dir = runs_dir
        self.clients = clients
        self.sink = sink
        self.rng = rng or random.Random()

    def _events(self, run_id: str) -> EventLog:
        return EventLog(self.runs_dir / run_id / "events.jsonl", sink=self.sink)

    def _workspace(self, run_id: str) -> Workspace:
        return Workspace(self.conn, run_id, self.runs_dir / run_id / "workspace")

    def start(self, goal: str, agent_ids: Sequence[str]) -> str:
        if not agent_ids:
            raise ValueError("a run needs at least one agent")
        unknown = [a for a in agent_ids if a not in self.store.ids()]
        if unknown:
            raise ValueError(f"unknown agents: {unknown}")
        run_id = new_run_id()
        (self.runs_dir / run_id).mkdir(parents=True)
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, ?, 'running', ?, ?)",
                (run_id, goal, json.dumps(list(agent_ids)), now_iso()),
            )
            Board(self.conn, run_id).append(0, SYSTEM, "goal", {"goal": goal})
        self._workspace(run_id)
        self._events(run_id).emit("run_started", run_id=run_id, goal=goal, agents=list(agent_ids))
        return run_id

    async def run(self, run_id: str) -> RunResult:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] == "closed":
            return RunResult(run_id, "closed", row["ended_reason"])
        goal, agent_ids = row["goal"], json.loads(row["agent_ids"])
        board, ws, events = Board(self.conn, run_id), self._workspace(run_id), self._events(run_id)

        last = self.conn.execute(
            "SELECT round, workspace_commit FROM rounds WHERE run_id = ? ORDER BY round DESC LIMIT 1", (run_id,)
        ).fetchone()
        ws.reset_to(last["workspace_commit"] if last else ws.initial_commit())
        completed = last["round"] if last else 0
        self.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))

        for rnd in range(completed + 1, self.settings.max_rounds + 1):
            events.emit("round_started", round=rnd)
            outcomes = await asyncio.gather(
                *(self._step(a, rnd, goal, agent_ids, board, ws, events) for a in agent_ids)
            )
            if all(o.failure == "model_error" for o in outcomes):
                self.conn.execute("UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,))
                events.emit("run_paused", round=rnd, detail=outcomes[0].detail)
                return RunResult(run_id, "paused")
            order = list(zip(agent_ids, outcomes))
            self.rng.shuffle(order)
            posted: list[dict[str, Any]] = []
            with transaction(self.conn):
                for agent_id, outcome in order:
                    posted.append(self._post(board, ws, run_id, rnd, agent_id, outcome))
                self.conn.execute(
                    "INSERT INTO rounds (run_id, round, workspace_commit, completed_at) VALUES (?, ?, ?, ?)",
                    (run_id, rnd, ws.head(), now_iso()),
                )
            for event in posted:
                events.emit("action_posted", **event)
            events.emit("round_completed", round=rnd)
            completed = rnd
            decision = stop_decision(board.entries(), agent_ids, rnd, self.settings)
            if decision:
                return self._close(run_id, goal, board, events, rnd, decision)
        decision = stop_decision(board.entries(), agent_ids, completed, self.settings) or StopDecision("budget")
        return self._close(run_id, goal, board, events, completed, decision)

    async def _step(
        self, agent_id: str, rnd: int, goal: str, agent_ids: Sequence[str],
        board: Board, ws: Workspace, events: EventLog,
    ) -> StepOutcome:
        events.emit("agent_step_started", round=rnd, agent=agent_id)
        inputs = collect_inputs(agent_id=agent_id, goal=goal, round=rnd, board=board, store=self.store,
                                workspace=ws, view_entries=self.settings.view_entries)
        who = make_who(inputs.profiles)
        system = system_prompt(who(agent_id), self.store.private(agent_id)["temperament.md"], self.settings.max_reads)
        client = self.clients[agent_id]
        budget = max(2_000, int(client.context_window * 3 * 0.75) - len(system))
        outcome = await take_step(
            client=client, system=system, view=build_view(inputs, budget_chars=budget), round=rnd,
            max_reads=self.settings.max_reads, reader=make_reader(ws, inputs.entries, who),
            validate=make_validator(inputs.entries, agent_ids),
        )
        events.emit("agent_step_finished", round=rnd, agent=agent_id, action=outcome.action.type, failure=outcome.failure)
        return outcome

    def _fail(self, run_id: str, rnd: int, agent_id: str, kind: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO failures (run_id, round, agent_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, rnd, agent_id, kind, detail, now_iso()),
        )

    def _post(
        self, board: Board, ws: Workspace, run_id: str, rnd: int, agent_id: str, outcome: StepOutcome
    ) -> dict[str, Any]:
        action = outcome.action
        failure = outcome.failure
        if failure:
            entry = board.append(rnd, agent_id, "pass", {"failure": failure})
            self._fail(run_id, rnd, agent_id, failure, outcome.detail)
        elif isinstance(action, WriteFile):
            path = normalise_path(action.path)
            try:
                version = ws.write(path, action.base_version, action.content, author=agent_id, note=action.note)
            except WriteConflict as exc:
                failure = "write_conflict"
                entry = board.append(rnd, agent_id, "pass", {"failure": failure})
                self._fail(run_id, rnd, agent_id, failure, str(exc))
                board.append(rnd, SYSTEM, "write_rejected", {
                    "agent": agent_id, "path": path, "base_version": action.base_version,
                    "current_version": exc.current_version,
                }, refs=[entry.id])
            else:
                entry = board.append(rnd, agent_id, "write_file", {"path": path, "version": version, "note": action.note})
                board.append(rnd, SYSTEM, "artifact_changed", {
                    "agent": agent_id, "path": path, "from_version": action.base_version,
                    "to_version": version, "note": action.note,
                }, refs=[entry.id])
        else:
            entry = board.append(rnd, agent_id, action.type, action.model_dump(exclude={"type"}), refs=target_refs(action))
        if outcome.reasoning:
            self.conn.execute(
                "INSERT INTO traces (entry_id, run_id, agent_id, model, reasoning) VALUES (?, ?, ?, ?, ?)",
                (entry.id, run_id, agent_id, outcome.model, outcome.reasoning),
            )
        return {"round": rnd, "agent": agent_id, "entry": entry.id, "type": entry.type, "failure": failure}

    def _close(
        self, run_id: str, goal: str, board: Board, events: EventLog, rnd: int, decision: StopDecision
    ) -> RunResult:
        with transaction(self.conn):
            board.append(rnd, SYSTEM, "run_closed", {"reason": decision.reason, "call_id": decision.call_id})
            self.conn.execute(
                "UPDATE runs SET status = 'closed', ended_reason = ? WHERE id = ?", (decision.reason, run_id)
            )
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        write_report(self.runs_dir / run_id, goal, board.entries(), decision, who)
        events.emit("run_closed", reason=decision.reason, call_id=decision.call_id)
        return RunResult(run_id, "closed", decision.reason, decision.call_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: 8 passed

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/runner.py tests/conftest.py tests/test_runner.py
git commit -m "feat: add runner with atomic rounds, stop rules, pause and resume"
```

---

### Task 15: CLI

**Files:**
- Create: `src/aiswarm/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces a typer `app` (entry point `aiswarm`) with commands: `init`, `agents new ID --model NAME (--temperament TEXT | --temperament-file PATH)`, `agents introduce ID`, `agents list`, `agents show ID`, `run [GOAL] [--resume RUN_ID] [--agent ID ...] [--max-rounds N]`, `show RUN_ID`, `doctor`. Every command takes `--config/-c PATH` (default `aiswarm.toml`). `make_client` is imported into `aiswarm.cli` so tests can monkeypatch it.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import itertools
import json
import re

import pytest
from typer.testing import CliRunner

import aiswarm.cli as cli
from aiswarm.models.fake import FakeModel

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    counter = itertools.count(1)

    def respond(system, messages):
        first = messages[0]["content"]
        if "introduce yourself" in first.lower():
            n = next(counter)
            return json.dumps({"name": f"Member {n}", "headline": f"Headline {n}", "introduction": f"Hello from {n}."})
        rnd = int(re.search(r"^# Round (\d+)", first, re.M).group(1))
        if rnd == 1:
            return json.dumps({"action": {"type": "propose", "answer_summary": "Blue", "method": "Gut"}})
        return json.dumps({"action": {"type": "pass"}})

    monkeypatch.setattr(cli, "make_client", lambda backend, retries: FakeModel(respond))
    config = tmp_path / "aiswarm.toml"
    result = runner.invoke(cli.app, ["init", "-c", str(config)])
    assert result.exit_code == 0, result.output
    return tmp_path, config


def invoke(*args):
    return runner.invoke(cli.app, [str(a) for a in args])


def test_init_creates_config_community_and_db(project):
    tmp_path, config = project
    assert config.exists()
    assert (tmp_path / "community" / ".git").exists()
    assert (tmp_path / "community" / "community.db").exists()
    assert (tmp_path / "runs").is_dir()


def test_agents_new_introduces_and_lists(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "local", "--temperament", "Curious.", "-c", config)
    assert result.exit_code == 0, result.output
    assert "Member 1 (a) — Headline 1" in result.output
    listing = invoke("agents", "list", "-c", config)
    assert "a" in listing.output and "Member 1" in listing.output and "local" in listing.output
    shown = invoke("agents", "show", "a", "-c", config)
    assert "Curious." in shown.output and "Hello from 1." in shown.output


def test_agents_new_rejects_unknown_backend(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "nope", "--temperament", "x", "-c", config)
    assert result.exit_code != 0
    assert "unknown model backend" in result.output


def test_run_then_show(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    result = invoke("run", "Pick a colour", "--max-rounds", "4", "-c", config)
    assert result.exit_code == 0, result.output
    assert "closed: stagnation" in result.output
    run_id = re.search(r"run (\S+) closed", result.output).group(1)
    shown = invoke("show", run_id, "-c", config)
    assert "Competing positions" in shown.output and "No consensus was reached" in shown.output


def test_run_refuses_agents_without_public_profile(project):
    tmp_path, config = project
    from aiswarm.profiles import ProfileStore
    ProfileStore(tmp_path / "community").create("z", "local", "T.")
    result = invoke("run", "G", "-c", config)
    assert result.exit_code != 0
    assert "agents introduce z" in result.output


def test_missing_config_is_reported(tmp_path):
    result = invoke("agents", "list", "-c", tmp_path / "missing.toml")
    assert result.exit_code != 0
    assert "aiswarm init" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aiswarm.cli'`

- [ ] **Step 3: Implement `src/aiswarm/cli.py`**

```python
"""Command-line interface."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from aiswarm.config import DEFAULT_CONFIG, ClaudeCliBackend, Config, load_config
from aiswarm.db import connect
from aiswarm.introduce import introduce
from aiswarm.models.base import ModelError
from aiswarm.models.claude_cli import ClaudeCliClient, leak_check
from aiswarm.models.factory import make_client
from aiswarm.models.openai_compat import OpenAICompatClient
from aiswarm.profiles import PRIVATE_FILES, ProfileError, ProfileStore
from aiswarm.runner import Runner

app = typer.Typer(no_args_is_help=True, help="A self-organising community of LLM agents.")
agents_app = typer.Typer(no_args_is_help=True, help="Create and inspect agents.")
app.add_typer(agents_app, name="agents")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to aiswarm.toml.")]
DEFAULT_PATH = Path("aiswarm.toml")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=False)
    raise typer.Exit(1)


def _load(config: Path) -> Config:
    if not config.exists():
        _fail(f"{config} not found; run `aiswarm init` first")
    return load_config(config)


@app.command()
def init(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Create a config file (if missing), the community repo, the database, and the runs folder."""
    if not config.exists():
        config.write_text(DEFAULT_CONFIG)
        typer.echo(f"wrote {config} — set models.local.model before using local agents")
    cfg = load_config(config)
    ProfileStore(cfg.community_dir)
    connect(cfg.db_path).close()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"community ready at {cfg.community_dir}")


def _introduce(cfg: Config, store: ProfileStore, agent_id: str) -> None:
    client = make_client(cfg.models[store.record(agent_id).model], cfg.run.model_retries)
    try:
        profile = asyncio.run(introduce(store, agent_id, client))
    except (ProfileError, ModelError) as exc:
        _fail(f"{exc}. Retry with `aiswarm agents introduce {agent_id}`")
    typer.echo(f"{profile.name} ({agent_id}) — {profile.headline}\n\n{profile.introduction}")


@agents_app.command("new")
def agents_new(
    agent_id: str,
    model: Annotated[str, typer.Option(help="Backend name from [models] in the config.")],
    temperament: Annotated[str | None, typer.Option(help="Seed temperament text.")] = None,
    temperament_file: Annotated[Path | None, typer.Option(help="File containing the seed temperament.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Create an agent from a temperament; the agent then writes its own public profile."""
    cfg = _load(config)
    if model not in cfg.models:
        _fail(f"unknown model backend {model!r}; known: {', '.join(cfg.models)}")
    text = temperament_file.read_text() if temperament_file else temperament
    if not text:
        _fail("give --temperament or --temperament-file")
    store = ProfileStore(cfg.community_dir)
    try:
        store.create(agent_id, model, text)
    except ProfileError as exc:
        _fail(str(exc))
    _introduce(cfg, store, agent_id)


@agents_app.command("introduce")
def agents_introduce(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Ask an existing agent to (re)write its first public profile."""
    cfg = _load(config)
    _introduce(cfg, ProfileStore(cfg.community_dir), agent_id)


@agents_app.command("list")
def agents_list(config: ConfigOpt = DEFAULT_PATH) -> None:
    """List agents with their public name, backend, and headline."""
    store = ProfileStore(_load(config).community_dir)
    for agent_id in store.ids():
        profile = store.public(agent_id)
        name, headline = (profile.name, profile.headline) if profile else ("(no profile)", "")
        typer.echo(f"{agent_id}\t{name}\t{store.record(agent_id).model}\t{headline}")


@agents_app.command("show")
def agents_show(agent_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Show an agent's public profile and private files (for you, the observer)."""
    store = ProfileStore(_load(config).community_dir)
    if agent_id not in store.ids():
        _fail(f"no agent {agent_id!r}")
    profile = store.public(agent_id)
    if profile:
        typer.echo(f"# {profile.name} ({agent_id})\n{profile.headline}\n\n{profile.introduction}")
        if profile.former_names:
            typer.echo(f"\nformerly: {', '.join(profile.former_names)}")
    private = store.private(agent_id)
    for filename in PRIVATE_FILES:
        typer.echo(f"\n## {filename}\n{private[filename].strip() or '(empty)'}")


def _print_event(event: dict[str, Any]) -> None:
    kind = event["type"]
    if kind == "round_started":
        typer.echo(f"— round {event['round']} —")
    elif kind == "action_posted":
        suffix = f" ({event['failure']})" if event.get("failure") else ""
        typer.echo(f"  #{event['entry']} {event['agent']} {event['type']}{suffix}")
    elif kind == "run_paused":
        typer.echo(f"paused: {event['detail']}")


@app.command()
def run(
    goal: Annotated[str | None, typer.Argument(help="The goal prompt.")] = None,
    resume: Annotated[str | None, typer.Option(help="Resume a paused or interrupted run.")] = None,
    agent: Annotated[list[str] | None, typer.Option("--agent", help="Agent ids to include (default: all).")] = None,
    max_rounds: Annotated[int | None, typer.Option(help="Override the round budget.")] = None,
    config: ConfigOpt = DEFAULT_PATH,
) -> None:
    """Run the community on a goal (or resume a run)."""
    cfg = _load(config)
    if max_rounds is not None:
        cfg.run.max_rounds = max_rounds
    store = ProfileStore(cfg.community_dir)
    conn = connect(cfg.db_path)
    if resume:
        row = conn.execute("SELECT agent_ids FROM runs WHERE id = ?", (resume,)).fetchone()
        if row is None:
            _fail(f"no run {resume!r}")
        agent_ids = json.loads(row["agent_ids"])
    else:
        if not goal:
            _fail("give a goal, or --resume RUN_ID")
        agent_ids = agent or store.ids()
        if not agent_ids:
            _fail("no agents yet; create some with `aiswarm agents new`")
    for agent_id in agent_ids:
        if agent_id not in store.ids():
            _fail(f"no agent {agent_id!r}")
        if store.public(agent_id) is None:
            _fail(f"{agent_id} has no public profile; run `aiswarm agents introduce {agent_id}`")
        if store.record(agent_id).model not in cfg.models:
            _fail(f"{agent_id} uses unknown model backend {store.record(agent_id).model!r}")
    clients = {a: make_client(cfg.models[store.record(a).model], cfg.run.model_retries) for a in agent_ids}
    runner = Runner(settings=cfg.run, conn=conn, store=store, runs_dir=cfg.runs_dir, clients=clients,
                    sink=_print_event)
    run_id = resume or runner.start(goal or "", agent_ids)
    result = asyncio.run(runner.run(run_id))
    if result.status == "paused":
        typer.echo(f"run {run_id} paused; resume with `aiswarm run --resume {run_id}`")
    else:
        typer.echo(f"run {run_id} closed: {result.reason}")
        typer.echo(f"report: {cfg.runs_dir / run_id / 'report'}")


@app.command()
def show(run_id: str, config: ConfigOpt = DEFAULT_PATH) -> None:
    """Print a run's answer and minority report."""
    report = _load(config).runs_dir / run_id / "report"
    if not report.exists():
        _fail(f"no report for run {run_id!r}")
    typer.echo((report / "answer.md").read_text())
    typer.echo((report / "minority.md").read_text())


@app.command()
def doctor(config: ConfigOpt = DEFAULT_PATH) -> None:
    """Check each model backend: oMLX reachable with the model loaded; claude -p isolated (leak check)."""
    cfg = _load(config)
    healthy = True
    for name, backend in cfg.models.items():
        try:
            if isinstance(backend, ClaudeCliBackend):
                client = ClaudeCliClient(backend, workdir=Path(tempfile.mkdtemp(prefix="aiswarm-claude-")))
                ok, tokens = asyncio.run(leak_check(client))
                detail = f"input tokens {tokens} (limit {backend.leak_threshold_tokens})"
            else:
                models = asyncio.run(OpenAICompatClient(backend).list_models())
                ok = backend.model in models
                detail = f"model {backend.model!r} {'available' if ok else 'not found'}; server lists {len(models)}"
        except ModelError as exc:
            ok, detail = False, str(exc)
        healthy &= ok
        typer.echo(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    if not healthy:
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/cli.py tests/test_cli.py
git commit -m "feat: add aiswarm CLI (init, agents, run, show, doctor)"
```

---

### Task 16: Live smoke tests and first real run

**Files:**
- Create: `tests/smoke/test_live.py`
- Modify: none

**Interfaces:**
- Consumes: `OpenAICompatClient`, `ClaudeCliClient`, `leak_check`, `step_schema`, `parse_action`.
- Environment: `AISWARM_SMOKE_MODEL` (an oMLX model id) enables the oMLX test; the claude tests run when `claude` is on `PATH`.

- [ ] **Step 1: Write the smoke tests**

`tests/smoke/test_live.py`:

```python
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from aiswarm.actions import parse_action, step_schema
from aiswarm.config import ClaudeCliBackend, OpenAIBackend
from aiswarm.models.claude_cli import ClaudeCliClient, leak_check
from aiswarm.models.openai_compat import OpenAICompatClient

pytestmark = pytest.mark.smoke
VIEW = "# Goal\nName a colour.\n\n# Round 1\nThis is the opening round. Propose your own position (action `propose`)."
SYSTEM = 'You are Test (t). Reply with JSON only, in the form {"action": {...}}.'


def claude_client():
    return ClaudeCliClient(ClaudeCliBackend(kind="claude_cli"), workdir=Path(tempfile.mkdtemp(prefix="aiswarm-claude-")))


@pytest.mark.skipif(not os.environ.get("AISWARM_SMOKE_MODEL"), reason="set AISWARM_SMOKE_MODEL to an oMLX model id")
async def test_omlx_returns_a_parseable_action():
    client = OpenAICompatClient(OpenAIBackend(kind="openai", model=os.environ["AISWARM_SMOKE_MODEL"]))
    assert os.environ["AISWARM_SMOKE_MODEL"] in await client.list_models()
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
```

- [ ] **Step 2: Start oMLX and pick a model**

```bash
omlx start
curl -s http://localhost:8000/v1/models | python3 -m json.tool
```

Expected: a JSON list of models. Choose one id (a ~8B instruct model is enough for smoke).

- [ ] **Step 3: Run the smoke tests**

Run: `AISWARM_SMOKE_MODEL=<model-id> uv run pytest -m smoke -v`
Expected: 3 passed. If `test_omlx_returns_a_parseable_action` fails on the request itself, set `json_schema = false` for that backend and note it; if it fails on parsing, record the raw reply in the task report.

- [ ] **Step 4: First real run (manual)**

```bash
mkdir -p ~/aiswarm-lab && cd ~/aiswarm-lab
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm init
# edit aiswarm.toml: set models.local.model = "<model-id>"
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm doctor
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm agents new a --model local --temperament "Reasons from first principles; distrusts consensus."
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm agents new b --model local --temperament "Reasons by analogy; looks for precedent."
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm agents new c --model claude --temperament "Empirical; wants evidence before agreeing."
uv run --project /Users/andrew/Develop/90_PLAYGROUND/AISwarm aiswarm run "What is the most useful habit for learning a new language as an adult?" --max-rounds 6
```

Expected: `doctor` prints `ok` for both backends; each `agents new` prints a self-chosen name, headline, and introduction; `run` prints rounds and actions and ends with `run <id> closed: <reason>` and a report path. Note any `invalid_action` failures per agent in the task report — they are expected occasionally on small models.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Develop/90_PLAYGROUND/AISwarm
git add tests/smoke/test_live.py
git commit -m "test: add live smoke tests for oMLX and claude -p"
```
