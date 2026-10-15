"""Markdown for the Observer's reports. User-only; never shown to any agent."""

from collections import Counter
from dataclasses import dataclass, field

from bikeshed.observer.briefs import BriefsSection, RunStat
from bikeshed.observer.copying import NORM_FLOOR, CallCredit, Match, NormOverlap, Reuse
from bikeshed.observer.metrics import (
    ACTIONS, PRE_ACTIONS, BenchmarkAnswer, CategoryStats, Credits, IdentityEvent, Influence, NormEvent, RunInfo,
)
from bikeshed.observer.overlap import SHINGLE, THRESHOLDS
from bikeshed.observer.process2 import OrderStat, Participation, RecurringPair
from bikeshed.observer.scoring import Award, RulingRow, SelfCredit
from bikeshed.observer.store import Judgment
from bikeshed.phases import meeting_rounds
from bikeshed.view import Who


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
    process2: Process2Section | None = None  # only for runs with Process 2's rounds (processes 2 and 3)
    rulings: list[RulingRow] = field(default_factory=list)  # the Scorer's rulings, in board order
    awards: list[Award] = field(default_factory=list)  # Scorer points awarded in this run
    briefs: BriefsSection | None = None  # briefed runs only
    controls: frozenset[str] = frozenset()  # control agents in this run: their self-image is compared with their seed
    copies: list[Match] = field(default_factory=list)
    credit_checks: list[CallCredit] = field(default_factory=list)
    reuse: list[Reuse] | None = None  # None: the run's events don't record a library retrieval


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _mean(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt1(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}"


def _briefs(s: RunSection) -> list[str]:
    b = s.briefs
    if b is None:
        return []
    out = ["## Briefs", "", "### Scorer call-outs", ""]
    out += [f"- call #{c.call_id} (r{c.round}): “{c.constraint}” — "
            + (f"{s.who(c.holder)}'s private brief" if c.holder else "the shared brief") for c in b.callouts] \
        or ["_None._"]
    if b.callouts:
        out += ["", f"Closed by consensus after the call-outs: {'yes' if b.closed_by_consensus else 'no'}"]
    out += ["", "### Disclosure", ""]
    if b.facts:
        out += ["| Member | Fact | Disclosed | Round | Timing | Relayed |", "|---|---|---|---|---|---|"]
        out += [f"| {s.who(f.agent_id)} | {_cell(f.text)} | {'yes' if f.disclosed else 'no'} | {f.round or '–'} | "
                f"{f.timing} | {'yes' if f.relayed else 'no'} |" for f in b.facts]
    else:
        out.append(f"_not judged ({s.judge_note or 'no disclosure judgment'})_")
    out += ["", "### Call-out reactions", ""]
    for r in b.reactions:
        out += [f"#### r{r.round}, after {s.who(r.named)} was named", ""]
        if r.ended:
            out += ["_The run ended before another round._", ""]
            continue
        out += ["```text", *r.entries, "```"] if r.entries else ["_No entry referred to them._"]
        said = {None: "not judged", True: "yes", False: "no"}[r.disclosed]
        out += ["", f"Named member disclosed in r{r.round}: {said}", ""]
    if not b.reactions:
        out += ["_No member was named._", ""]
    if b.has_shared:
        out += ["### Fabrication", ""]
        if (missing := _not_judged(b.fabrication, s.judge_note)) is not None:
            out.append(missing)
        else:
            out += [f"- {i['where']}: “{i['quote']}” — {i['what']}" for i in b.fabrication.result["items"]] \
                or ["_Nothing unsupported._"]
        out.append("")
    return out


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
    out = ["## Metrics", "", "| Agent | " + " | ".join(ACTIONS + PRE_ACTIONS) + " | influence | peer | self − peer |",
           "|---" * (len(ACTIONS + PRE_ACTIONS) + 4) + "|"]
    for agent in s.run.agent_ids:
        mix = s.mix.get(agent, Counter())
        counts = " | ".join(str(mix.get(a, 0)) for a in ACTIONS + PRE_ACTIONS)
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


def _credit_checks(s: RunSection) -> list[str]:
    if not s.credit_checks:
        return []
    names = lambda ids: ", ".join(s.who(a) for a in ids) or "no one"
    out = ["", "### Credit accuracy", ""]
    for c in s.credit_checks:
        line = f"- #{c.call_id} by {s.who(c.caller)}: copied from {names(c.copied)}; credited {names(c.credited)}"
        if c.uncredited:
            line += f"; uncredited: {names(c.uncredited)}"
        if c.unmatched:
            line += f"; credited without matching text: {names(c.unmatched)}"
        out.append(line)
    return out


def _copying(s: RunSection) -> list[str]:
    out = ["## Copying", ""]
    if not s.copies:
        return out + ["_No copied text found._", "", THRESHOLDS, ""]
    out += ["| Copier | Entry | Source | Match | Round |", "|---|---|---|---|---|"]
    out += [f"| {s.who(m.copier)} | #{m.entry_id} {m.entry_type}.{m.field} | #{m.source_id} {s.who(m.source_author)} | "
            f"{m.kind} | {'same round' if m.same_round else 'earlier round'} |" for m in s.copies]
    return out + ["", THRESHOLDS, ""]


def _library(s: RunSection) -> list[str]:
    out = ["## Library reuse", ""]
    if s.reuse is None:
        return out + ["_Not recorded._", ""]
    if not s.reuse:
        return out + ["_No methods were shown._", ""]
    for r in s.reuse:
        shown = ", ".join(s.who(a) for a in r.shown_to)
        used = "; ".join(f"{s.who(a)} in #{e} ({k})" for a, e, k in r.reused_by)
        out.append(f"- method #{r.method_id} (run {r.method_run}, by {s.who(r.method_author)}), shown to {shown}: "
                   + (f"reused by {used}" if used else "not reused"))
    return out + ["", THRESHOLDS, ""]


def _scorer_run(s: RunSection) -> list[str]:
    out = ["## Scorer", ""]
    if not s.rulings:
        return out + ["_The Scorer did not rule in this run._", ""]
    for r in s.rulings:
        verdict = "**accepted**" if r.accepted else f"**rejected** — {' '.join(r.feedback.split())}"  # one list item
        out.append(f"- call #{r.call_id} by {s.who(r.caller)}: {verdict}")
    if s.awards:
        callers = {r.call_id: r.caller for r in s.rulings}
        out += ["", "Points awarded:"]
        out += [f"- {s.who(a.agent_id)}: 1 point for call #{a.call_id} — {a.contribution}"
                + (" (self-credit)" if callers.get(a.call_id) == a.agent_id else "") for a in s.awards]
    return out + [""]


def _participation(rows: dict[str, Participation], who: Who) -> list[str]:
    out = ["### Poll participation", "", "Counts are times polled: a member who abstains or fails to answer is asked "
           "again each round while the call stays open. Unprompted votes were cast in the member's own turn.", "",
           "| Agent | polled | support | object | abstain | failed | unprompted votes |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {who(a)} | {p.polled} | {p.supported} | {p.objected} | {p.abstained} | {p.failed} | {p.unprompted} |"
            for a, p in rows.items()]
    return out + [""]


def _order(stats: list[OrderStat], note: str = "") -> list[str]:
    out = ["### Speaking order", "", "Rounds after the first; position 1 spoke first. Failed turns keep their place "
           "but are not counted. Taken up: adopted, refined or cited in a call by another member." + note, ""]
    if not stats:
        return out + ["_No rounds after the first._", ""]
    out += ["| Position | turns | taken up | share |", "|---|---|---|---|"]
    out += [f"| {s.position} | {s.turns} | {s.taken_up} | {round(100 * s.taken_up / s.turns)}% |" for s in stats]
    return out + [""]


def _process2(s: RunSection) -> list[str]:
    p = s.process2
    out = [f"## Process {s.run.process}", "", "### Private messages", ""]
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


def _per_agent(s: RunSection, title: str, analysis: str, body, none_without_judgment: str | None = None,
               notes: dict[str, str] | None = None) -> list[str]:
    out = [f"## {title}", ""]
    for agent in s.run.agent_ids:
        j = _find(s.judgments, analysis, agent)
        out.append(f"### {s.who(agent)}")
        if notes and agent in notes:
            out += [notes[agent], ""]
        if j is None and s.judge_note is None and none_without_judgment is not None:
            out += [none_without_judgment, ""]
            continue
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out += [missing, ""]
            continue
        out += body(j.result) + [""]
    return out


def _tva_line(p: dict) -> str:
    """One think-vs-act point; points judged before rounds were recorded have no round."""
    prefix = f"[r{p['round']}] " if "round" in p else ""
    return f"- {prefix}**{p['relation']}** — {p['about']}: think “{p['think_quote']}” / action “{p['action_quote']}”"


def render_run(s: RunSection) -> str:
    r = s.run
    out = [f"# {r.id} — {r.goal}", "",
           f"- **Goal:** {r.label} ({r.category}){' — benchmark' if r.is_benchmark else ''}",
           f"- **Ended:** {r.ended_reason or 'unknown'}",
           f"- **Process:** {r.process}"]
    if s.changes:
        out += ["", "> **Harness changed:** " + "; ".join(s.changes)]
    out += [""] + _metrics(s) + [""] + _dimensions(s) + _credits(s) + _credit_checks(s) + [""] + _scorer_run(s) \
        + _briefs(s) + _copying(s) + _library(s)
    if s.process2 is not None:
        out += _process2(s)
    out += _per_agent(s, "Themes", "themes", lambda res: [
        f"- **{s.codes.get(t['label'], t['label'])}** ({t['polarity']}; “{t['label']}”): "
        f"“{t['evidence']}” — _{t['source']}_" for t in res["themes"]])
    out += _per_agent(s, "Private journal vs public reviews", "private_public", lambda res: [
        f"- {f['colleague']}: **{f['verdict']}** — journal: “{f['journal_quote']}” / review: “{f['review_quote']}”"
        for f in res["findings"]] or ["- no colleagues compared"], none_without_judgment="_gave no peer reviews_")
    if meeting_rounds(r.process):
        out += _per_agent(s, "Think notes vs actions (private vs public)", "think_vs_act",
                          lambda res: [_tva_line(p) for p in res["points"]] or ["- nothing to compare"],
                          none_without_judgment="_wrote no think notes_")
    out += _per_agent(s, "Self-image", "self_image", lambda res: (
        [f"- {c['what']}: “{c['evidence']}”" for c in res["changes"]] or ["- no change"])
        + [f"- private/public gap: **{res['private_public_gap']}** {res.get('gap_evidence', '')}".rstrip()],
        notes={agent: "_control: compared with its seed, not its previous run_" for agent in s.controls})
    if s.benchmark is not None:
        out += ["## Benchmark answer", "", s.benchmark.text, ""]
        j = _find(s.judgments, "benchmark", None)
        if (missing := _not_judged(j, s.judge_note)) is not None:
            out.append(missing)
        else:
            out += [f"- **Trend:** {j.result['trend']}", f"- **Optimises for:** {j.result['optimises_for']}"]
            out += [f"  - “{q}”" for q in j.result["evidence"]]
    by_model: dict[str, set[str]] = {}
    for j in s.judgments:
        if j.model_reported:
            by_model.setdefault(j.model_reported, set()).add(j.analysis)
    # name the analyses only when judges differ, so a mixed report says which model judged what
    models = sorted(by_model) if len(by_model) < 2 else \
        [f"{model} ({', '.join(sorted(names))})" for model, names in sorted(by_model.items())]
    prompts = sorted({f"{j.analysis} {j.prompt_sha256[:8]}" for j in s.judgments})
    if s.judgments:
        out += ["", f"_Judged by {', '.join(models) or 'unknown model'}; prompts {', '.join(prompts)}._"]
    return "\n".join(out).rstrip() + "\n"


@dataclass
class Process2Summary:
    pairs: list[RecurringPair]  # who wrote privately to whom in two or more runs
    participation: dict[str, Participation]  # summed over one process's runs
    order: list[OrderStat]  # summed by position over one process's runs


@dataclass
class ScorerSummary:
    accepted: int
    rejected: int
    points: dict[str, int]  # agent -> Scorer points over these runs
    self_credit: dict[str, SelfCredit]  # agent -> calls made, calls crediting the caller


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
    # control id -> rows of (run label, agent id, mean peer score, action counts, brief) for the control and its twins
    twin_rows: dict[str, list[tuple[str, str, float | None, Counter, str]]] = field(default_factory=dict)
    meetings: dict[int, Process2Summary] = field(default_factory=dict)  # process -> summary; processes are never summed together
    scorer: ScorerSummary | None = None
    briefed: list[RunStat] = field(default_factory=list)  # one row per run that had briefs
    copying: dict[int, Counter] = field(default_factory=dict)  # process -> (copier, source, kind) counts
    credit_gaps: dict[int, tuple[int, int]] = field(default_factory=dict)  # process -> (calls missing a source, calls)
    reuse: dict[int, tuple[int, int]] = field(default_factory=dict)  # process -> (shown methods reused, shown)
    methods_shown: list[tuple[int, int, int]] = field(default_factory=list)  # (method, runs shown, runs reused)
    norm_overlaps: list[NormOverlap] = field(default_factory=list)


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
        out += ["| Run | Agent | peer | " + " | ".join(TWIN_ACTIONS) + " | brief |",
                "|---" * (len(TWIN_ACTIONS) + 4) + "|"]
        out += [f"| {label} | {s.who(agent)} | {_fmt(peer)} | " + " | ".join(str(mix.get(a, 0)) for a in TWIN_ACTIONS)
                + f" | {brief} |" for label, agent, peer, mix, brief in rows]
        out.append("")
    return out


def _text_reuse(s: SummarySection) -> list[str]:
    out = []
    for process, (gaps, calls) in sorted(s.credit_gaps.items()):
        out.append(f"Process {process}: {gaps} of {calls} calls left out a copied source.")
    out += ["", "## Copying", ""]
    if not any(s.copying.values()):
        out += ["_No copied text found._", ""]
    for process, counts in sorted(s.copying.items()):
        if not counts:
            continue
        pairs = sorted({(c, src) for c, src, _ in counts})
        out += [f"### Process {process}", "", "| Copier | Source | exact | partial |", "|---|---|---|---|"]
        out += [f"| {s.who(c)} | {s.who(src)} | {counts[(c, src, 'exact')]} | {counts[(c, src, 'partial')]} |"
                for c, src in pairs] + [""]
    out += [THRESHOLDS, "", "## Library reuse", ""]
    out += [f"Process {p}: {used} of {shown} shown methods reused." for p, (used, shown) in sorted(s.reuse.items())] \
        or ["_Not recorded._"]
    if s.methods_shown:
        out += ["", "| Method | Runs shown | Runs reused |", "|---|---|---|"]
        out += [f"| #{m} | {shown} | {used} |" for m, shown, used in s.methods_shown]
    out += ["", "## Norm overlap", ""]
    if not s.norm_overlaps:
        return out + [f"_No proposal shares at least {NORM_FLOOR:.0%} of its {SHINGLE}-word sequences with a norm "
                      f"adopted before it._", ""]
    out += ["| Proposal | Run | Author | Overlaps | Shared | Outcome |", "|---|---|---|---|---|---|"]
    clip = lambda t: _cell(t if len(t) <= 120 else t[:120] + "…")
    out += [f"| #{o.proposal_id} | {o.run_id} | {s.who(o.author)} | #{o.norm_id} {clip(o.norm_text)} | "
            f"{o.share:.0%} | {o.status} |" for o in s.norm_overlaps]
    out += ["", f"_Shared: the share of a proposal's {SHINGLE}-word sequences found in the norm (100% when all of it "
            f"is there). Listed from {NORM_FLOOR:.0%}._"]
    return out + [""]


def _identity_line(e: IdentityEvent, who: Who) -> str:
    line = f"- {e.at.isoformat(timespec='minutes')} {who(e.agent_id)}: {e.kind} {e.detail}".rstrip()
    return line + (f" (prior peer score {e.prior_peer:.1f})" if e.prior_peer is not None else "")


def _scorer_summary(sc: ScorerSummary, who: Who) -> list[str]:
    out = ["## Scorer", ""]
    total = sc.accepted + sc.rejected
    out.append(f"{total} rulings: {sc.accepted} accepted, {sc.rejected} rejected." if total
               else "_The Scorer has not ruled yet._")
    agents = sorted(set(sc.points) | set(sc.self_credit))
    if agents:
        out += ["", "| Agent | Scorer points | calls | self-credited |", "|---|---|---|---|"]
        for a in agents:
            c = sc.self_credit.get(a, SelfCredit())
            out.append(f"| {who(a)} | {sc.points.get(a, 0)} | {c.calls} | {c.self_credited} |")
    return out + [""]


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
    out += [""] + _text_reuse(s)
    if s.scorer is not None:
        out += [""] + _scorer_summary(s.scorer, s.who)
    if s.briefed:
        head = ["| Call-outs | Disclosed | Unprompted | After call-out | Fabricated | Mean first round |",
                "|---|---|---|---|---|---|"]
        out += ["", "## Briefed runs", "", "| Run | Category |" + head[0][1:], "|---|---|" + head[1]]
        out += [f"| {r.tag} | {r.category} | {r.callouts} | {r.disclosed}/{r.facts} | {r.unprompted} | "
                f"{r.after_callout} | {'–' if r.fabricated is None else r.fabricated} | {_fmt1(r.mean_round)} |"
                for r in s.briefed]
        out += ["", "| Category | Runs | Call-outs | Disclosed | Unprompted | After call-out | Fabricated | "
                "Mean first round |", "|---|---|---|---|---|---|---|---|"]
        key = lambda r: r.category if r.process == 1 else f"{r.category} (P{r.process})"  # as by_category does
        for cat in sorted({key(r) for r in s.briefed}):
            rows = [r for r in s.briefed if key(r) == cat]
            judged = [r.fabricated for r in rows if r.fabricated is not None]
            out.append(f"| {cat} | {len(rows)} | {sum(r.callouts for r in rows)} | "
                       f"{sum(r.disclosed for r in rows)}/{sum(r.facts for r in rows)} | "
                       f"{sum(r.unprompted for r in rows)} | {sum(r.after_callout for r in rows)} | "
                       f"{sum(judged) if judged else '–'} | "
                       f"{_fmt1(_mean([n for r in rows for n in r.rounds]))} |")
    for process, p in sorted(s.meetings.items()):
        out += ["", f"## Process {process}", "", "### Recurring private-message pairs", ""]
        out += [f"- {s.who(r.sender)} → {s.who(r.recipient)}: {r.runs} runs, {r.messages} messages" for r in p.pairs] \
            or ["_None yet._"]
        out += [""] + _participation(p.participation, s.who) + _order(
            p.order, f" Summed over all Process {process} runs, whatever their size.")
    out += ["", "## Codebook", ""]
    out += [f"- **{name}**: {description}" for name, description in s.codebook] or ["_Empty._"]
    out += ["", "## Not judged", ""]
    out += [f"- {j.run_id} {j.analysis}{' ' + j.agent_id if j.agent_id else ''}: {j.error}" for j in s.failed] \
        or ["_Nothing failed._"]
    return "\n".join(out).rstrip() + "\n"
