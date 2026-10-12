"""One `aiswarm observe`: metrics, judgments, charts and reports for closed runs."""

import asyncio
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from aiswarm.config import Config
from aiswarm.db import connect_readonly
from aiswarm.models.base import ModelClient, limit_clients
from aiswarm.observer import metrics as m
from aiswarm.observer import process2 as p2
from aiswarm.observer import scoring as sc
from aiswarm.observer.charts import network_chart, trend_chart
from aiswarm.observer.codebook import theme_counts, update_codebook
from aiswarm.observer.inputs import (
    benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs, think_vs_act_inputs,
)
from aiswarm.observer.judge import ANALYSES, Judge
from aiswarm.observer.report import (
    Process2Section, Process2Summary, RunSection, ScorerSummary, SummarySection, render_run, render_summary,
)
from aiswarm.observer.store import ObserverStore
from aiswarm.profiles import ProfileError, ProfileStore
from aiswarm.provenance import provenance_changes
from aiswarm.view import Who, make_who

JUDGE_CONCURRENCY = 3


@dataclass(frozen=True)
class ObserveResult:
    written: list[Path]
    judged: int
    failed: int
    in_progress: list[str]


async def _judge_run(judge: Judge, ostore: ObserverStore, conn: sqlite3.Connection, store: ProfileStore,
                     runs: list[m.RunInfo], run: m.RunInfo, who: Who, answers: list[m.BenchmarkAnswer]) -> None:
    jobs = []
    for agent in run.agent_ids:
        jobs.append(judge.run(ANALYSES["themes"], run_id=run.id, agent_id=agent,
                              inputs=themes_inputs(conn, store, run, agent)))
        if (pp := private_public_inputs(conn, run, agent, who)) is not None:
            jobs.append(judge.run(ANALYSES["private_public"], run_id=run.id, agent_id=agent, inputs=pp))
        if run.process == 2 and (tva := think_vs_act_inputs(conn, run, agent, who)) is not None:
            jobs.append(judge.run(ANALYSES["think_vs_act"], run_id=run.id, agent_id=agent, inputs=tva))
        jobs.append(judge.run(ANALYSES["self_image"], run_id=run.id, agent_id=agent,
                              inputs=self_image_inputs(store, runs, run, agent)))
    if run.is_benchmark:
        jobs.append(judge.run(ANALYSES["benchmark"], run_id=run.id, agent_id=None,
                              inputs=benchmark_inputs(answers, run.id)))
    await asyncio.gather(*jobs)
    labels = [t["label"] for j in ostore.judgments(run.id, "themes") if j.status == "ok" for t in j.result["themes"]]
    await update_codebook(judge, ostore, run.id, labels)


def _process2_section(conn: sqlite3.Connection, run: m.RunInfo, who: Who, out_dir: Path,
                      written: list[Path]) -> Process2Section | None:
    if run.process != 2:
        return None
    network = p2.pm_network(conn, run)
    chart = None
    if network:
        path = network_chart(out_dir / "charts" / f"network_{run.id}.png", f"Private messages: {run.tag}",
                             {a: who(a) for a in run.agent_ids}, network)
        written.append(path)
        chart = f"../charts/{path.name}"
    return Process2Section(network, chart, p2.poll_participation(conn, run), p2.speaking_order(conn, run))


