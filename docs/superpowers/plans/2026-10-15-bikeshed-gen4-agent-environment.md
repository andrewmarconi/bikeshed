# Generation 4 Agent Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post-run reviews pair every member who took part in a run, and both agent system prompts say what a refine does.

**Architecture:** `postrun/feedback.py` replaces `interactions()` (ref-based links) with `participants()` (who took part), and `PostRun._calls` builds review pairs from it. Both action lists in the system prompts gain a factual description of refine. Fixes 11 and 13 are decisions only. They are recorded in `docs/gen4-fixes.md`.

**Tech Stack:** Python 3.14, uv, pytest (asyncio), `FakeModel`.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-gen4-agent-environment-design.md`

## Global Constraints

- Work on a feature branch `agent-env` in the main checkout (no worktree). Check `pgrep -fl "bikeshed (run|postrun)"` before every commit; if a run is live, stop and ask.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (subagents use their own model's name).
- Never set `BIKESHED_WRITE_GOLDEN`. After Task 2, `tests/test_process1_golden.py` fails; Andrew regenerates it. That is the only failure allowed in the full suite.
- Values-neutral wording: the refine line states what the harness does, nothing about how to behave. Exact text: `: a change to an entry, posted as its own entry; the target's text stays as it was.`
- A participant: a member with at least one **public** entry whose type is not `pass`, or at least one row in `files` for the run. Private entries (`visible_to` set: think notes, private messages) never count.
- Every participant reviews every other participant; every member reviews itself; non-participants get no peer reviews and write none. Review subjects are ordered: other participants sorted, then self (as now).
- `postrun_steps` keys stay `('review', reviewer, reviewee)`.
- Full suite: `uv run pytest -q` (about 4 minutes). Redirect output to a file and read its tail.

## Review Focus

1. A member whose every turn failed (all `pass` with a `failure` payload) is not a participant. Pinned in Task 1 (`test_participants_skip_passes_failures_and_private_entries`).
2. A member who only wrote a workspace file (Process 2 pre-action `write_file`, or only a `files` row) is a participant. Pinned in Task 1 (`test_participants_count_file_writers`).
3. A member's only entries are private messages. They are not a participant, even though another member's entry may ref theirs. Pinned in Task 1.
4. A post-run cycle resumed after a partial review step redoes only the pairs left undone. Pinned by the existing `test_rerun_retries_only_unfinished_calls_and_never_duplicates`, which must still pass with the new pairing.
5. System entries and non-members (`SYSTEM`, ids not in `agent_ids`) never become participants. Pinned in Task 1.

---

### Task 1: Review pairs from participation (fix 10)

**Files:**
- Modify: `src/bikeshed/postrun/feedback.py:9-33` (replace `interactions`)
- Modify: `src/bikeshed/postrun/cycle.py:25` (import), `:121-128` (`_calls` review branch)
- Test: `tests/test_feedback.py` (replace the two `interactions` tests)
- Test: `tests/test_postrun.py` (update pairing assertions; add star and non-participant tests)

**Interfaces:**
- Produces: `participants(entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]) -> list[str]`. It returns the members who took part, in `agent_ids` order. The caller passes `public(entries)`, but the function also skips any entry with `visible_to` set, so it is safe on its own.

- [ ] **Step 0: Branch**

```bash
pgrep -fl "bikeshed (run|postrun)"; git checkout -b agent-env
```
Expected: pgrep prints nothing; `Switched to a new branch 'agent-env'`.

- [ ] **Step 1: Write the failing unit tests**

In `tests/test_feedback.py`, change the import line to:

```python
from bikeshed.postrun.feedback import feedback_packet, participants, transcript
```

Replace `test_interactions_from_refs_either_way_and_shared_files` and `test_interactions_ignore_system_and_non_members` with:

