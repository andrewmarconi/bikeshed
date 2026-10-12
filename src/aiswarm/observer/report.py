"""Markdown for the Observer's reports. User-only; never shown to any agent."""

from collections import Counter
from dataclasses import dataclass, field

from aiswarm.observer.metrics import (
    ACTIONS, BenchmarkAnswer, CategoryStats, Credits, IdentityEvent, Influence, NormEvent, RunInfo,
)
from aiswarm.observer.process2 import OrderStat, Participation, RecurringPair
from aiswarm.observer.store import Judgment
from aiswarm.view import Who


@dataclass
class Process2Section:
    network: Counter  # (sender, recipient) -> private messages
    chart: str | None  # network PNG relative to the run report; None when no messages were sent
    participation: dict[str, Participation]
    order: list[OrderStat]


@dataclass
class RunSection:
    run: RunInfo
    who: Who
    changes: list[str]
    mix: dict[str, Counter]
    influence: dict[str, Influence]
    credits: Credits | None
    peer: dict[str, float]
    gap: dict[str, float]
    judgments: list[Judgment]
    codes: dict[str, str]  # theme label -> code
    benchmark: BenchmarkAnswer | None
    judge_note: str | None  # why nothing was judged (no judge configured / --no-judge), else None
    dimensions: dict[str, dict[str, float]] = field(default_factory=dict)  # agent -> adopted dimension -> peer mean
    process2: Process2Section | None = None  # only for Process 2 runs


def _dimensions(s: RunSection) -> list[str]:
    names = sorted({name for scores in s.dimensions.values() for name in scores})
    if not names:
        return []
    out = ["## Dimensions", "", "| Agent | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    for agent in s.run.agent_ids:
        scores = s.dimensions.get(agent, {})
        out.append(f"| {s.who(agent)} | " + " | ".join(_fmt(scores.get(n)) for n in names) + " |")
    return out + [""]


def _find(judgments: list[Judgment], analysis: str, agent_id: str | None) -> Judgment | None:
    return next((j for j in judgments if j.analysis == analysis and j.agent_id == agent_id), None)


def _not_judged(j: Judgment | None, note: str | None) -> str | None:
    if j is not None and j.status == "ok":
        return None
    reason = note or (j.error if j is not None else "no judgment")
    return f"_not judged ({reason})_"


def _fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}"


def _metrics(s: RunSection) -> list[str]:
    out = ["## Metrics", "", "| Agent | " + " | ".join(ACTIONS) + " | influence | peer | self − peer |",
           "|---" * (len(ACTIONS) + 4) + "|"]
    for agent in s.run.agent_ids:
        mix = s.mix.get(agent, Counter())
        counts = " | ".join(str(mix.get(a, 0)) for a in ACTIONS)
        score = s.influence.get(agent, Influence()).score
        out.append(f"| {s.who(agent)} | {counts} | {score} | {_fmt(s.peer.get(agent))} | {_fmt(s.gap.get(agent))} |")
    return out


def _credits(s: RunSection) -> list[str]:
    out = ["## Credits", ""]
    if s.credits is None:
        return out + ["No closing call yet: the run did not end on consensus."]
    out.append(f"Called by {s.who(s.credits.caller)}.")
    for agent in s.run.agent_ids:
        contribution = s.credits.credited.get(agent)
        score = s.influence.get(agent, Influence()).score
        given = f"credited: {contribution}" if contribution is not None else "not credited"
        self_credit = " (self-credit)" if agent == s.credits.caller and contribution is not None else ""
        out.append(f"- {s.who(agent)}: influence {score}; {given}{self_credit}")
    return out


def _participation(rows: dict[str, Participation], who: Who) -> list[str]:
    out = ["### Poll participation", "", "| Agent | polled | support | object | abstain | failed | unprompted votes |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {who(a)} | {p.polled} | {p.supported} | {p.objected} | {p.abstained} | {p.failed} | {p.unprompted} |"
            for a, p in rows.items()]
    return out + [""]


def _order(stats: list[OrderStat]) -> list[str]:
    out = ["### Speaking order", "", "Rounds after the first; position 1 spoke first. Taken up: adopted, refined or "
           "cited in a call by another member.", ""]
    if not stats:
        return out + ["_No rounds after the first._", ""]
    out += ["| Position | turns | taken up | share |", "|---|---|---|---|"]
    out += [f"| {s.position} | {s.turns} | {s.taken_up} | {round(100 * s.taken_up / s.turns)}% |" for s in stats]
    return out + [""]


