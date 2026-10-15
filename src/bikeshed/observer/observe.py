"""One `bikeshed observe`: metrics, judgments, charts and reports for closed runs."""

import asyncio
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bikeshed.config import Config
from bikeshed.db import connect_readonly
from bikeshed.models.base import ModelClient, limit_clients
from bikeshed.observer import briefs as pb
from bikeshed.observer import metrics as m
from bikeshed.observer import process2 as p2
from bikeshed.observer import scoring as sc
from bikeshed.observer.charts import network_chart, trend_chart
from bikeshed.observer.codebook import theme_counts, update_codebook
from bikeshed.observer.inputs import (
    benchmark_inputs, private_public_inputs, self_image_inputs, themes_inputs, think_vs_act_inputs,
)
from bikeshed.observer.judge import ANALYSES, Judge
from bikeshed.observer.report import (
    Process2Section, Process2Summary, RunSection, ScorerSummary, SummarySection, render_run, render_summary,
)
from bikeshed.observer.store import ObserverStore
from bikeshed.phases import meeting_rounds
from bikeshed.profiles import ProfileError, ProfileStore
from bikeshed.provenance import provenance_changes
from bikeshed.sequence import postrun_unfinished
from bikeshed.view import Who, make_who

Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


def _elapsed(start: float) -> str:
    secs = round(time.monotonic() - start)
    return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"


@dataclass(frozen=True)
class ObserveResult:
    written: list[Path]
    judged: int
    failed: int
    in_progress: list[str]


async def _judge_run(judge: Callable[[str], Judge], ostore: ObserverStore, conn: sqlite3.Connection, store: ProfileStore,
                     runs: list[m.RunInfo], run: m.RunInfo, who: Who, answers: list[m.BenchmarkAnswer],
                     progress: Progress, max_calls: int) -> None:
    specs: list[tuple[str, str | None, dict[str, str]]] = []
    for agent in run.agent_ids:
        specs.append(("themes", agent, themes_inputs(conn, store, run, agent)))
        if (pp := private_public_inputs(conn, run, agent, who)) is not None:
            specs.append(("private_public", agent, pp))
        if meeting_rounds(run.process) and (tva := think_vs_act_inputs(conn, run, agent, who)) is not None:
            specs.append(("think_vs_act", agent, tva))
        specs.append(("self_image", agent, self_image_inputs(store, runs, run, agent)))
        if (di := pb.disclosure_inputs(conn, run, agent, who)) is not None:
            specs.append(("disclosure", agent, di))
    if (fi := pb.fabrication_inputs(conn, run, who)) is not None:
        specs.append(("fabrication", None, fi))
    if run.is_benchmark:
        specs.append(("benchmark", None, benchmark_inputs(answers, run.id)))
    hits = {i: judge(a).cached(ANALYSES[a], run_id=run.id, agent_id=agent, inputs=inputs)
            for i, (a, agent, inputs) in enumerate(specs)}
    fresh = [s for i, s in enumerate(specs) if hits[i] is None]
    settled = sum(1 for j in hits.values() if j is not None and j.status == "failed")
    progress(f"  {len(specs)} judgments: {len(specs) - len(fresh)} cached"
             + (f" ({settled} failed, not retried)" if settled else "") + f", {len(fresh)} to judge"
             + (f" ({max_calls} at a time)" if fresh else ""))
    start, finished = time.monotonic(), 0

    async def one(analysis: str, agent: str | None, inputs: dict[str, str]) -> None:
        nonlocal finished
        j = await judge(analysis).run(ANALYSES[analysis], run_id=run.id, agent_id=agent, inputs=inputs)
        finished += 1
        outcome = "ok" if j.status == "ok" else f"failed: {j.error}"
        progress(f"  [{finished}/{len(fresh)}] {analysis} · {who(agent) if agent else 'everyone'}: {outcome}"
                 f" ({_elapsed(start)})")

    await asyncio.gather(*(one(*s) for s in fresh))
    labels = [t["label"] for j in ostore.judgments(run.id, "themes") if j.status == "ok" for t in j.result["themes"]]
    new = len({label for label in labels if ostore.code_for(label) is None})
    if new:
        progress(f"  codebook: merging {new} new theme label{'s' if new != 1 else ''}")
    codebook = await update_codebook(judge("codebook"), ostore, run.id, labels)
    if codebook is not None and codebook.status != "ok":
        progress(f"  codebook: failed: {codebook.error}")