```python
def test_participants_are_members_with_a_public_non_pass_entry():
    # board_with_consensus: a, b, c post; d posts nothing
    assert participants(board_with_consensus(), [], AGENTS) == ["a", "b", "c"]


def test_participants_skip_passes_failures_and_private_entries():
    entries = [
        e(2, 1, "a", "propose", answer_summary="Red", method="M"),
        e(3, 1, "b", "pass"),
        e(4, 2, "c", "pass", failure="model_error"),
        Entry(5, "r1", 2, "d", "private_message", {"text": "hi"}, [2], "a", "t", ("d", "a")),
        Entry(6, "r1", 2, "d", "think", {"text": "note"}, [], "all", "t", ("d",)),
    ]
    assert participants(entries, [], AGENTS) == ["a"]


def test_participants_count_file_writers():
    entries = [e(2, 1, "a", "pass")]
    assert participants(entries, [("plan.md", "b"), ("plan.md", "b")], ["a", "b"]) == ["b"]


def test_participants_ignore_system_and_non_members():
    entries = [e(2, 1, "a", "propose"), e(3, 1, SYSTEM, "artifact_changed", links=[2]), e(4, 1, "zed", "critique", links=[2])]
    assert participants(entries, [("x.md", "zed"), ("y.md", SYSTEM)], ["a", "b"]) == ["a"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_feedback.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'participants'`.

- [ ] **Step 3: Implement `participants`**

In `src/bikeshed/postrun/feedback.py`, replace the whole `interactions` function with:

```python
def participants(
    entries: Sequence[Entry], file_authors: Sequence[tuple[str, str]], agent_ids: Sequence[str]
) -> list[str]:
    """The members who took part in a run: a public entry other than a pass, or a workspace file written."""
    took_part = {entry.author for entry in entries if entry.visible_to is None and entry.type != "pass"}
    took_part |= {author for _, author in file_authors}
    return [agent_id for agent_id in agent_ids if agent_id in took_part]
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `uv run pytest tests/test_feedback.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing integration tests**

In `tests/test_postrun.py`, add after `finished_run`:

```python
def star_run(conn, tmp_path, make_run):
    """r1: a proposes and calls; b and c only vote on the call. Every ref points at a's entries."""
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    board.append(1, "a", "propose", {"answer_summary": "Red", "method": "M"})                       # 2
    board.append(2, "a", "call_consensus", {"synthesis": "Red", "refs": [2], "credits": []}, refs=[2])  # 3
    board.append(2, "b", "vote", {"target": 3, "stance": "support", "reason": "R"}, refs=[3])     # 4
    board.append(2, "c", "vote", {"target": 3, "stance": "support", "reason": "R"}, refs=[3])     # 5
    board.append(2, SYSTEM, "run_closed", {"reason": "consensus", "call_id": 3})                  # 6
    conn.execute("UPDATE runs SET status = 'closed', ended_reason = 'consensus' WHERE id = 'r1'")
    report = tmp_path / "runs" / "r1" / "report"
    report.mkdir(parents=True)
    (report / "answer.md").write_text("# Answer\nRED\n")
    return "r1"
```

and these tests:

```python
async def test_voters_on_one_call_still_review_each_other(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = star_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    pairs = {(r["reviewer"], r["reviewee"]) for r in conn.execute("SELECT reviewer, reviewee FROM reviews")}
    assert pairs == {(x, y) for x in "abc" for y in "abc"}


async def test_a_member_who_only_passed_writes_only_a_self_review(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    board.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    board.append(1, "a", "propose", {"answer_summary": "Red", "method": "M"})        # 2
    board.append(1, "b", "critique", {"target": 2, "about": "method", "points": "P"}, refs=[2])  # 3
    board.append(1, "c", "pass", {})                                                  # 4
    board.append(2, "c", "pass", {"failure": "model_error"})                          # 5
    board.append(2, SYSTEM, "run_closed", {"reason": "budget", "call_id": None})      # 6
    conn.execute("UPDATE runs SET status = 'closed', ended_reason = 'budget' WHERE id = 'r1'")
    report = tmp_path / "runs" / "r1" / "report"
    report.mkdir(parents=True)
    (report / "answer.md").write_text("# Answer\nRED\n")
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run("r1")
    pairs = {(r["reviewer"], r["reviewee"]) for r in conn.execute("SELECT reviewer, reviewee FROM reviews")}
    assert pairs == {("a", "a"), ("a", "b"), ("b", "a"), ("b", "b"), ("c", "c")}
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q -k "voters_on_one_call or only_passed"`
Expected: `test_voters_on_one_call_still_review_each_other` FAILS: pairs lack `("b", "c")` and `("c", "b")`. `test_a_member_who_only_passed_writes_only_a_self_review` may already pass, because the old ref links give the same pairs here. That's fine: it pins the non-participant rule against the new code.

