"""Run provenance: what produced each run, so a harness change is never mistaken for emergence."""

import asyncio
import hashlib
import json
import sqlite3
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import aiswarm
from aiswarm.config import Config, OpenAIBackend
from aiswarm.db import now_iso
from aiswarm.gitrepo import GitError, GitRepo

Phase = Literal["start", "resume", "postrun"]
Probe = Callable[[OpenAIBackend], Awaitable[dict[str, Any]]]
_PATH_KEYS = ("community_dir", "runs_dir")


@dataclass(frozen=True)
class Provenance:
    run_id: str
    phase: str
    code_commit: str | None
    code_dirty: bool | None
    config: dict[str, Any]
    prompts: dict[str, dict[str, str]]
    models: dict[str, dict[str, Any]]
    servers: dict[str, dict[str, Any]]
    seed: int | None
    fingerprint: str
    captured_at: str


def _without_keys(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _without_keys(v) for k, v in data.items() if k != "api_key"}
    if isinstance(data, list):
        return [_without_keys(v) for v in data]
    return data


def code_state(code_dir: Path | None = None) -> tuple[str | None, bool | None]:
    """The AISwarm git commit and whether the checkout has uncommitted changes; (None, None) outside git."""
    repo = GitRepo(code_dir or Path(aiswarm.__file__).parent)
    try:
        commit = repo.head()
        if commit is None or not repo.tracks("__init__.py"):
            return None, None  # e.g. installed into a venv inside some other project's repo
        return commit, repo.is_dirty()
    except (GitError, OSError):
        return None, None


def prompt_snapshot() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted(files("aiswarm.prompts").iterdir(), key=lambda p: p.name):
        if path.name.endswith(".md"):
            text = path.read_text()
            out[path.name] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}
    return out


async def probe_server(backend: OpenAIBackend) -> dict[str, Any]:
    """The server's model list and, where exposed (oMLX), its /health report. Never raises."""
    def get(url: str) -> Any:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {backend.api_key}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    base = backend.base_url.rstrip("/")
    out: dict[str, Any] = {"models": None, "health": None, "error": None}
    try:
        listing = await asyncio.to_thread(get, base + "/models")
        out["models"] = sorted(item["id"] for item in listing.get("data", []))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        out["health"] = await asyncio.to_thread(get, base.removesuffix("/v1") + "/health")
    except Exception:
        pass  # oMLX serves /health; other OpenAI-compatible servers may not
    return out


def _fingerprint(code_commit: str | None, code_dirty: bool | None, config: dict[str, Any],
                 prompts: dict[str, dict[str, str]], models: dict[str, dict[str, Any]]) -> str:
    payload = {
        "code": code_commit, "dirty": code_dirty,
        "config": {k: v for k, v in config.items() if k not in _PATH_KEYS},
        "prompts": {name: p["sha256"] for name, p in prompts.items()},
        "models": models,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def capture(
    conn: sqlite3.Connection, run_id: str, phase: Phase, cfg: Config, agent_backends: Mapping[str, str],
    *, probe: Probe = probe_server, code_dir: Path | None = None,
) -> Provenance:
    """Record what is about to produce (or continue) a run. Never fails because a server is unreachable."""
    commit, dirty = code_state(code_dir)
    used = set(agent_backends.values()) | {n for n in (cfg.report.narrator, cfg.library.embeddings) if n}
    # Only backends this run uses: an unused one (e.g. the Observer's judge) is not part of the harness.
    config = _without_keys(cfg.model_dump(mode="json", exclude={"observer"}))
    config["models"] = {name: spec for name, spec in config["models"].items() if name in used}
    prompts = prompt_snapshot()
    models = {agent: {"backend": name, **_without_keys(cfg.models[name].model_dump(mode="json"))}
              for agent, name in sorted(agent_backends.items())}
    servers: dict[str, dict[str, Any]] = {}
    for name in sorted(used):
        backend = cfg.models[name]
        if isinstance(backend, OpenAIBackend) and backend.base_url not in servers:
            try:
                servers[backend.base_url] = await probe(backend)
            except Exception as exc:
                servers[backend.base_url] = {"models": None, "health": None, "error": f"{type(exc).__name__}: {exc}"}
    row = conn.execute("SELECT seed FROM runs WHERE id = ?", (run_id,)).fetchone()
    prov = Provenance(
        run_id=run_id, phase=phase, code_commit=commit, code_dirty=dirty, config=config, prompts=prompts,
        models=models, servers=servers, seed=row["seed"] if row else None,
        fingerprint=_fingerprint(commit, dirty, config, prompts, models), captured_at=now_iso(),
    )
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, phase, commit, None if dirty is None else int(dirty), json.dumps(config), json.dumps(prompts),
         json.dumps(models), json.dumps(servers), prov.seed, prov.fingerprint, prov.captured_at),
    )
    return prov


