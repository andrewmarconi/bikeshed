"""The goal queue (community/goals.toml). Categories are for the Observer only; no agent ever sees this file."""

import json
import re
import sqlite3
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from bikeshed.actions import summarise_validation
from bikeshed.profiles import ProfileStore

GOALS_FILE = "goals.toml"
BENCHMARK_ID = "benchmark"
GOAL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TEXT_MAX = 500
# unsettled: no data could settle it (taste, values). underspecified: the question leaves out details the group must supply.
Category = Literal["unsettled", "underspecified"]


class GoalError(ValueError):
    pass


def _text(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= TEXT_MAX or "\n" in value:
        raise ValueError(f"goal text must be a single line of 1-{TEXT_MAX} characters")
    if any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("goal text must not contain control characters")
    return value


class Benchmark(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in goals.toml is an error, not ignored

    text: str
    category: Category
    every: int = Field(5, ge=1)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _text(value)


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    category: Category

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if value == BENCHMARK_ID:
            raise ValueError(f"the id {BENCHMARK_ID!r} is reserved")
        if not GOAL_ID.match(value):
            raise ValueError("goal id must be lowercase letters, digits or '-', max 48")
        return value

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _text(value)


class GoalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark: Benchmark
    goals: list[Goal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "GoalFile":
        seen: set[str] = set()
        for goal in self.goals:
            if goal.id in seen:
                raise ValueError(f"duplicate goal id {goal.id!r}")
            seen.add(goal.id)
        return self


DEFAULT_GOALS = GoalFile(
    benchmark=Benchmark(text="Pick one pizza topping combination the whole group will share.",
                        category="underspecified"),
    goals=[
        Goal(id="best-fruit", text="Come to an agreement on the best fruit.", category="unsettled"),
        Goal(id="hot-dog-sandwich", text="Decide, once and for all, whether a hot dog is a sandwich.",
             category="unsettled"),
        Goal(id="greatest-board-game", text="Settle on the greatest board game of all time.", category="unsettled"),
        Goal(id="dishwasher", text="Agree on the correct way to load a dishwasher.", category="unsettled"),
        Goal(id="picnic-basket", text="Plan a picnic menu that has to fit in a single basket.",
             category="underspecified"),
        Goal(id="road-trip-playlist",
             text="Choose three songs for a road-trip playlist that everyone in the car must listen to.",
             category="underspecified"),
        Goal(id="twenty-dollar-sunday", text="Design the ideal Sunday with only $20 to spend.",
             category="underspecified"),
        Goal(id="airplane-recline", text="Decide whether it's okay to recline your seat on a plane.",
             category="unsettled"),
        Goal(id="group-dinner", text="Choose where the group should go for dinner.", category="underspecified"),
        Goal(id="late-text", text="Agree on the latest acceptable time to text someone.", category="unsettled"),
        Goal(id="team-offsite", text="Plan the team's next offsite.", category="underspecified"),
        Goal(id="project-name", text="Pick a name for the new project.", category="underspecified"),
    ],
)


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def render_goals(gf: GoalFile) -> str:
    b = gf.benchmark
    lines = [
        "# Bikeshed goal queue. Categories are for the Observer only; no agent ever sees this file.",
        "", "[benchmark]", f"text = {_q(b.text)}", f"category = {_q(b.category)}", f"every = {b.every}",
    ]
    for goal in gf.goals:
        lines += ["", "[[goals]]", f"id = {_q(goal.id)}", f"text = {_q(goal.text)}", f"category = {_q(goal.category)}"]
    return "\n".join(lines) + "\n"


def load_goals(path: Path) -> GoalFile:
    if not path.exists():
        raise GoalError(f"{path} not found; run `bikeshed init`")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GoalError(f"{path}: not valid UTF-8 ({exc.reason} at byte {exc.start})") from exc
    except OSError as exc:
        raise GoalError(f"{path}: {exc.strerror or exc}") from exc
    try:
        return GoalFile.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise GoalError(f"{path}: {exc}") from exc
    except ValidationError as exc:
        raise GoalError(f"{path}: {summarise_validation(exc)}") from exc


def seed_goals(store: ProfileStore) -> bool:
    """Write the starter queue if the community has none. Returns True if it wrote one."""
    if (store.root / GOALS_FILE).exists():
        return False
    store.write_community_file(GOALS_FILE, render_goals(DEFAULT_GOALS), "seed goal queue", author="bikeshed")
    return True


def _slug(text: str, taken: set[str]) -> str:
    base = "-".join(re.findall(r"[a-z0-9]+", text.lower())[:5])[:40].strip("-") or "goal"
    if base == BENCHMARK_ID:
        base = "benchmark-goal"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def add_goal(store: ProfileStore, text: str, category: str, goal_id: str | None = None) -> Goal:
    gf = load_goals(store.root / GOALS_FILE)
    try:
        goal = Goal(id=goal_id or _slug(text, {g.id for g in gf.goals}), text=text, category=category)
        updated = GoalFile(benchmark=gf.benchmark, goals=[*gf.goals, goal])
    except ValidationError as exc:
        raise GoalError(summarise_validation(exc)) from exc
    text = render_goals(updated)
    try:  # never commit a file the queue could not read back
        GoalFile.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise GoalError(f"goal could not be saved: {exc}") from exc
    store.write_community_file(GOALS_FILE, text, f"add goal {goal.id}", author="bikeshed")
    return goal


@dataclass(frozen=True)
class Selected:
    id: str
    text: str
    category: str
    is_benchmark: bool


def _last_benchmark_rowid(conn: sqlite3.Connection) -> int | None:
    return conn.execute("SELECT MAX(rowid) FROM runs WHERE is_benchmark = 1").fetchone()[0]


def runs_since_benchmark(conn: sqlite3.Connection) -> int | None:
    """Runs of any kind started since the last benchmark run; None if it has never run."""
    last = _last_benchmark_rowid(conn)
    if last is None:
        return None
    return conn.execute("SELECT COUNT(*) FROM runs WHERE rowid > ?", (last,)).fetchone()[0]


def benchmark_due(conn: sqlite3.Connection, every: int) -> bool:
    since = runs_since_benchmark(conn)
    return since is None or since >= every - 1


def used_goals(conn: sqlite3.Connection) -> dict[str, str]:
    """Queue goal id -> the first run that used it."""
    used: dict[str, str] = {}
    for row in conn.execute("SELECT goal_id, id FROM runs WHERE goal_id IS NOT NULL AND is_benchmark = 0 ORDER BY rowid"):
        used.setdefault(row["goal_id"], row["id"])
    return used


def next_goal(conn: sqlite3.Connection, gf: GoalFile) -> Selected:
    """The benchmark when due, else the first unused queue goal. Selecting does not consume a goal; starting a run does."""
    if benchmark_due(conn, gf.benchmark.every):
        return Selected(BENCHMARK_ID, gf.benchmark.text, gf.benchmark.category, True)
    used = used_goals(conn)
    for goal in gf.goals:
        if goal.id not in used:
            return Selected(goal.id, goal.text, goal.category, False)
    raise GoalError("goal queue is empty; add goals with `bikeshed goals add`")