- [ ] **Step 7: Wire `participants` into the cycle**

In `src/bikeshed/postrun/cycle.py` line 25:

```python
from bikeshed.postrun.feedback import feedback_packet, participants, transcript
```

Replace the review branch of `_calls`:

```python
        if step == "review":
            files = [(r["path"], r["author"]) for r in self.conn.execute(
                "SELECT path, author FROM files WHERE run_id = ?", (ctx.run_id,))]
            taking_part = participants(public(ctx.entries), files, ctx.agent_ids)

            def subjects(reviewer: str) -> list[str]:
                others = sorted(p for p in taking_part if p != reviewer) if reviewer in taking_part else []
                return [*others, reviewer]

            return [
                _Call(step, reviewer, reviewee, lambda r=reviewer, e=reviewee: self._review(ctx, r, e))
                for reviewer in ctx.agent_ids for reviewee in subjects(reviewer)
            ]
```

- [ ] **Step 8: Update the existing pairing assertions in `tests/test_postrun.py`**

`finished_run`'s c proposes, so c is now a participant. Change its docstring to:

```python
    """r1: a, b, c propose; b critiques a. All three took part, so all three review each other."""
```

In `test_cycle_writes_journals_reviews_and_responses`, replace the pairs block and the c-responses assertion with:

```python
    pairs = {(r["reviewer"], r["reviewee"]) for r in conn.execute("SELECT reviewer, reviewee FROM reviews")}
    assert pairs == {(x, y) for x in "abc" for y in "abc"}
    for agent in "abc":
        peer = reviews_of(conn, agent, run_id=run_id)
        assert len(peer) == 2 and all(responses_for(conn, [p.id])[p.id].stance == "accept" for p in peer)
```

(Delete the line `assert ("responses", "c", "") not in steps(conn)`.)

In `test_review_citing_unknown_entries_is_corrected_then_recorded_as_failed`:

```python
    assert result.status == "done" and result.failed == 3              # a→a, a→b and a→c
    ...
    assert kinds == ["postrun:review:invalid_reply"] * 3
```

- [ ] **Step 9: Run the post-run and feedback tests**

Run: `uv run pytest tests/test_postrun.py tests/test_feedback.py -q 2>&1 | tail -30`
Expected: all pass. If another test fails because it encoded ref-based pairing (it counts reviews, response calls or model calls that depended on c having no peer reviews), update its expected values to the participation rule. Record each one in the ledger as a ruling: the test name, the old value and the new value. A failure for any other reason is a bug; debug it.

- [ ] **Step 10: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/postrun/feedback.py src/bikeshed/postrun/cycle.py tests/test_feedback.py tests/test_postrun.py
git commit -m "feat: post-run reviews pair every member who took part, not only members linked by refs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Say what a refine does (fix 12)

**Files:**
- Modify: `src/bikeshed/prompts/agent_system.md:18`
- Modify: `src/bikeshed/prompts/process2_agent_system.md:22`
- Test: `tests/test_agent.py` (add one parametrized test after `test_system_prompts_say_how_the_workspace_works`)