def _process2_section(conn: sqlite3.Connection, run: m.RunInfo, who: Who, out_dir: Path,
                      written: list[Path]) -> Process2Section | None:
    if not meeting_rounds(run.process):
        return None
    network = p2.pm_network(conn, run)
    chart = None
    if network:
        path = network_chart(out_dir / "charts" / f"network_{run.id}.png", f"Private messages: {run.tag}",
                             {a: who(a) for a in run.agent_ids}, network)
        written.append(path)
        chart = f"../charts/{path.name}"
    return Process2Section(network, chart, p2.poll_participation(conn, run), p2.speaking_order(conn, run))


def _judges(cfg: Config, ostore: ObserverStore, default: ModelClient,
            analysis_clients: dict[str, ModelClient], retry_failed: bool = True) -> Callable[[str], Judge]:
    """The judge for each analysis: the default one unless `[observer.judges]` names another backend."""
    if unknown := sorted(set(cfg.observer.judges) - set(ANALYSES)):
        raise ValueError(f"[observer.judges] names unknown analyses: {', '.join(unknown)} (known: {', '.join(ANALYSES)})")
    if missing := sorted(set(cfg.observer.judges) - set(analysis_clients)):
        raise ValueError(f"no client for the judge of: {', '.join(missing)}")

    # one limiter for every judge, like the agents in a run: at most `[run] max_concurrent_calls` calls at once
    limited = limit_clients({"": default} | {name: analysis_clients[name] for name in cfg.observer.judges},
                            cfg.run.max_concurrent_calls)

    def make(client: ModelClient, backend_name: str | None) -> Judge:
        backend = cfg.models.get(backend_name or "")
        return Judge(client, ostore, backend.model if backend else "judge",  # a new model means new judgments
                     retry_failed=retry_failed)

    fallback = make(limited[""], cfg.observer.judge)
    own = {name: make(limited[name], backend) for name, backend in cfg.observer.judges.items()}
    return lambda analysis: own.get(analysis, fallback)


async def observe(cfg: Config, *, run_id: str | None = None, judge_client: ModelClient | None = None,
                  analysis_clients: dict[str, ModelClient] | None = None,
                  twin: str | None = None, judge_note: str | None = None, retry_failed: bool = True,
                  progress: Progress = _quiet) -> ObserveResult:
    try:
        store = ProfileStore(cfg.community_dir, create=False)  # never init, never commit
    except ProfileError as exc:
        raise ValueError(str(exc)) from exc
    conn = connect_readonly(cfg.db_path)
    out_dir = cfg.observer.dir
    ostore = ObserverStore(out_dir / "observer.db")
    unfinished = [r.id for r in m.closed_runs(conn) if postrun_unfinished(conn, cfg.runs_dir, r.id)]
    runs = [r for r in m.closed_runs(conn) if r.id not in unfinished]
    if run_id in unfinished:
        raise ValueError(f"run {run_id!r} is still in its post-run; observe it when the post-run has finished")
    targets = runs if run_id is None else [r for r in runs if r.id == run_id]
    if run_id is not None and not targets:
        raise ValueError(f"no closed run {run_id!r}")
    in_progress = m.open_runs(conn) + [f"{r} (post-run)" for r in unfinished]
    who = make_who({i: store.public(i) for i in store.ids()})
    known = set(store.ids())
    answers = m.benchmark_answers(conn, runs, who)
    judge = None if judge_client is None else _judges(cfg, ostore, judge_client, analysis_clients or {}, retry_failed)
    note = judge_note if judge is None else None
    written: list[Path] = []
    progress(f"observing {len(targets)} closed run{'s' if len(targets) != 1 else ''}"
             + (f"; skipping {', '.join(in_progress)}" if in_progress else ""))
    progress(f"judge: {', '.join(sorted({cfg.observer.judge or '', *cfg.observer.judges.values()} - {''}))}"
             if judge is not None else "not judging" + (f" ({note})" if note else "") + ": metrics only")
    for i, run in enumerate(targets, 1):
        progress(f"[{i}/{len(targets)}] {run.tag}" + (f" ({run.id})" if run.tag != run.id else ""))
        if judge is not None:
            await _judge_run(judge, ostore, conn, store, runs, run, who, answers, progress, cfg.run.max_concurrent_calls)
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
            briefs=pb.section(conn, run, who, judgments),
            controls=frozenset(a for a in run.agent_ids if a in known and store.record(a).control),
        )
        path = out_dir / "runs" / f"{run.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_run(section))
        written.append(path)
        progress(f"  wrote {path}")
    if run_id is None:
        progress(f"summary across {len(runs)} runs" + (": drawing charts" if len(runs) >= 2 else ""))
        summary = _write_summary(conn, store, ostore, runs, who, answers, out_dir, twin, note, in_progress)
        written += summary
        progress(f"  wrote {summary[-1]} and {len(summary) - 1} chart{'s' if len(summary) != 2 else ''}")
    all_judgments = [j for r in targets for j in ostore.judgments(r.id)]
    return ObserveResult(written, sum(j.status == "ok" for j in all_judgments),
                         sum(j.status == "failed" for j in all_judgments), in_progress)