def _process2(s: RunSection) -> list[str]:
    p = s.process2
    out = ["## Process 2", "", "### Private messages", ""]
    if not p.network:
        out += ["_No private messages._", ""]
    else:
        agents = s.run.agent_ids
        out += ["| From \\ To | " + " | ".join(s.who(a) for a in agents) + " |", "|---" * (len(agents) + 1) + "|"]
        out += [f"| {s.who(a)} | " + " | ".join("–" if a == b else str(p.network.get((a, b), 0)) for b in agents) + " |"
                for a in agents]
        out += [""] + ([f"![Private messages]({p.chart})", ""] if p.chart else [])
    return out + _participation({a: p.participation[a] for a in s.run.agent_ids if a in p.participation}, s.who) \
        + _order(p.order)


def _per_agent(s: RunSection, title: str, analysis: str, body, none_without_judgment: str | None = None) -> list[str]:
    out = [f"## {title}", ""]
    for agent in s.run.agent_ids:
        j = _find(s.judgments, analysis, agent)
        out.append(f"### {s.who(agent)}")
        if j is None and s.judge_note is None and none_without_judgment is not None:
            out += [none_without_judgment, ""]
            continue
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out += [missing, ""]
            continue
        out += body(j.result) + [""]
    return out


def render_run(s: RunSection) -> str:
    r = s.run
    out = [f"# {r.id} — {r.goal}", "",
           f"- **Goal:** {r.label} ({r.category}){' — benchmark' if r.is_benchmark else ''}",
           f"- **Ended:** {r.ended_reason or 'unknown'}",
           f"- **Process:** {r.process}"]
    if s.changes:
        out += ["", "> **Harness changed:** " + "; ".join(s.changes)]
    out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + [""]
    if s.process2 is not None:
        out += _process2(s)
    out += _per_agent(s, "Themes", "themes", lambda res: [
        f"- **{s.codes.get(t['label'], t['label'])}** ({t['polarity']}; “{t['label']}”): "
        f"“{t['evidence']}” — _{t['source']}_" for t in res["themes"]])
    out += _per_agent(s, "Private journal vs public reviews", "private_public", lambda res: [
        f"- {f['colleague']}: **{f['verdict']}** — journal: “{f['journal_quote']}” / review: “{f['review_quote']}”"
        for f in res["findings"]] or ["- no colleagues compared"], none_without_judgment="_gave no peer reviews_")
    if r.process == 2:
        out += _per_agent(s, "Think notes vs actions (private vs public)", "think_vs_act", lambda res: [
            f"- **{p['relation']}** — {p['about']}: think “{p['think_quote']}” / action “{p['action_quote']}”"
            for p in res["points"]] or ["- nothing to compare"], none_without_judgment="_wrote no think notes_")
    out += _per_agent(s, "Self-image", "self_image", lambda res: (
        [f"- {c['what']}: “{c['evidence']}”" for c in res["changes"]] or ["- no change"])
        + [f"- private/public gap: **{res['private_public_gap']}** {res.get('gap_evidence', '')}".rstrip()])
    if s.benchmark is not None:
        out += ["## Benchmark answer", "", s.benchmark.text, ""]
        j = _find(s.judgments, "benchmark", None)
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out.append(missing)
        else:
            out += [f"- **Trend:** {j.result['trend']}", f"- **Optimises for:** {j.result['optimises_for']}"]
            out += [f"  - “{q}”" for q in j.result["evidence"]]
    models = sorted({j.model_reported for j in s.judgments if j.model_reported})
    prompts = sorted({f"{j.analysis} {j.prompt_sha256[:8]}" for j in s.judgments})
    if s.judgments:
        out += ["", f"_Judged by {', '.join(models) or 'unknown model'}; prompts {', '.join(prompts)}._"]
    return "\n".join(out).rstrip() + "\n"


@dataclass
class Process2Summary:
    pairs: list[RecurringPair]  # who wrote privately to whom in two or more runs
    participation: dict[str, Participation]  # summed over Process 2 runs
    order: list[OrderStat]  # summed by position over Process 2 runs