def _row(row: sqlite3.Row) -> Provenance:
    return Provenance(
        run_id=row["run_id"], phase=row["phase"], code_commit=row["code_commit"],
        code_dirty=None if row["code_dirty"] is None else bool(row["code_dirty"]),
        config=json.loads(row["config"]), prompts=json.loads(row["prompts"]), models=json.loads(row["models"]),
        servers=json.loads(row["servers"]), seed=row["seed"], fingerprint=row["fingerprint"],
        captured_at=row["captured_at"],
    )


def provenance_rows(conn: sqlite3.Connection, run_id: str) -> list[Provenance]:
    return [_row(r) for r in conn.execute("SELECT * FROM run_provenance WHERE run_id = ? ORDER BY id", (run_id,))]


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(data, dict):
        return {prefix: data}
    out: dict[str, Any] = {}
    for key, value in data.items():
        out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    return out


def _diff(old: Provenance, new: Provenance) -> list[str]:
    changes: list[str] = []
    if old.code_commit != new.code_commit:
        changes.append(f"code commit {(old.code_commit or 'none')[:8]} → {(new.code_commit or 'none')[:8]}")
    if old.code_dirty != new.code_dirty:
        changes.append("uncommitted code changes" if new.code_dirty else "code changes committed")
    prompts = sorted(name for name in old.prompts.keys() | new.prompts.keys()
                     if old.prompts.get(name, {}).get("sha256") != new.prompts.get(name, {}).get("sha256"))
    if prompts:
        changes.append("prompts " + ", ".join(prompts))
    before = _flatten({k: v for k, v in old.config.items() if k not in _PATH_KEYS})
    after = _flatten({k: v for k, v in new.config.items() if k not in _PATH_KEYS})
    keys = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    if keys:
        changes.append("config " + ", ".join(keys))
    agents = sorted(a for a in old.models.keys() | new.models.keys() if old.models.get(a) != new.models.get(a))
    if agents:
        changes.append("models for " + ", ".join(agents))
    return changes


def provenance_changes(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """How this run's harness differs from the previous run's, and any change during the run itself."""
    rows = provenance_rows(conn, run_id)
    start = next((r for r in rows if r.phase == "start"), None)
    if start is None:
        return []
    out: list[str] = []
    earlier = conn.execute(
        "SELECT id FROM runs WHERE rowid < (SELECT rowid FROM runs WHERE id = ?) ORDER BY rowid DESC", (run_id,)
    ).fetchall()
    for row in earlier:
        previous = next((r for r in provenance_rows(conn, row["id"]) if r.phase == "start"), None)
        if previous is not None:
            if previous.fingerprint != start.fingerprint:
                out += [f"since run {previous.run_id}: {c}" for c in _diff(previous, start)]
            break
    for later in rows:
        if later.phase != "start" and later.fingerprint != start.fingerprint:
            out += [f"at {later.phase} ({later.captured_at}): {c}" for c in _diff(start, later)]
    return out
