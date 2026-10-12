"""Builds what one agent sees in one step: privacy-filtered and trimmed to its context budget."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aiswarm.board import SYSTEM, Board, Entry
from aiswarm.consensus import call_status
from aiswarm.profiles import ProfileStore, PublicProfile
from aiswarm.reviews import Reputation, dimension_reputations, reputation
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
    journals: tuple[tuple[str, str], ...] = ()
    norms: str = ""
    reputations: Mapping[str, Reputation] = field(default_factory=dict)
    library: tuple[str, ...] = ()
    dimension_scores: Mapping[str, list[tuple[str, Reputation]]] = field(default_factory=dict)


def _norms(store: ProfileStore) -> str:
    path = store.root / "norms.md"
    return path.read_text() if path.exists() else ""


def collect_inputs(
    *, agent_id: str, goal: str, round: int, board: Board, store: ProfileStore,
    workspace: Workspace, view_entries: int, half_life: float = 5.0, library: Sequence[str] = (),
) -> ViewInputs:
    entries = board.entries_for(agent_id)
    if round == 1:
        entries = [e for e in entries if not (e.round == 1 and e.author != agent_id)]
    private = store.private(agent_id)
    return ViewInputs(
        agent_id=agent_id, goal=goal, round=round, entries=entries,
        own_notes={name: private[name] for name in _OWN_NOTES},
        profiles={i: store.public(i) for i in store.ids()},
        files=workspace.list_files(), view_entries=view_entries,
        journals=() if store.record(agent_id).control else tuple(store.journals(agent_id, 2)),
        norms=_norms(store),
        reputations={i: reputation(board.conn, i, half_life) for i in store.ids()},
        library=tuple(library),
        dimension_scores={i: dimension_reputations(board.conn, i, half_life) for i in store.ids()},
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


# Notes, journals and library methods carry over from runs with other goals; small models drift back to those goals
NOTES_HEADER = "# Your private notes from earlier runs (only you can see these; those runs may have had other goals)"
METHODS_HEADER = "# Methods from earlier runs (each is shown with its own goal, which may differ from this one)"


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
    notes += [f"## journal/{run_id}.md\n{text.strip()}" for run_id, text in inputs.journals if text.strip()]
    if notes:
        fixed.append(f"{NOTES_HEADER}\n" + "\n\n".join(notes))
    if inputs.norms.strip():
        fixed.append("# Community norms\n" + inputs.norms.strip())
    positions: list[str] = []
    adopted: set[int] = set()
    for entry in inputs.entries:
        if entry.author != inputs.agent_id:
            continue
        if entry.type == "propose":
            positions.append(f"- You proposed #{entry.id} (r{entry.round})")
        elif entry.type == "adopt" and entry.payload.get("target") not in adopted:
            adopted.add(entry.payload.get("target"))
            positions.append(f"- You adopted #{entry.payload.get('target')} (r{entry.round})")
    if positions:
        fixed.append("# Your positions\n" + "\n".join(positions))
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

    footer = f"# Current goal\n{inputs.goal}"  # restated last, where a small model attends most
    remaining = budget_chars - sum(len(part) + 2 for part in [*fixed, footer])
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
        rep = inputs.reputations.get(agent_id)
        suffix = f" · {rep.label()}" if rep else ""
        suffix += "".join(f" · {name} {score.score:.1f}" for name, score in inputs.dimension_scores.get(agent_id, [])
                          if score.count and score.score is not None)
        pronouns = f" · {profile.pronouns}" if profile.pronouns else ""
        block = f"- {profile.name} ({agent_id}){pronouns} — {profile.headline}{suffix}\n  {profile.introduction}"
        if profile.former_names:
            block += f"\n  formerly: {', '.join(profile.former_names)}"
        members.append(block)

    parts = list(fixed)
    for title, items in (
        ("# Entries that respond to you", [render_entry(e, who) for e in about_me]),
        ("# Recent board entries", [render_entry(e, who) for e in recent]),
        (METHODS_HEADER, list(inputs.library)),
        ("# Workspace files", files),
        ("# Community members", members),
    ):
        section, remaining = _fit(title, items, remaining)
        if section:
            parts.append(section)
    return "\n\n".join([*parts, footer])