@dataclass
class SummarySection:
    runs: list[RunInfo]
    who: Who
    charts: dict[str, str]  # title -> path relative to summary.md
    identity: list[IdentityEvent]
    norms: list[NormEvent]
    codebook: list[tuple[str, str]]
    twins: dict[str, list[str]]
    categories: dict[str, CategoryStats]
    failed: list[Judgment]
    benchmark: list[BenchmarkAnswer]
    reciprocity: list[tuple[str, str, str]]
    in_progress: list[str]
    judge_note: str | None
    # control id -> rows of (run label, agent id, mean peer score, action counts) for the control and its twins
    twin_rows: dict[str, list[tuple[str, str, float | None, Counter]]] = field(default_factory=dict)
    process2: Process2Summary | None = None  # None when no Process 2 run has closed


TWIN_ACTIONS = ("propose", "critique", "adopt", "vote", "call_consensus", "failed")


def _control_vs_twin(s: SummarySection) -> list[str]:
    out = ["## Control vs twin", ""]
    if not s.twins:
        return out + ["_No control agent._", ""]
    for control, found in s.twins.items():
        twins = ", ".join(s.who(t) for t in found) if found else "no twin found"
        out += [f"### {s.who(control)} vs {twins}", ""]
        rows = s.twin_rows.get(control, [])
        if not rows:
            out += ["_No closed runs with these agents yet._", ""]
            continue
        out += ["| Run | Agent | peer | " + " | ".join(TWIN_ACTIONS) + " |", "|---" * (len(TWIN_ACTIONS) + 3) + "|"]
        out += [f"| {label} | {s.who(agent)} | {_fmt(peer)} | " + " | ".join(str(mix.get(a, 0)) for a in TWIN_ACTIONS)
                + " |" for label, agent, peer, mix in rows]
        out.append("")
    return out


def _identity_line(e: IdentityEvent, who: Who) -> str:
    line = f"- {e.at.isoformat(timespec='minutes')} {who(e.agent_id)}: {e.kind} {e.detail}".rstrip()
    return line + (f" (prior peer score {e.prior_peer:.1f})" if e.prior_peer is not None else "")


def render_summary(s: SummarySection) -> str:
    out = ["# Observer summary", "", f"{len(s.runs)} closed runs: " + ", ".join(r.tag for r in s.runs)]
    if s.in_progress:
        out.append(f"In progress (skipped): {', '.join(s.in_progress)}")
    if s.judge_note:
        out.append(f"Not judged: {s.judge_note}.")
    out += ["", "## Trends", ""]
    if not s.charts:
        out += ["_Trend charts need at least 2 closed runs._", ""]
    for title, path in s.charts.items():
        out += [f"### {title}", "", f"![{title}]({path})", ""]
    out += _control_vs_twin(s)
    out += ["## By goal category", "", "| Category | Runs | Endings | Top actions |", "|---|---|---|---|"]
    for name, c in sorted(s.categories.items()):
        endings = ", ".join(f"{k} {v}" for k, v in c.endings.most_common())
        top = ", ".join(f"{k} {v}" for k, v in c.actions.most_common(4))
        out.append(f"| {name} | {c.runs} | {endings} | {top} |")
    out += ["", "## Benchmark answers", ""]
    for a in s.benchmark:
        out += [f"### {a.run_id} ({'consensus' if a.consensus else 'no consensus'})", "", a.text, ""]
    if not s.benchmark:
        out += ["_No benchmark runs yet._", ""]
    out += ["## Identity events", ""]
    out += [_identity_line(e, s.who) for e in s.identity] or ["_None yet._"]
    out += ["", "## Norms and dimensions", ""]
    out += [f"- {n.run_id} {s.who(n.author)} proposed a {n.kind} ({n.status}): {n.text}" for n in s.norms] or ["_None yet._"]
    out += ["", "## Credit reciprocity", ""]
    out += [f"- {run}: {s.who(g)} → {s.who(r)}" for run, g, r in s.reciprocity] or ["_No credits yet._"]
    if s.process2 is not None:
        p = s.process2
        out += ["", "## Process 2", "", "### Recurring private-message pairs", ""]
        out += [f"- {s.who(r.sender)} → {s.who(r.recipient)}: {r.runs} runs, {r.messages} messages" for r in p.pairs] \
            or ["_None yet._"]
        out += [""] + _participation(p.participation, s.who) + _order(p.order)
    out += ["", "## Codebook", ""]
    out += [f"- **{name}**: {description}" for name, description in s.codebook] or ["_Empty._"]
    out += ["", "## Not judged", ""]
    out += [f"- {j.run_id} {j.analysis}{' ' + j.agent_id if j.agent_id else ''}: {j.error}" for j in s.failed] \
        or ["_Nothing failed._"]
    return "\n".join(out).rstrip() + "\n"