**Interfaces:**
- Consumes: `system_prompt(display, temperament, max_reads, template)` from `bikeshed.agent` (existing).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("template", ["agent_system.md", "process2_agent_system.md"])
def test_system_prompts_say_a_refine_leaves_its_target_unchanged(template):
    text = system_prompt("Ash (a)", "Calm.", 2, template)
    assert ('{"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}: a change to an entry, '
            "posted as its own entry; the target's text stays as it was.") in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_agent.py -q -k refine_leaves`
Expected: 2 FAILED (assertion: the line is not in either prompt).

- [ ] **Step 3: Edit both prompts**

In both `src/bikeshed/prompts/agent_system.md` and `src/bikeshed/prompts/process2_agent_system.md`, replace the line

```
- {"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}
```

with

```
- {"type": "refine", "target": <entry id>, "change": "...", "reason": "..."}: a change to an entry, posted as its own entry; the target's text stays as it was.
```

- [ ] **Step 4: Run the test to verify it passes, and confirm the golden failure is this line only**

Run: `uv run pytest tests/test_agent.py -q -k refine_leaves`
Expected: 2 passed.

Run: `uv run pytest tests/test_process1_golden.py -q 2>&1 | tail -20`
Expected: FAIL, and the only difference shown is the refine line in the system prompt. Do **not** regenerate it. Ledger: `Task 2: golden recording awaits Andrew's regeneration (refine line only)`.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/prompts/agent_system.md src/bikeshed/prompts/process2_agent_system.md tests/test_agent.py
git commit -m "feat: the action lists say a refine is its own entry and leaves its target unchanged

The Process 1 golden recording changes by this one line; regenerate it with BIKESHED_WRITE_GOLDEN.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Record the outcomes (fixes 10–13)

**Files:**
- Modify: `docs/gen4-fixes.md` (fixes 10, 11, 12, 13; development-order table)
- Modify: `CLAUDE.md` ("After a run" paragraph)

- [ ] **Step 1: Add an outcome line under each fix in `docs/gen4-fixes.md`**

After fix 10's paragraph:

```markdown
    *Done in sub-project B (`2026-10-15-bikeshed-gen4-agent-environment-design.md`):* every member who took part in the run (a public entry other than a pass, or a workspace file) reviews every other member who took part. Refs no longer decide the pairs.
```

After fix 11's paragraph:

```markdown
    *Decided in sub-project B:* ranking stays similarity-only. Each method's reception is already in its label, so agents can weigh it themselves. Logging which retrieved text reappears moves to sub-project C, with fix 14's copy metric. Brief facts can't leak through method text into a run where they are still private: queue goals run once, and post-run reveals every brief to every member. A briefed goal must not be re-run within a generation.
```

After fix 12's "Checked against the code" paragraph:

```markdown
    *Done in sub-project B:* both action lists now say a refine is "a change to an entry, posted as its own entry; the target's text stays as it was." Refines are still stored and voted on as before.
```

After fix 13's "Checked against the code" paragraph:

```markdown
    *Decided in sub-project B:* no change. Process 3 keeps Process 2's sequential react, so generation 4's rounds match generation 3's. Sub-project C measures the effect (fix 14 and `speaking_order`).
```

- [ ] **Step 2: Update the development-order table**

F's row: `**Done** (merged 2026-10-15, `49e0480`)`. B's row: `**In progress**`. Change the sentence "F comes straight after A, ahead of B:" only if it now reads wrong; it describes history and can stay.

- [ ] **Step 3: Update `CLAUDE.md`**

In "### After a run", change `review (named 360° reviews with cited evidence and ratings)` to `review (named 360° reviews with cited evidence and ratings, between every pair of members who took part in the run)`.

- [ ] **Step 4: Full suite**

Run: `uv run pytest -q > .superpowers/sdd/2026-10-15-bikeshed-gen4-agent-environment/suite.txt 2>&1; tail -15 .superpowers/sdd/2026-10-15-bikeshed-gen4-agent-environment/suite.txt`
Expected: `1 failed` (`tests/test_process1_golden.py`, the refine line), everything else passed.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add docs/gen4-fixes.md CLAUDE.md
git commit -m "docs: record sub-project B outcomes for fixes 10-13

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

When B merges, set its table row to `**Done**` with the merge commit.
