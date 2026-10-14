"""User-only export of the community and Observer data to plain text files, for analysis outside Bikeshed.

Read-only like the Observer: both databases are opened with `connect_readonly`, the community repo is only read
with git, and the output may not live inside the community or runs directories. Nothing here reaches an agent.
"""

import base64
import csv
import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from bikeshed.config import Config
from bikeshed.db import connect_readonly

RUN_FILES = ("events.jsonl",)
RUN_DIRS = ("report", "observer", "workspace")

README = """\
# Bikeshed data export

Generated {generated} from `{source}`. Filter: {filter}.

Everything here is plain text. JSONL files hold one JSON object per line.

- `manifest.json`: source paths, schema versions, filter, row count per table.
- `runs.csv`: one row per run with its setup (models, close rule, code commit) and behaviour counts.
- `community/<table>.jsonl`: every row of every table in `community.db`, columns as stored. JSON held in
  TEXT columns (payloads, refs, provenance) stays a string; BLOBs (embeddings) become `{{"base64": ...}}`.
  Tables with a `run_id` column (and `runs` itself) are filtered; the rest are exported whole.
- `community/schema.sql`: the database schema.
- `community/profile_history.jsonl`: every change to a file in the community git repo (agent profiles, journals,
  norms, goals), oldest first, with the file's full content after the commit (`null` when deleted). Not filtered.
- `community/files/`: the community repo's tracked files as they are now.
- `observer/<table>.jsonl`, `observer/schema.sql`: the same for the Observer's `observer.db`, when it exists.
- `runs/<run-id>/transcript.md`: the run as a readable transcript, with failures and the post-run reviews.
- `runs/<run-id>/entries.jsonl`: the run's entries with `payload`, `refs` and `visible_to` parsed, and the
  model's `reasoning` where a trace exists.
- `runs/<run-id>/events.jsonl`, `report/`, `observer/`, `workspace/`: copied from the run directory as they are
  (the workspace without its `.git`).
"""


def export(cfg: Config, out: Path, *, run_ids: list[str] | None = None, since: str | None = None) -> Path:
    """Write the export to `out`, which must be empty or absent. Returns `out`."""
    out = out.resolve()
    for root in (cfg.community_dir, cfg.runs_dir):
        if out.is_relative_to(root.resolve()):
            raise ValueError(f"refusing to export inside {root}")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)

    conn = connect_readonly(cfg.db_path)
    try:
        selected = _select_runs(conn, run_ids, since)
        manifest: dict[str, Any] = {
            "generated": datetime.now(UTC).isoformat(timespec="seconds"),
            "filter": {"run_ids": run_ids, "since": since, "runs": selected},
            "community": _dump_db(conn, out / "community", selected, cfg.db_path),
            "observer": None,
        }
        _profile_history(cfg.community_dir, out / "community")
        _write_runs_csv(conn, selected, out / "runs.csv")
        for run_id in selected:
            _export_run(conn, cfg, run_id, out / "runs" / run_id)
    finally:
        conn.close()

    observer_db = cfg.observer.dir / "observer.db"
    if observer_db.exists():
        oconn = connect_readonly(observer_db)
        try:
            manifest["observer"] = _dump_db(oconn, out / "observer", selected, observer_db)
        finally:
            oconn.close()

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    described = ", ".join(f"{k}={v}" for k, v in (("runs", run_ids), ("since", since)) if v) or "none (everything)"
    (out / "README.md").write_text(README.format(generated=manifest["generated"], source=cfg.community_dir.parent,
                                                 filter=described))
    return out