def _series(runs: list[m.RunInfo], value) -> dict[str, list[float | None]]:
    agents = sorted({a for r in runs for a in r.agent_ids})
    return {a: [value(r, a) for r in runs] for a in agents}


def _charts(conn: sqlite3.Connection, ostore: ObserverStore, runs: list[m.RunInfo], who: Who,
            out_dir: Path) -> dict[str, Path]:
    labels = [r.tag for r in runs]
    markers = m.harness_changes(conn, runs)
    peer = {r.id: m.peer_scores(conn, r.id) for r in runs}
    gap = {r.id: m.self_peer_gap(conn, r.id) for r in runs}
    mixes = {r.id: m.action_mix(conn, r) for r in runs}

    def share(run: m.RunInfo, action: str) -> float | None:
        total = sum(c[a] for c in mixes[run.id].values() for a in m.ACTIONS)
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
    stats = [pb.run_stat(conn, r, who, ostore.judgments(r.id)) for r in runs]
    if any(st is not None for st in stats):
        specs["Disclosure"] = ("disclosure.png", {"share of private facts disclosed": [
            None if st is None or not st.facts else st.disclosed / st.facts for st in stats]}, "share")
        specs["Fabrication"] = ("fabrication.png", {"unsupported specifics": [
            None if st is None or st.fabricated is None else float(st.fabricated) for st in stats]}, "count")
    return {title: trend_chart(out_dir / "charts" / filename, title, labels, series, markers, ylabel)
            for title, (filename, series, ylabel) in specs.items()}


def _write_summary(conn: sqlite3.Connection, store: ProfileStore, ostore: ObserverStore, runs: list[m.RunInfo],
                   who: Who, answers: list[m.BenchmarkAnswer], out_dir: Path, twin: str | None,
                   judge_note: str | None, in_progress: list[str]) -> list[Path]:
    charts = _charts(conn, ostore, runs, who, out_dir) if len(runs) >= 2 else {}
    found = store.twins()
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
    meetings: dict[int, Process2Summary] = {}
    for process in sorted({r.process for r in runs if meeting_rounds(r.process)}):
        group = [r for r in runs if r.process == process]  # never sum one generation's rounds into another's
        meetings[process] = Process2Summary(
            p2.recurring_pairs(conn, group),
            p2.combine_participation([p2.poll_participation(conn, r) for r in group]),
            p2.combine_order([p2.speaking_order(conn, r) for r in group]),
        )
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
        meetings=meetings, scorer=scorer,
        briefed=[st for r in runs if (st := pb.run_stat(conn, r, who, ostore.judgments(r.id))) is not None],
    )
    path = out_dir / "summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_summary(summary))
    return [*charts.values(), path]