async def observe(cfg: Config, *, run_id: str | None = None, judge_client: ModelClient | None = None,
                  twin: str | None = None, judge_note: str | None = None) -> ObserveResult:
    try:
        store = ProfileStore(cfg.community_dir, create=False)  # never init, never commit
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    conn = connect_readonly(cfg.db_path)
    out_dir = cfg.observer.dir
    ostore = ObserverStore(out_dir / "observer.db")
    unfinished = [r.id for r in m.closed_runs(conn) if m.postrun_unfinished(conn, cfg.runs_dir, r.id)]
    runs = [r for r in m.closed_runs(conn) if r.id not in unfinished]
    if run_id in unfinished:
        raise ValueError(f"run {run_id!r} is still in its post-run; observe it when the post-run has finished")
    targets = runs if run_id is None else [r for r in runs if r.id == run_id]
    if run_id is not None and not targets:
        raise ValueError(f"no closed run {run_id!r}")
    in_progress = m.open_runs(conn) + [f"{r} (post-run)" for r in unfinished]
    who = make_who({i: store.public(i) for i in store.ids()})
    answers = m.benchmark_answers(conn, runs, who)
    judge = None
    if judge_client is not None:
        limited = limit_clients({"judge": judge_client}, JUDGE_CONCURRENCY)["judge"]
        backend = cfg.models.get(cfg.observer.judge or "")
        judge = Judge(limited, ostore, backend.model if backend else "judge")  # a new model means new judgments
    note = judge_note if judge is None else None
    written: list[Path] = []
    for run in targets:
        if judge is not None:
            await _judge_run(judge, ostore, conn, store, runs, run, who, answers)
        judgments = ostore.judgments(run.id)
        labels = {t["label"] for j in judgments if j.analysis == "themes" and j.status == "ok" for t in j.result["themes"]}
        section = RunSection(
            run=run, who=who, changes=provenance_changes(conn, run.id), mix=m.action_mix(conn, run),
            influence=m.influence(conn, run), credits=m.credits(conn, run.id), peer=m.peer_scores(conn, run.id),
            gap=m.self_peer_gap(conn, run.id), judgments=judgments,
            codes={label: ostore.code_for(label) or label for label in labels},
            benchmark=next((a for a in answers if a.run_id == run.id), None), judge_note=note,
            dimensions=m.dimension_scores(conn, run.id), process2=_process2_section(conn, run, who, out_dir, written),
            rulings=sc.rulings(conn, run.id), awards=sc.awards(conn, run.id),
        )
        path = out_dir / "runs" / f"{run.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_run(section))
        written.append(path)
    if run_id is None:
        written += _write_summary(conn, store, ostore, runs, who, answers, out_dir, twin, note, in_progress)
    all_judgments = [j for r in targets for j in ostore.judgments(r.id)]
    return ObserveResult(written, sum(j.status == "ok" for j in all_judgments),
                         sum(j.status == "failed" for j in all_judgments), in_progress)


def _series(runs: list[m.RunInfo], value) -> dict[str, list[float | None]]:
    agents = sorted({a for r in runs for a in r.agent_ids})
    return {a: [value(r, a) for r in runs] for a in agents}


def _charts(conn: sqlite3.Connection, ostore: ObserverStore, runs: list[m.RunInfo], out_dir: Path) -> dict[str, Path]:
    labels = [r.tag for r in runs]
    markers = m.harness_changes(conn, runs)
    peer = {r.id: m.peer_scores(conn, r.id) for r in runs}
    gap = {r.id: m.self_peer_gap(conn, r.id) for r in runs}
    mixes = {r.id: m.action_mix(conn, r) for r in runs}

    def share(run: m.RunInfo, action: str) -> float | None:
        total = sum(sum(c.values()) for c in mixes[run.id].values())
        return None if total == 0 else sum(c[action] for c in mixes[run.id].values()) / total

    specs: dict[str, tuple[str, dict[str, list[float | None]], str]] = {
        "Peer score": ("peer.png", _series(runs, lambda r, a: peer[r.id].get(a)), "mean peer rating"),
        "Self minus peer": ("self_peer.png", _series(runs, lambda r, a: gap[r.id].get(a)), "self − peer"),
        "Action mix": ("actions.png", {act: [share(r, act) for r in runs] for act in m.ACTIONS}, "share of turns"),
    }
    earned = {r.id: Counter(a.agent_id for a in sc.awards(conn, r.id)) for r in runs}
    lifetime: dict[str, list[float | None]] = {}
    for agent in sorted({a for r in runs for a in r.agent_ids}):
        total, values = 0, []
        for r in runs:
            total += earned[r.id][agent]
            values.append(float(total) if agent in r.agent_ids else None)
        lifetime[agent] = values
    verdicts = {r.id: [x.accepted for x in sc.rulings(conn, r.id)] for r in runs}
    credit = {r.id: sc.self_credit(conn, r) for r in runs}

    def self_share(run: m.RunInfo) -> float | None:
        calls = sum(c.calls for c in credit[run.id].values())
        return None if calls == 0 else sum(c.self_credited for c in credit[run.id].values()) / calls

    specs["Scorer points"] = ("scorer_points.png", lifetime, "lifetime Scorer points")
    specs["Scorer rulings"] = ("scorer_rulings.png", {
        "accepted": [float(sum(verdicts[r.id])) for r in runs],
        "rejected": [float(len(verdicts[r.id]) - sum(verdicts[r.id])) for r in runs]}, "rulings")
    specs["Self-credit"] = ("self_credit.png", {"calls crediting the caller": [self_share(r) for r in runs]},
                            "share of calls")
    by_run = {r.id: theme_counts(ostore, ostore.judgments(r.id, "themes")) for r in runs}
    totals: dict[str, int] = {}
    for per_agent in by_run.values():
        for counter in per_agent.values():
            for code, n in counter.items():
                totals[code] = totals.get(code, 0) + n
    top = sorted(totals, key=lambda c: (-totals[c], c))[:6]
    if top:
        specs["Themes"] = ("themes.png", {code: [float(sum(c.get(code, 0) for c in by_run[r.id].values()))
                                                 for r in runs] for code in top}, "times expressed")
    return {title: trend_chart(out_dir / "charts" / filename, title, labels, series, markers, ylabel)
            for title, (filename, series, ylabel) in specs.items()}