def _select_runs(conn: sqlite3.Connection, run_ids: list[str] | None, since: str | None) -> list[str]:
    rows = conn.execute("SELECT id, created_at FROM runs ORDER BY created_at, id").fetchall()
    known = {r["id"] for r in rows}
    if run_ids:
        missing = [r for r in run_ids if r not in known]
        if missing:
            raise ValueError(f"unknown run(s): {', '.join(missing)}")
    return [r["id"] for r in rows
            if (not run_ids or r["id"] in run_ids) and (not since or r["created_at"] >= since)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    return value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _dump_db(conn: sqlite3.Connection, out: Path, selected: list[str], source: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    schema = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid")]
    (out / "schema.sql").write_text(";\n\n".join(schema) + ";\n")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    marks = ",".join("?" * len(selected))
    counts = {}
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        key = "id" if table == "runs" else "run_id" if "run_id" in columns else None
        query = f"SELECT * FROM {table}" + (f" WHERE {key} IN ({marks})" if key else "") + " ORDER BY rowid"
        try:
            cursor = conn.execute(query, selected if key else [])
        except sqlite3.OperationalError:  # WITHOUT ROWID tables
            cursor = conn.execute(query.removesuffix(" ORDER BY rowid"), selected if key else [])
        rows = [{c: _jsonable(row[c]) for c in columns} for row in cursor]
        _write_jsonl(out / f"{table}.jsonl", rows)
        counts[table] = len(rows)
    return {"path": str(source), "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "tables": counts}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _profile_history(community: Path, out: Path) -> None:
    if not (community / ".git").exists():
        return
    log = _git(community, "log", "--reverse", "--no-renames", "--format=%x1e%H%x1f%aI%x1f%an%x1f%s", "--name-status")
    rows = []
    for block in log.split("\x1e")[1:]:
        header, _, changes = block.partition("\n")
        commit, date, author, subject = header.split("\x1f")
        for line in changes.splitlines():
            if not line.strip():
                continue
            status, path = line.split("\t", 1)
            content = None if status == "D" else _git(community, "show", f"{commit}:{path}")
            rows.append({"commit": commit, "date": date, "author": author, "subject": subject,
                         "status": status, "path": path, "content": content})
    _write_jsonl(out / "profile_history.jsonl", rows)
    for path in _git(community, "ls-files").splitlines():
        target = out / "files" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(community / path, target)


def _loads(text: str | None) -> Any:
    return json.loads(text) if text else None


def _run_row(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    entries = conn.execute("SELECT type, payload FROM entries WHERE run_id = ?", (run_id,)).fetchall()
    types = [e["type"] for e in entries]
    votes = [json.loads(e["payload"]) for e in entries if e["type"] == "vote"]
    closed = next((json.loads(e["payload"]) for e in entries if e["type"] == "run_closed"), {})
    start = conn.execute("SELECT * FROM run_provenance WHERE run_id = ? AND phase = 'start' ORDER BY id LIMIT 1",
                         (run_id,)).fetchone()
    config = _loads(start["config"]) if start else {}
    models = _loads(start["models"]) if start else {}
    reviews = conn.execute("SELECT is_self, overall FROM reviews WHERE run_id = ?", (run_id,)).fetchall()
    peer = [r["overall"] for r in reviews if not r["is_self"]]
    own = [r["overall"] for r in reviews if r["is_self"]]
    rounds = conn.execute("SELECT MAX(round) FROM rounds WHERE run_id = ?", (run_id,)).fetchone()[0]
    return {
        "run_id": run_id, "created_at": run["created_at"], "goal": run["goal"], "goal_id": run["goal_id"],
        "goal_category": run["goal_category"], "is_benchmark": run["is_benchmark"], "process": run["process"],
        "status": run["status"], "ended_reason": run["ended_reason"], "closing_call": closed.get("call_id", ""),
        "agents": ";".join(json.loads(run["agent_ids"])),
        "models": ";".join(sorted({m.get("model", "") for m in models.values()})),
        "close_rule": (config.get("run") or {}).get("close_rule", ""),
        "max_rounds": (config.get("run") or {}).get("max_rounds", ""),
        "code_commit": start["code_commit"] if start else "", "code_dirty": start["code_dirty"] if start else "",
        "rounds": rounds if rounds is not None else 0, "entries": len(entries),
        "think_notes": types.count("think"), "proposals": types.count("propose"),
        "critiques": types.count("critique"), "refines": types.count("refine"), "adopts": types.count("adopt"),
        "calls": types.count("call_consensus"), "votes": len(votes),
        "non_support_votes": sum(v.get("stance") != "support" for v in votes),
        "unique_vote_reasons": len({v.get("reason") for v in votes}), "passes": types.count("pass"),
        "scorer_accepted": types.count("scorer_accepted"), "scorer_rejected": types.count("scorer_rejected"),
        "failures": conn.execute("SELECT COUNT(*) FROM failures WHERE run_id = ?", (run_id,)).fetchone()[0],
        "peer_reviews": len(peer), "mean_peer_rating": round(fmean(peer), 2) if peer else "",
        "mean_self_rating": round(fmean(own), 2) if own else "",
    }


def _write_runs_csv(conn: sqlite3.Connection, selected: list[str], path: Path) -> None:
    rows = [_run_row(conn, run_id) for run_id in selected]
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _export_run(conn: sqlite3.Connection, cfg: Config, run_id: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    traces = {r["entry_id"]: r for r in conn.execute("SELECT * FROM traces WHERE run_id = ?", (run_id,))}
    entries = []
    for e in conn.execute("SELECT * FROM entries WHERE run_id = ? ORDER BY id", (run_id,)):
        trace = traces.get(e["id"])
        entries.append({"id": e["id"], "run_id": run_id, "round": e["round"], "author": e["author"],
                        "type": e["type"], "to": e["to_"], "refs": _loads(e["refs"]),
                        "visible_to": _loads(e["visible_to"]), "payload": _loads(e["payload"]),
                        "created_at": e["created_at"], "model": trace["model"] if trace else None,
                        "reasoning": trace["reasoning"] if trace else None})
    _write_jsonl(out / "entries.jsonl", entries)

    failures: dict[int, list[sqlite3.Row]] = {}
    for f in conn.execute("SELECT * FROM failures WHERE run_id = ? ORDER BY id", (run_id,)):
        failures.setdefault(f["round"], []).append(f)
    row = _run_row(conn, run_id)
    lines = [f"# {run_id}: {run['goal']}", "",
             f"- status: {run['status']} ({run['ended_reason'] or 'open'}), process {run['process']}, "
             f"created {run['created_at']}",
             f"- agents: {', '.join(json.loads(run['agent_ids']))}",
             f"- models: {row['models'] or 'unknown'}; close rule: {row['close_rule'] or 'unknown'}; "
             f"code: {row['code_commit'] or 'unknown'}{' (dirty)' if row['code_dirty'] else ''}"]
    current = None
    for e in entries:
        if e["round"] != current:
            if current is not None and failures.get(current):
                lines += _failure_lines(failures.pop(current))
            current = e["round"]
            lines += ["", f"## Round {current}"]
        audience = f"private to {', '.join(e['visible_to'])}" if e["visible_to"] else f"to {e['to']}"
        refs = f", refs {', '.join(map(str, e['refs']))}" if e["refs"] else ""
        lines += ["", f"### #{e['id']} {e['author']} {e['type']} ({audience}{refs})"]
        for key, value in (e["payload"] or {}).items():
            lines.append(f"- **{key}:** {_render_value(value)}")
    for remaining in failures.values():
        lines += _failure_lines(remaining)
    lines += _review_lines(conn, run_id)
    (out / "transcript.md").write_text("\n".join(lines) + "\n")

    source = cfg.runs_dir / run_id
    for name in RUN_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, out / name)
    for name in RUN_DIRS:
        if (source / name).is_dir():
            shutil.copytree(source / name, out / name, ignore=shutil.ignore_patterns(".git"))


def _failure_lines(failures: list[sqlite3.Row]) -> list[str]:
    lines = ["", "### Failures"]
    lines += [f"- {f['agent_id']} {f['kind']}: {f['detail']}" for f in failures]
    return lines


def _review_lines(conn: sqlite3.Connection, run_id: str) -> list[str]:
    reviews = conn.execute("SELECT * FROM reviews WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    if not reviews:
        return []
    lines = ["", "## Post-run reviews"]
    for r in reviews:
        lines += ["", f"### {r['reviewer']} on {r['reviewee']}{' (self)' if r['is_self'] else ''}: {r['overall']}/5",
                  f"- **more of:** {r['more_of']}", f"- **less of:** {r['less_of']}",
                  f"- **reason:** {r['overall_reason']}", f"- **evidence:** {r['evidence']}"]
    return lines
