"""A baseline run: a closed community run's goal, settings, seed and brief deal, repeated in a fresh room of seed
agents with no norms, no library, no history and no post-run. It lives in its own scratch community under
`<lab>/baselines/` and never writes to the generation's."""

import json
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bikeshed.briefs import RunBriefs
from bikeshed.briefs import load as load_briefs
from bikeshed.config import Config, RunSettings
from bikeshed.db import connect, now_iso
from bikeshed.profiles import ProfileStore

INFO_FILE = "baseline.toml"
# the experiment's settings; model_retries, max_concurrent_calls and the reputation half-life govern the machine
RUN_FIELDS = ("process", "max_rounds", "close_rule", "view_entries", "max_writes", "quorum_slack", "stagnation_rounds",
              "max_reads")


class BaselineError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    run_id: str
    goal: str
    goal_id: str | None
    goal_category: str | None
    is_benchmark: bool
    agent_ids: tuple[str, ...]
    seed: int
    run: dict[str, Any]           # [run] settings from the last start or resume provenance
    backends: dict[str, str]      # agent -> backend name it ran on
    models: dict[str, str]        # agent -> model that backend named then
    briefs: RunBriefs
    temperaments: dict[str, str]  # agent -> first committed temperament.md


def read_source(conn: sqlite3.Connection, store: ProfileStore, run_id: str) -> Source:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise BaselineError(f"no run {run_id!r}")
    if row["status"] != "closed":
        raise BaselineError(f"run {run_id} is not closed; a baseline mirrors a finished run")
    if row["seed"] is None:
        raise BaselineError(f"run {run_id} has no recorded seed, so it cannot be mirrored")
    prov = conn.execute("SELECT phase, config, models FROM run_provenance WHERE run_id = ? "
                        "AND phase IN ('start', 'resume') ORDER BY id", (run_id,)).fetchall()
    if not prov:
        raise BaselineError(f"run {run_id} has no provenance, so its settings and models are unknown")
    started = next((p for p in prov if p["phase"] == "start"), prov[0])
    run_cfg = json.loads(prov[-1]["config"]).get("run") or {}
    models = json.loads(started["models"])
    agent_ids = tuple(json.loads(row["agent_ids"]))
    missing = [a for a in agent_ids if a not in models]
    if missing:
        raise BaselineError(f"run {run_id}'s provenance names no model for {', '.join(missing)}")
    temperaments = {}
    for agent_id in agent_ids:
        first = store.first_version(agent_id, "temperament.md")
        if first is None:
            raise BaselineError(f"{agent_id} has no committed temperament in the community")
        temperaments[agent_id] = first
    return Source(run_id, row["goal"], row["goal_id"], row["goal_category"], bool(row["is_benchmark"]), agent_ids,
                  row["seed"], {k: run_cfg[k] for k in RUN_FIELDS if k in run_cfg},
                  {a: models[a]["backend"] for a in agent_ids}, {a: models[a].get("model", "") for a in agent_ids},
                  load_briefs(conn, run_id), temperaments)


def check_backends(source: Source, cfg: Config) -> None:
    """Every agent must run on the backend it ran on then, and that backend must still name the same model."""
    for agent_id in source.agent_ids:
        name = source.backends[agent_id]
        backend = cfg.models.get(name)
        if backend is None:
            raise BaselineError(f"{agent_id} ran on backend {name!r}, which is no longer in the config")
        now = backend.model_dump().get("model", "")
        if now != source.models[agent_id]:
            raise BaselineError(f"backend {name!r} now names {now!r}; {agent_id} ran on {source.models[agent_id]!r}")


def settings_for(source: Source, cfg: Config) -> RunSettings:
    return RunSettings.model_validate({**cfg.run.model_dump(), **source.run})


def home(cfg: Config) -> Path:
    return cfg.community_dir.parent / "baselines"


def new_directory(home_dir: Path, run_id: str) -> Path:
    k = 1
    while (home_dir / f"{run_id}-b{k}").exists():
        k += 1
    return home_dir / f"{run_id}-b{k}"


def scratch_config(cfg: Config, directory: Path, settings: RunSettings) -> Config:
    """The config a baseline runs under: its own community and runs, the source run's settings, no library."""
    scratch = cfg.model_copy(deep=True)
    scratch.community_dir = directory / "community"
    scratch.runs_dir = directory / "runs"
    scratch.run = settings
    scratch.library.embeddings = None
    return scratch


def prepare(directory: Path, source: Source) -> ProfileStore:
    """A fresh community holding the source run's agents as seeds: first temperament, same backend, no profile yet."""
    store = ProfileStore(directory / "community")
    connect(directory / "community" / "community.db").close()
    (directory / "runs").mkdir(parents=True, exist_ok=True)
    for agent_id in source.agent_ids:
        store.create(agent_id, source.backends[agent_id], source.temperaments[agent_id])
    _write_info(directory, {"source_run": source.run_id, "created_at": now_iso()})
    return store


def read_info(directory: Path) -> dict[str, str]:
    path = directory / INFO_FILE
    if not path.exists():
        raise BaselineError(f"no baseline at {directory}")
    return tomllib.loads(path.read_text())


def record_run(directory: Path, run_id: str) -> None:
    _write_info(directory, {**read_info(directory), "baseline_run": run_id})


def _write_info(directory: Path, info: dict[str, str]) -> None:
    (directory / INFO_FILE).write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in info.items()))
