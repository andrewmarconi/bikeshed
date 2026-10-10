"""Builds what one agent sees in one step: privacy-filtered and trimmed to its context budget."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiswarm.board import SYSTEM, Board, Entry
from aiswarm.consensus import call_status
from aiswarm.profiles import ProfileStore, PublicProfile
from aiswarm.workspace import Workspace

Who = Callable[[str], str]
_OWN_NOTES = ("self.md", "lessons.md", "peer-notes.md")


def make_who(profiles: dict[str, PublicProfile | None]) -> Who:
    def who(author: str) -> str:
        if author == SYSTEM:
            return "system"
        profile = profiles.get(author)
        return f"{profile.name if profile else author} ({author})"

    return who


def _fmt(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render_entry(entry: Entry, who: Who) -> str:
    head = f"#{entry.id} [r{entry.round}] {who(entry.author)} {entry.type}"
    if entry.refs:
        head += " → " + ", ".join(f"#{ref}" for ref in entry.refs)
    body = [f"  {key}: {_fmt(value)}" for key, value in entry.payload.items()]
    return "\n".join([head, *body])


@dataclass(frozen=True)
class ViewInputs:
    agent_id: str
    goal: str
    round: int
    entries: list[Entry]
    own_notes: dict[str, str]
    profiles: dict[str, PublicProfile | None]
    files: list[tuple[str, int]]
    view_entries: int


def collect_inputs(
    *, agent_id: str, goal: str, round: int, board: Board, store: ProfileStore,
    workspace: Workspace, view_entries: int,
) -> ViewInputs:
    entries = board.entries()
    if round == 1:
        entries = [e for e in entries if not (e.round == 1 and e.author != agent_id)]
    private = store.private(agent_id)
    return ViewInputs(
        agent_id=agent_id, goal=goal, round=round, entries=entries,
        own_notes={name: private[name] for name in _OWN_NOTES},
        profiles={i: store.public(i) for i in store.ids()},
        files=workspace.list_files(), view_entries=view_entries,
    )


def _fit(title: str, items: list[str], remaining: int) -> tuple[str | None, int]:
    """Keep the newest items (end of list) that fit; render them in original order."""
    used = len(title) + 2
    kept: list[str] = []
    for item in reversed(items):
        if used + len(item) + 1 > remaining:
            break
        kept.append(item)
        used += len(item) + 1
    if not kept:
        return None, remaining
    return title + "\n" + "\n".join(reversed(kept)), remaining - used


def build_view(inputs: ViewInputs, budget_chars: int) -> str:
    who = make_who(inputs.profiles)
    header = f"# Goal\n{inputs.goal}\n\n# Round {inputs.round}\nYou are {who(inputs.agent_id)}."
    if inputs.round == 1:
        header += (
            "\nThis is the opening round. Propose your own position (action `propose`). "
            "Other members' opening proposals are hidden until the round ends."
        )
    fixed = [header]
    notes = [f"## {name}\n{text.strip()}" for name, text in inputs.own_notes.items() if text.strip()]
    if notes:
        fixed.append("# Your private notes (only you can see these)\n" + "\n\n".join(notes))
    calls = [e for e in inputs.entries if e.type == "call_consensus"]
    if calls:
        lines = []
        for call in calls:
            status = call_status(inputs.entries, call.id)
            support = ", ".join(sorted(who(a) for a in status.supporters)) or "none"
            objection = ", ".join(sorted(who(a) for a in status.objectors)) or "none"
            critiqued = "yes" if status.critiqued else "no"
            lines.append(
                f"- #{call.id} by {who(call.author)}: support: {support} · object: {objection} · critiqued: {critiqued}"
            )
        fixed.append("# Open calls for consensus\n" + "\n".join(lines))

    remaining = budget_chars - sum(len(part) + 2 for part in fixed)
    recent = inputs.entries[-inputs.view_entries:]
    recent_ids = {e.id for e in recent}
    mine = {e.id for e in inputs.entries if e.author == inputs.agent_id}
    about_me = [e for e in inputs.entries if e.id not in recent_ids and set(e.refs) & mine]
    files = [f"- {path} (v{version})" for path, version in inputs.files] or ["(no files yet)"]
    members = []
    for agent_id, profile in inputs.profiles.items():
        if profile is None:
            members.append(f"- {agent_id} ({agent_id}) — (no profile yet)")
            continue
        block = f"- {profile.name} ({agent_id}) — {profile.headline}\n  {profile.introduction}"
        if profile.former_names:
            block += f"\n  formerly: {', '.join(profile.former_names)}"
        members.append(block)

    parts = list(fixed)
    for title, items in (
        ("# Entries that respond to you", [render_entry(e, who) for e in about_me]),
        ("# Recent board entries", [render_entry(e, who) for e in recent]),
        ("# Workspace files", files),
        ("# Community members", members),
    ):
        section, remaining = _fit(title, items, remaining)
        if section:
            parts.append(section)
    return "\n\n".join(parts)
