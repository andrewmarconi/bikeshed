"""Plain text for each watcher panel. Pure functions of RunState, so they test without a terminal."""

from aiswarm.watch.state import RunState


def header(s: RunState) -> str:
    return f"{s.label} ({s.category}) · run {s.run_id} · round {s.round}/{s.max_rounds} · {s.phase} — {s.goal}"


def cards(s: RunState) -> str:
    lines = []
    for card in s.cards.values():
        rep = "–" if card.reputation is None else f"{card.reputation:.1f}"
        control = " [control]" if card.control else ""
        lines += [f"{card.name} ({card.agent_id}){control} ★{rep}  {card.state}", f"  last: {card.last or '–'}"]
    return "\n".join(lines) or "(no agents yet)"


def calls(s: RunState) -> str:
    lines = [f"#{c.entry_id} by {c.caller}: {c.supporters} for, {c.objectors} against" for c in s.calls]
    return "\n".join(["Calls for consensus", *(lines or ["(none yet)"])])


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


def footer(s: RunState, following: bool) -> str:
    text = f"q quit · f follow: {'on' if following else 'off'}"
    if s.bad_lines:
        text += f" · {s.bad_lines} unreadable event lines"
    return text
