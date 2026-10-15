"""Baseline runs and model variants, read only: each scratch room under <lab>/baselines/ that mirrors a closed community
run."""

import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bikeshed.db import connect_readonly
from bikeshed.observer import metrics as m
from bikeshed.observer import scoring as sc
from bikeshed.observer.copying import copies
from bikeshed.profiles import ProfileError, ProfileStore
from bikeshed.view import make_who


@dataclass(frozen=True)
class Baseline:
    id: str
    source_run: str
    run: m.RunInfo
    rounds: int
    calls: int
    accepted: int
    rejected: int
    answer: m.BenchmarkAnswer
    mix: dict[str, Counter]
    copied: int
    backend: str | None = None   # a model variant's backend; None for a plain baseline

    @property
    def label(self) -> str:
        return f"variant ({self.backend})" if self.backend else "baseline"


def find(home: Path) -> list[Baseline]:
    """Closed baselines, oldest directory name first. Open, unrecorded or unreadable ones are skipped."""
    out = []
    for directory in sorted(home.glob("*/")) if home.exists() else []:
        info_path = directory / "baseline.toml"
        db = directory / "community" / "community.db"
        if not info_path.exists() or not db.exists():
            continue
        info = tomllib.loads(info_path.read_text())
        conn = connect_readonly(db)
        run = next((r for r in m.closed_runs(conn) if r.id == info.get("baseline_run")), None)
        if run is None:
            continue
        try:
            store = ProfileStore(directory / "community", create=False)
            who = make_who({i: store.public(i) for i in store.ids()})
        except ProfileError:
            who = make_who({})
        rulings = sc.rulings(conn, run.id)
        rounds = conn.execute("SELECT MAX(round) FROM rounds WHERE run_id = ?", (run.id,)).fetchone()[0] or 0
        calls = conn.execute("SELECT COUNT(*) FROM entries WHERE run_id = ? AND type = 'call_consensus'",
                             (run.id,)).fetchone()[0]
        out.append(Baseline(directory.name, info["source_run"], run, rounds, calls,
                            sum(r.accepted for r in rulings), sum(not r.accepted for r in rulings),
                            m.run_answer(conn, run, who), m.action_mix(conn, run),
                            len(copies(conn, run, directory / "runs")), info.get("backend")))
    return out
