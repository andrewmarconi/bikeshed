"""Goal briefs (Process 4): facts a goal gives the community, shared by all or private to one member.

Plain code. The Runner stores each run's briefs when it starts; the View, the Scorer, the post-run cycle and the
Observer read them back from `run_briefs`. A brief states facts about the situation; it never tells anyone what to do.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BriefSpec:
    """A goal's briefs as written in goals.toml, before they are assigned to a run's members."""
    shared: str | None = None
    shared_constraints: tuple[str, ...] = ()
    private: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (text, constraints) per brief, in file order
