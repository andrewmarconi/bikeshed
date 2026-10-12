"""What each watcher pane shows. Pure functions of RunState, so they test without a terminal.

Colour is applied to spans in code; entry text is never parsed for markup, so brackets stay literal.
"""

from collections.abc import Iterable, Mapping

from rich.text import Text

from aiswarm.board import SYSTEM
from aiswarm.watch.state import FeedLine, RunState

AGENT_COLOURS = ("#ff79c6", "#2ec4b6", "#a78bfa", "#e5c07b", "#61afef")  # pink, teal, violet, gold, sky blue
ACTION_STYLES = {"propose": "green", "refine": "blue", "adopt": "blue", "critique": "dark_orange",
                 "question": "cyan", "call_consensus": "bold magenta", "abstain": "dim", "write_file": "yellow",
                 "pass": "dim grey50"}
STANCE_STYLES = {"support": "green", "object": "red"}
STATUS_STYLES = {"speaking…": "bold", "thinking…": "dim", "polling…": "dim"}
SHORT_NAME = 12


def agent_colours(agent_ids: Iterable[str]) -> dict[str, str]:
    """One colour per agent in id order; after five they repeat."""
    return {a: AGENT_COLOURS[i % len(AGENT_COLOURS)] for i, a in enumerate(sorted(agent_ids))}


def _agent(text: str, agent_id: str, colours: Mapping[str, str]) -> Text:
    colour = colours.get(agent_id)
    return Text(text, style=f"bold {colour}" if colour else "bold")


def styled(line: FeedLine, colours: Mapping[str, str]) -> Text:
    """The line's text, coloured by part. `styled(line, c).plain == line.text`."""
    out = Text()
    if line.author == SYSTEM:
        return out.append(line.text, style="red italic" if line.kind == "scorer_rejected" else "dim italic")
    if line.kind == "think":
        out.append("💭 ")
    elif line.kind == "private_message":
        out.append("🔒 ")
    out.append(line.head, style="dim")
    out.append(" ")
    out.append_text(_agent(line.name, line.author, colours))
    if line.kind == "think":
        return out.append(f": {line.body}")
    if line.kind == "private_message":
        out.append(" → ")
        out.append_text(_agent(line.to_name, line.to, colours))
        return out.append(f": {line.body}")
    out.append(" ")
    out.append(line.kind, style=ACTION_STYLES.get(line.kind, ""))
    out.append(line.target)
    if line.failure:
        out.append(": ")
        out.append(line.detail, style="red")
    elif line.kind == "vote" and line.stance:
        out.append(": ")
        out.append(line.stance, style=STANCE_STYLES.get(line.stance, ""))
        if line.body:
            out.append(f": {line.body}")
    elif line.detail:
        out.append(f": {line.detail}")
    return out


def phase_label(s: RunState) -> str:
    return s.step_phase if s.phase == "rounds" and s.step_phase else s.phase


def short(name: str) -> str:
    return name if len(name) <= SHORT_NAME else name[: SHORT_NAME - 1] + "…"


RULING_STYLES = {"accepted": "green", "rejected": "red"}
AGENT_SEPARATOR = "   ·   "


def ruling(s: RunState) -> Text:
    out = Text("Scorer: ")
    if s.ruling is None:
        return out.append("no ruling yet")
    return out.append(f"{s.ruling.verdict} #{s.ruling.call_id}", style=RULING_STYLES.get(s.ruling.verdict, ""))


def status(s: RunState, colours: Mapping[str, str]) -> Text:
    """Two lines: the run (round, phase, newest call, Scorer) and the agents. The agents line wraps, never cuts."""
    out = Text(f"round {s.round}/{s.max_rounds} · {phase_label(s)} │ ")
    if s.calls:  # newest first; the poll treats the latest call as the open one
        c = s.calls[0]
        out.append(f"call #{c.entry_id}: {c.supporters} for, {c.objectors} against")
    else:
        out.append("no call yet")
    out.append(" │ ")
    out.append_text(ruling(s))
    out.append("\n")
    for i, card in enumerate(s.cards.values()):
        if i:
            out.append(AGENT_SEPARATOR)
        out.append_text(_agent(short(card.name), card.agent_id, colours))
        if card.control:
            out.append(" ctl", style="dim")
        out.append(f" ★ {'–' if card.reputation is None else f'{card.reputation:.1f}'} ◆ {card.points} ")
        out.append(card.state, style="red" if card.state.startswith("failed") else STATUS_STYLES.get(card.state, ""))
    if not s.cards:
        out.append("(no agents yet)")
    return out


def files(s: RunState) -> str:
    lines = [f"{f.path} v{f.version} ({f.author}, r{f.round})" for f in s.files]
    return "\n".join(["Workspace", *(lines or ["(no files yet)"])])


def show_postrun(s: RunState) -> bool:
    return s.closed_reason is not None or s.postrun_seen


def postrun(s: RunState) -> str:
    if not s.postrun_seen:
        return "Post-run\nno post-run"
    lines = ["Post-run"]
    for row in s.postrun:
        failed = f"failed {row.failed}" + (f" ({', '.join(row.failed_agents)})" if row.failed_agents else "")
        running = "  ← running" if row.step == s.current_step else ""
        lines.append(f"{row.step:<15} done {row.done:<3} {failed}{running}")
    return "\n".join(lines)


def popup(s: RunState) -> str:
    return f"{files(s)}\n\n{postrun(s)}\n\n(w or esc closes)"


def footer(s: RunState, following: bool) -> str:
    text = f"q quit · f follow: {'on' if following else 'off'} · w workspace"
    if s.bad_lines:
        text += f" · {s.bad_lines} unreadable event lines"
    return text
