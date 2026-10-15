# Generation 4, sub-project B: agent environment

*2026-10-15. Covers fixes 10, 11, 12 and 13 in `docs/gen4-fixes.md`. Must land before generation 4's first run, because it changes what agents see and who reviews whom. Sub-projects A (control integrity) and F (Process 3 and naming) are merged; C (measurement) and D (baseline run) come after this.*

## Goal

Generation 3's environment shaped the agents in ways nobody chose: reviews formed a star around whoever made the call, and agents voted as though a refine had changed the call's text. Fix those two before generation 4 starts. Record decisions for the two fixes that need no code (11 and 13). Every change stays values-neutral: it says what the harness does, never how an agent should behave.

| Fix | Outcome |
|---|---|
| 10, review pairs | Code: every participant reviews every other participant |
| 11, library ranking and brief leaks | Decision: no change; reuse logging moves to C |
| 12, refine | Code: both action lists say what a refine does |
| 13, copying within a round | Decision: no change; C measures it |

## Fix 10: review pairs

**Now.** `interactions()` (`postrun/feedback.py:9`) links two members only when one entry refs the other's, or when they wrote the same workspace file. `PostRun._calls` (`postrun/cycle.py:125`) has each member review the members it is linked to, then itself. Once everyone votes on one call, everyone's only link is to its caller. In generation 3, most runs had 14–20 of the 20 possible peer reviews, but runs 13 and 14 fell to 8, all of them to or from the caller.

**Change.** Review pairs come from participation, not refs.

- A **participant** is a member who, in this run, wrote at least one public entry that is not a `pass` (a plain pass and a failed turn both count as `pass`), or wrote at least one workspace file version (`files` table). Private entries (private messages, Process 2 think notes) don't count, because the reviewer couldn't cite them.
- Every participant reviews every other participant. Every member, participant or not, still writes its self-review, as now.
- A non-participant gets no peer reviews and writes none. A reviewer would have nothing of theirs to cite as evidence.
- The function stays plain code over the record: it reads entry types, authors and file authors, never what entries say. It replaces `interactions()` (same module, a name that says what it now computes, e.g. `participants()`), and `_calls` builds the pairs from it. `interactions()` has no other caller in the code and is removed; its tests in `tests/test_feedback.py` are replaced by tests of the new function.
- The `postrun_steps` keys are unchanged (`step = 'review'`, `agent_id` = reviewer, `subject` = reviewee), so a cycle that was paused resumes as before. A cycle paused under the old pairing and resumed under the new one simply runs the pairs it has not done yet.

With six agents that is at most 30 peer reviews and 6 self-reviews per run. Reputation (`reviews.py`) is unchanged; it is still peer ratings only, recency-weighted.

## Fix 11: library (decision)

**Ranking stays similarity-only.** `retrieve()` (`library.py:129`) keeps ranking by cosine similarity to the goal. Each method already shows its reception to the agents ("adopted by …, critiqued n×, cited by the winning call"), so agents can weigh reception themselves. If the harness ranked by reception, it would be amplifying the room's earlier choices on its own initiative.

**Logging reuse moves to C.** Detecting retrieved method text that reappears in proposals is the library half of fix 14's copy metric. The `library_retrieved` event already records which method ids each agent was shown, so C has what it needs.

**Brief facts in method text: checked, no change.** A method's text is written by an agent who may have quoted its private brief. That can't carry a fact into a run where the fact is still private:

- Queue goals run once. `next_goal` (`goals.py:292`) skips any goal that already has a run. Process 3 refuses free text, and the benchmark, the one goal that repeats, has no brief.
- After a run, every brief is revealed to every member (`feedback_packet(briefs=…)`), so a brief fact in a method is already known to the whole room.

Procedure: a briefed goal must not be re-run within a generation. The queue already enforces this. Re-running one by hand (adding the same goal again under a new id) would also be compromised through journals and lessons, not only through the library.

## Fix 12: refine

**Now.** A refine is its own entry (`target`, `change`, `reason`) with a ref to its target (`runner.py:281`, `actions.py:42`), and nothing ever changes the target. Both action lists (`prompts/agent_system.md:18`, `prompts/process2_agent_system.md:22`) give the refine shape with no description, unlike propose and adopt. In runs 5 and 7, agents supported calls for changes that only a refine of the call contained.

**Change.** Both action lists describe refine in the same way as the other actions:

```
- {"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}: a change to an entry, posted as its own entry; the target's text stays as it was.
```

That is a statement of fact about the harness, not advice. Nothing changes in how refines are stored, rendered or voted on. Making a refine produce a new version of a call was considered and rejected: votes attach to one call id, so it would change what voters are voting on.

**Golden recording.** `agent_system.md` is a Process 1 prompt, so the Process 1 golden recording changes. Andrew regenerates it (`BIKESHED_WRITE_GOLDEN=1 uv run pytest tests/test_process1_golden.py`), and the diff must be this one prompt line and nothing else. Until then `test_process1_golden.py` fails, and that one failure is expected.

## Fix 13: copying within a round (decision)

Process 2's react phase is sequential from round 2 (`phases.py:77`), so each speaker sees the moves made before its turn. Run 14's copied vote reasons are that design at work, not a bug. Process 3 runs the same rounds and keeps it. Making votes blind would make generation 4's rounds differ from generation 3's on top of the briefs, and the two generations would no longer be comparable on process. C measures the effect, through fix 14's copy metric and the existing `speaking_order` metric (`observer/process2.py:92`).

## Docs

- `docs/gen4-fixes.md`: under fixes 10–13, a line recording the outcome and pointing to this spec. In the development-order table, F is marked done (`49e0480`) and B in progress.
- `CLAUDE.md`: the "After a run" paragraph says reviews are between members who took part in the run.

## Testing

TDD with `FakeModel`.

- **Star shape.** A caller and three members who only vote on its call: every one of the four reviews the other three. The old ref-based links would have given each voter only the caller.
- **Participation.**
  - A member whose only entries are plain passes, and one whose every turn failed, are not participants. Neither gets peer reviews or writes any, and each still writes its self-review.
  - A member whose only act is a workspace file write is a participant.
  - Private messages alone don't make a member a participant.
- **Resume.** A review step paused partway resumes without redoing finished pairs.
- **Prompt.** The refine description appears in both `agent_system.md` and `process2_agent_system.md`.
- **Golden.** After Andrew regenerates it, the diff is the refine line only.