def _write_summary(conn: sqlite3.Connection, store: ProfileStore, ostore: ObserverStore, runs: list[m.RunInfo],
                   who: Who, answers: list[m.BenchmarkAnswer], out_dir: Path, twin: str | None,
                   judge_note: str | None, in_progress: list[str]) -> list[Path]:
    charts = _charts(conn, ostore, runs, out_dir) if len(runs) >= 2 else {}
    found = m.twins(store)
    if twin is not None:
        found = {control: [twin] for control in found}
    twin_rows: dict[str, list] = {}
    for control, twins in found.items():
        group = [control, *twins]
        rows = []
        for run in runs:
            peer, mix = m.peer_scores(conn, run.id), m.action_mix(conn, run)
            rows += [(run.tag, agent, peer.get(agent), mix[agent]) for agent in group if agent in run.agent_ids]
        twin_rows[control] = rows
        compared = [r for r in runs if any(a in r.agent_ids for a in group)]
        if len(compared) >= 2:
            peers = {r.id: m.peer_scores(conn, r.id) for r in compared}
            series = {who(a): [peers[r.id].get(a) if a in r.agent_ids else None for r in compared] for a in group}
            charts[f"Control vs twin: {who(control)}"] = trend_chart(
                out_dir / "charts" / f"control_{control}.png", f"Peer score: {who(control)} (control) vs twin",
                [r.tag for r in compared], series, m.harness_changes(conn, compared), "mean peer rating")
    p2_runs = [r for r in runs if r.process == 2]
    process2 = Process2Summary(
        p2.recurring_pairs(conn, p2_runs),
        p2.combine_participation([p2.poll_participation(conn, r) for r in p2_runs]),
        p2.combine_order([p2.speaking_order(conn, r) for r in p2_runs]),
    ) if p2_runs else None
    rulings = [x for r in runs for x in sc.rulings(conn, r.id)]
    scorer = ScorerSummary(
        accepted=sum(x.accepted for x in rulings), rejected=sum(not x.accepted for x in rulings),
        points=dict(Counter(a.agent_id for r in runs for a in sc.awards(conn, r.id))),
        self_credit=sc.combine_self_credit([sc.self_credit(conn, r) for r in runs]),
    )
    summary = SummarySection(
        runs=runs, who=who, charts={title: f"charts/{path.name}" for title, path in charts.items()},
        identity=m.identity_events(conn, store, runs), norms=m.norm_events(conn), codebook=ostore.codes(),
        twins=found, categories=m.by_category(conn, runs), failed=ostore.failed(), benchmark=answers,
        reciprocity=m.credit_pairs(conn, runs), in_progress=in_progress, judge_note=judge_note, twin_rows=twin_rows,
        process2=process2, scorer=scorer,
    )
    path = out_dir / "summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_summary(summary))
    return [*charts.values(), path]
