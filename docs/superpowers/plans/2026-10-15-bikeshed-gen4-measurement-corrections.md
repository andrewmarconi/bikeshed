# Generation 4 Measurement Corrections (C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Observer's self-image, think-vs-act and private-vs-public analyses, the identity timeline, the export, and the narrative say what the record says.

**Architecture:** Read-side changes only:
- `observer/inputs.py`: what judges read;
- `observer/judge.py`: the think-vs-act reply and check;
- `observer/metrics.py` and `observer/report.py`: control labels;
- `export.py`: provenance rows;
- `postrun/cycle.py`: the narrative header string only;
- `cli.py`: the `show` label.

Nothing reaches an agent.

**Tech Stack:** Python 3.14, uv, pytest (asyncio), `FakeModel`, the `community` fixture and `tests/observer_helpers.py`.

**Spec:** `docs/superpowers/specs/2026-10-15-bikeshed-gen4-measurement-corrections-design.md`

## Global Constraints

- Work on a feature branch `measure-c1` in the main checkout. Check `pgrep -fl "bikeshed (run|postrun)"` before every commit; if a run is live, stop and ask.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never set `BIKESHED_WRITE_GOLDEN`. `tests/test_process1_golden.py` may already fail from sub-project B, until Andrew regenerates it. That is the only failure allowed, and nothing in this plan changes it.
- Observer code opens the store read-only and never writes it. `test_observer_isolation.py` must keep passing.
- Exact strings:
  - identity suffix: `(control; reset before its next run)`
  - self-image control note: `_control: compared with its seed, not its previous run_`
  - narrative header: `_Written by the narrator model from the account and not checked against the record. Where it disagrees with account.md, the account is right. No agent ever sees this._`
  - show label: `(The narrative below is the narrator model's retelling, not checked against the record.)`
  - think-vs-act input keys: `round N think note`, `round N actions`
- Full suite: `uv run pytest -q` (about 4 minutes). Redirect output to a file in the plan workspace and read its tail.

## Review Focus

1. A cached `think_vs_act` judgment from before this change has no `round` in its points. The report must still render it, with no `[rN]`. Pinned in Task 3 (`test_old_think_vs_act_points_without_round_still_render`).
2. An agent no longer in the store (removed agent dir) appears in an old run's `agent_ids`. Control detection must not crash. Pinned in Task 2 (`controls` built only from ids in `store.ids()`).
3. A run with no provenance rows at all (pre-provenance runs): export still writes empty `models`, `max_rounds` and `code_commit`. Pinned in Task 5 (the existing `r2` in `_seed` has no provenance rows; assert its row).
4. A round where the agent wrote a think note and only a plain `pass` (not a failure): it is kept, because a plain pass is an action. Pinned in Task 3.
5. A reset commit that comes after this run's end, from a later run, must not be chosen as "before". Pinned in Task 1 (`before_moment` takes only commits strictly before `run.end`).

---

### Task 1: Self-image compares the right moments (fix 9)

**Files:**
- Modify: `src/bikeshed/observer/inputs.py` (`self_image_inputs`; new `before_moment`)
- Test: `tests/test_observer_inputs.py`

**Interfaces:**
- Produces: `before_moment(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> datetime | None`. `self_image_inputs(store, runs, run, agent_id)` keeps its signature.

- [ ] **Step 0: Branch**

```bash
pgrep -fl "bikeshed (run|postrun)"; git checkout -b measure-c1
```
Expected: pgrep prints nothing; `Switched to a new branch 'measure-c1'`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_observer_inputs.py`. Extend the import from `bikeshed.observer.inputs` with `before_moment`, and the import from `bikeshed.observer.metrics` with `RunInfo`. Add `from datetime import datetime` at the top.

```python
def _info(run_id, end, agents=("a",)):
    return RunInfo(run_id, "g", None, None, False, "budget", tuple(agents), end, end)


def test_before_is_the_latest_run_that_ended_earlier_not_the_one_created_earlier(community):
    store, _ = community
    created_first = _info("x1", "2000-01-02T03:00:00+00:00")   # created first, finished last
    created_second = _info("x2", "2000-01-02T02:00:00+00:00")
    runs = [created_first, created_second]                      # closed_runs order: creation
    assert before_moment(store, runs, created_second, "a") is None
    assert before_moment(store, runs, created_first, "a") == datetime.fromisoformat(created_second.end)


def test_a_control_agents_before_is_its_reset(community):
    store, _ = community
    store.create("e", "fake", "Seed.", control=True)
    store.set_public("e", "Eve", "Eve headline", "I am Eve.")
    store.save_seed_public("e")
    store.write_private("e", "self.md", "Changed by a cycle.")
    store.reset_to_seed("e")
    reset = next(c.at for c in reversed(store.history("e")) if c.subject == "reset control agent e to seed")
    earlier = _info("x1", "2000-01-01T00:00:00+00:00", agents=("e",))
    now = _info("x2", FUTURE, agents=("e",))
    assert before_moment(store, [earlier, now], now, "e") == reset


def test_a_reset_after_the_run_is_not_its_before(community):
    store, _ = community
    store.create("e", "fake", "Seed.", control=True)
    store.set_public("e", "Eve", "Eve headline", "I am Eve.")
    store.save_seed_public("e")
    store.write_private("e", "self.md", "Changed.")
    store.reset_to_seed("e")                                    # committed now, after both runs below
    earlier = _info("x1", "2000-01-01T00:00:00+00:00", agents=("e",))
    this = _info("x2", "2000-01-02T00:00:00+00:00", agents=("e",))
    assert before_moment(store, [earlier, this], this, "e") == datetime.fromisoformat(earlier.end)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_inputs.py -q 2>&1 | tail -5`
Expected: collection ERROR, `ImportError: cannot import name 'before_moment'`.

- [ ] **Step 3: Implement**

In `src/bikeshed/observer/inputs.py`, add `from bikeshed.profiles import ProfileStore` if it isn't imported (it is). Replace `self_image_inputs` with:

```python
def before_moment(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> datetime | None:
    """When an agent's "before" snapshot is read: the later of the end of its latest run that ended before this one,
    and its latest reset to seed before this run ended. Runs are compared by when they ended, not when they began."""
    end = datetime.fromisoformat(run.end)
    ends = [datetime.fromisoformat(r.end) for r in runs
            if r.id != run.id and agent_id in r.agent_ids and datetime.fromisoformat(r.end) < end]
    resets = [c.at for c in store.history(agent_id)
              if c.subject == f"reset control agent {agent_id} to seed" and c.at < end]
    moments = ends + resets
    return max(moments) if moments else None


def self_image_inputs(store: ProfileStore, runs: list[RunInfo], run: RunInfo, agent_id: str) -> dict[str, str]:
    now = _after(run)
    out = {"self.md now": store.file_at(agent_id, "self.md", now) or NONE,
           "public profile now": store.file_at(agent_id, "public.toml", now) or NONE}
    moment = before_moment(store, runs, run, agent_id)
    if moment is not None:
        then = moment + timedelta(seconds=1)
        out["self.md before"] = store.file_at(agent_id, "self.md", then) or NONE
        out["public profile before"] = store.file_at(agent_id, "public.toml", then) or NONE
    else:
        out["self.md before"] = out["public profile before"] = "(no earlier run)"
    return out
```

- [ ] **Step 4: Run the inputs tests**

Run: `uv run pytest tests/test_observer_inputs.py -q 2>&1 | tail -3`
Expected: all pass, including the existing `test_self_image_inputs_compare_with_the_previous_run`.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/inputs.py tests/test_observer_inputs.py
git commit -m "fix: self-image compares with the run that ended before, or the control's reset, not creation order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The control's own updates are labelled (fix 8)

**Files:**
- Modify: `src/bikeshed/observer/metrics.py` (`identity_events`)
- Modify: `src/bikeshed/observer/report.py` (`RunSection.controls`, `_per_agent` notes, Self-image call)
- Modify: `src/bikeshed/observer/observe.py` (pass `controls` into `RunSection`)
- Test: `tests/test_observer_metrics.py`, `tests/test_observer_report.py`

**Interfaces:**
- Produces: `metrics.CONTROL_NOTE = "(control; reset before its next run)"`, and `RunSection.controls: frozenset[str] = frozenset()`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_observer_metrics.py`:

```python
def test_a_control_agents_own_updates_are_labelled(community):
    store, conn = community
    add_run(conn, "r1", created="2000-01-01T00:00:00+00:00")
    store.create("e", "fake", "Seed.", control=True)
    store.write_private("e", "self.md", "After one cycle.")
    store.write_private("a", "self.md", "I changed.")
    events = identity_events(conn, store, closed_runs(conn))
    control = next(e for e in events if e.agent_id == "e" and e.kind == "self.md")
    member = next(e for e in events if e.agent_id == "a" and e.kind == "self.md")
    assert control.detail == "(control; reset before its next run)"
    assert member.detail == ""
```

In `tests/test_observer_report.py`:

```python
def self_image(agent):
    return Judgment("self_image", "r1", agent, "ok", {"changes": [], "private_public_gap": "none",
                                                      "gap_evidence": ""}, None, "claude-x", "p")


def test_the_controls_self_image_says_it_is_compared_with_its_seed():
    text = render_run(section(judgments=[self_image("a"), self_image("b")], controls=frozenset({"b"})))
    block = text.split("## Self-image\n")[1]
    alpha, bea = block.split("### Bea (b)")
    assert "compared with its seed" not in alpha
    assert bea.lstrip().startswith("_control: compared with its seed, not its previous run_")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_metrics.py tests/test_observer_report.py -q -k "control_agents_own or compared_with_its_seed" 2>&1 | tail -4`
Expected: 2 failed. The metrics test fails on detail `''`; the report test fails with `TypeError: ... unexpected keyword argument 'controls'`.

- [ ] **Step 3: Implement**

`src/bikeshed/observer/metrics.py`: under `_IDENTITY_FILES`, add:

```python
CONTROL_NOTE = "(control; reset before its next run)"  # a control's own changes are wiped by its next reset
```

In `identity_events`, before the loop:

```python
    controls = {agent_id for agent_id in store.ids() if store.record(agent_id).control}
```

and replace the `events.append(...)` line with:

```python
            detail = kind[1]
            if agent_id in controls and kind[0] != "control reset":
                detail = f"{detail} {CONTROL_NOTE}".strip()
            events.append(IdentityEvent(commit.at, agent_id, kind[0], detail, score))
```

`src/bikeshed/observer/report.py`: add a field to `RunSection` after `briefs`:

```python
    controls: frozenset[str] = frozenset()  # control agents in this run: their self-image is compared with their seed
```

Change `_per_agent` to accept notes:

```python
def _per_agent(s: RunSection, title: str, analysis: str, body, none_without_judgment: str | None = None,
               notes: dict[str, str] | None = None) -> list[str]:
    out = [f"## {title}", ""]
    for agent in s.run.agent_ids:
        j = _find(s.judgments, analysis, agent)
        out.append(f"### {s.who(agent)}")
        if notes and agent in notes:
            out += [notes[agent], ""]
```

(The rest of the function is unchanged.) In `render_run`, the Self-image call gains:

```python
        notes={agent: "_control: compared with its seed, not its previous run_" for agent in s.controls})
```

`src/bikeshed/observer/observe.py`: in the `RunSection(...)` call, add:

```python
            controls=frozenset(a for a in run.agent_ids if a in known and store.record(a).control),
```

with `known = set(store.ids())` computed once, next to `who = make_who(...)`.

- [ ] **Step 4: Run the observer tests**

Run: `uv run pytest tests/test_observer_metrics.py tests/test_observer_report.py tests/test_observer_observe.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/metrics.py src/bikeshed/observer/report.py src/bikeshed/observer/observe.py tests/test_observer_metrics.py tests/test_observer_report.py
git commit -m "feat: the Observer labels a control agent's own profile changes and compares its self-image with its seed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Think notes are paired with the same round's moves (fix 14a)

**Files:**
- Modify: `src/bikeshed/observer/inputs.py` (`think_vs_act_inputs`)
- Modify: `src/bikeshed/observer/judge.py` (`ThinkActPoint.round`, `_think_and_act`)
- Modify: `src/bikeshed/prompts/observer/think_vs_act.md`
- Modify: `src/bikeshed/observer/report.py` (think-vs-act line)
- Test: `tests/test_observer_inputs.py`, `tests/test_observer_judge.py`, `tests/test_observer_report.py`, `tests/test_observer_observe.py`

**Interfaces:**
- Produces: inputs keyed `round N think note` / `round N actions`. Each point in a `think_vs_act` result has `round: int`; points cached before this change don't.

- [ ] **Step 1: Write the failing input tests**

In `tests/test_observer_inputs.py`, replace `test_think_vs_act_inputs_hold_only_the_agents_own_thoughts_and_actions` with:

```python
def test_think_vs_act_inputs_pair_each_note_with_its_rounds_moves(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt the mango plan."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "b", "think", {"text": "B's secret."}, round=2, visible_to=["b"])
    add_entry(conn, "r1", "a", "adopt", {"target": 1, "reason": "Mango is fine."}, refs=[1], round=2)
    add_entry(conn, "r1", "a", "think", {"text": "Say no quietly."}, round=3, visible_to=["a"])
    add_entry(conn, "r1", "a", "private_message", {"to": "c", "text": "Between us: no."}, round=3,
              visible_to=["a", "c"], to="c")
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": "To a from b."}, round=3,
              visible_to=["b", "a"], to="a")
    add_entry(conn, "r1", "b", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["round 2 think note", "round 2 actions", "round 3 think note", "round 3 actions"]
    assert "I doubt the mango plan." in got["round 2 think note"] and "B's secret." not in got["round 2 think note"]
    assert "Mango is fine." in got["round 2 actions"] and "Pear" not in got["round 2 actions"]
    assert "Between us: no." in got["round 3 actions"] and "To a from b." not in got["round 3 actions"]


def test_think_vs_act_inputs_leave_out_failed_turns_but_keep_plain_passes(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note one."}, round=1, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "model_error"}, round=1)
    add_entry(conn, "r1", "a", "think", {"text": "Note two."}, round=2, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "invalid_action"}, round=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Pear", "method": "m"}, round=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note three."}, round=3, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {}, round=3)
    who = make_who({i: store.public(i) for i in store.ids()})
    got = think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who)
    assert list(got) == ["round 2 think note", "round 2 actions", "round 3 think note", "round 3 actions"]
    assert "failure" not in got["round 2 actions"] and "Pear" in got["round 2 actions"]
    assert "pass" in got["round 3 actions"]


def test_think_vs_act_inputs_none_when_every_noted_round_only_failed(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "think", {"text": "Note."}, round=1, visible_to=["a"])
    add_entry(conn, "r1", "a", "pass", {"failure": "model_error"}, round=1)
    who = make_who({i: store.public(i) for i in store.ids()})
    assert think_vs_act_inputs(conn, closed_runs(conn)[0], "a", who) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_inputs.py -q -k think_vs_act 2>&1 | tail -5`
Expected: the three new tests FAIL (the keys are still `think notes` / `public actions`). `test_think_vs_act_inputs_none_without_think_notes` passes.

- [ ] **Step 3: Implement the inputs**

Replace `think_vs_act_inputs` in `src/bikeshed/observer/inputs.py`:

```python
def think_vs_act_inputs(conn: sqlite3.Connection, run: RunInfo, agent_id: str, who: Who) -> dict[str, str] | None:
    """Round by round: one agent's think note and what it then did in that round (its public entries and the
    private messages it sent). Failed turns are left out, and a round with nothing else is dropped. Nothing it received."""
    own = [e for e in Board(conn, run.id).entries() if e.author == agent_id]

    def acted(e) -> bool:
        failed = e.type == "pass" and "failure" in e.payload
        return e.type != "think" and not failed and (e.visible_to is None or e.type == "private_message")

    def show(entries) -> str:
        return "\n\n".join(render_entry(e, who) for e in entries)

    out: dict[str, str] = {}
    for rnd in sorted({e.round for e in own if e.type == "think"}):
        done = [e for e in own if e.round == rnd and acted(e)]
        if done:
            out[f"round {rnd} think note"] = show([e for e in own if e.round == rnd and e.type == "think"])
            out[f"round {rnd} actions"] = show(done)
    return out or None
```

`public` may now be unused in `inputs.py`. Remove it from the import if so.

- [ ] **Step 4: Run the input tests**

Run: `uv run pytest tests/test_observer_inputs.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Write the failing judge and report tests**

In `tests/test_observer_judge.py`, replace `TVA` and `tva_reply`:

```python
TVA = {"round 2 think note": "#5 [r2] Alpha (a) think\n  text: I doubt the mango plan.",
       "round 2 actions": "#6 [r2] Alpha (a) adopt → #3\n  target: 3\n  reason: Mango is fine.",
       "round 3 think note": "#7 [r3] Alpha (a) think\n  text: Say no quietly.",
       "round 3 actions": "#8 [r3] Alpha (a) private_message → Cyd (c)\n  text: Between us: no."}


def tva_reply(think_quote, action_quote, round=2):
    return json.dumps({"points": [{"round": round, "relation": "diverges", "about": "mango",
                                   "think_quote": think_quote, "action_quote": action_quote}]})
```

In `test_think_vs_act_rejects_a_quote_from_the_wrong_side`, change the error assertion to `assert j.status == "failed" and "think note" in j.error`. Then add:

```python
async def test_think_vs_act_rejects_an_action_from_another_round(tmp_path):
    other_round = tva_reply("I doubt the mango plan.", "Between us: no.", round=2)  # the action is round 3's
    judge, client = make_judge(tmp_path, [other_round, other_round])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and len(client.calls) == 2


async def test_think_vs_act_rejects_a_round_not_in_the_input(tmp_path):
    missing = tva_reply("I doubt the mango plan.", "Mango is fine.", round=9)
    judge, _ = make_judge(tmp_path, [missing, missing])
    j = await judge.run(ANALYSES["think_vs_act"], run_id="r1", agent_id="a", inputs=TVA)
    assert j.status == "failed" and "round 9" in j.error
```

In `tests/test_observer_report.py`, give `tva()`'s point `"round": 2`. In `test_process_2_run_report`, change the expected line to `"- [r2] **diverges** — mango: think “I doubt it” / action “Mango is fine”"`, and add:

```python
def test_old_think_vs_act_points_without_round_still_render():
    old = Judgment("think_vs_act", "r1", "a", "ok", {"points": [
        {"relation": "agrees", "about": "pear", "think_quote": "x", "action_quote": "y"}]}, None, "claude-x", "p")
    text = render_run(section(run=P2, process2=P2_SECTION, judgments=[old]))
    assert "- **agrees** — pear: think “x” / action “y”" in text
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/test_observer_judge.py tests/test_observer_report.py -q -k "think_vs_act or process_2_run_report" 2>&1 | tail -8`
Expected: FAILS. The judge tests fail on the `think notes` key lookups (`KeyError`) and the missing round check; `test_process_2_run_report` fails because there's no `[r2]`. `test_old_think_vs_act_points_without_round_still_render` passes already, which is fine: it pins backward compatibility.

- [ ] **Step 7: Implement judge, prompt and report**

`src/bikeshed/observer/judge.py`: `ThinkActPoint` gains a first field `round: int = Field(ge=1)`. Replace `_think_and_act`:

```python
def _think_and_act(reply: Any, inputs: dict[str, str]) -> str | None:
    """Each point compares one round's note with what the agent did in that same round; each side is quoted from its
    own section of that round, so a quote from another round, or a note passed off as an action, fails."""
    missing = []
    for p in reply.points:
        thought, done = inputs.get(f"round {p.round} think note"), inputs.get(f"round {p.round} actions")
        if thought is None or done is None:
            missing.append(f"round {p.round} is not in the input")
            continue
        missing += [repr(p.think_quote)] if not quoted_in(p.think_quote, thought) else []
        missing += [repr(p.action_quote)] if not quoted_in(p.action_quote, done) else []
    if missing:
        return ("each point names a round from the input; its think_quote must be copied from that round's think note "
                "and its action_quote from that round's actions; not found: " + "; ".join(missing[:5]))
    return None
```

`src/bikeshed/prompts/observer/think_vs_act.md`: replace the whole file with:

```
You are a careful qualitative researcher studying a community of AI agents. You are reading one agent's private think notes from one session, which it wrote to itself before its turn in each round, and what it then did in that same round: its public actions and the private messages it sent. The input is grouped by round. Nobody in the community will ever see your analysis.

For each round, compare what the agent thought with what it did in that round. List the points where they agree and the points where they diverge: for example a position held in private but not stated in public, a doubt kept to itself, or a private message that says something the public record does not. Describe what you see; do not judge whether the agent was right.

For each point give:
- round: the round number
- relation: "agrees" or "diverges"
- about: what the point concerns, in a few words
- think_quote: copied character for character from that round's think note
- action_quote: copied character for character from that round's actions

If there is nothing to compare, return an empty list.

Reply with JSON only: {"points": [{"round": 1, "relation": "...", "about": "...", "think_quote": "...", "action_quote": "..."}]}
```

`src/bikeshed/observer/report.py`: add a helper above `render_run`:

```python
def _tva_line(p: dict) -> str:
    """One think-vs-act point; points judged before rounds were recorded have no round."""
    prefix = f"[r{p['round']}] " if "round" in p else ""
    return f"- {prefix}**{p['relation']}** — {p['about']}: think “{p['think_quote']}” / action “{p['action_quote']}”"
```

and make the think-vs-act body `lambda res: [_tva_line(p) for p in res["points"]] or ["- nothing to compare"]`.

- [ ] **Step 8: Update the observe tests**

In `tests/test_observer_observe.py`:
- Add `import re` if it's missing.
- In `judge_reply`, replace the `"## think notes"` branch with:

```python
    if "think note" in text:
        rnd = int(re.search(r"^## round (\d+) think note", text, re.M).group(1))
        return json.dumps({"points": [{"round": rnd, "relation": "diverges", "about": "pear",
                                       "think_quote": "Pear is weak.", "action_quote": "Pear is fine."}]})
```

- In `test_observe_reports_a_process_2_run`, change the round 1 propose to `{"answer_summary": "Pear is fine.", "method": "m"}`, so round 1's note and action hold the two quotes.
- In the per-analysis judge test, change both `"## think notes"` checks to `"think note"`.

- [ ] **Step 9: Run the observer tests**

Run: `uv run pytest tests/test_observer_inputs.py tests/test_observer_judge.py tests/test_observer_report.py tests/test_observer_observe.py -q 2>&1 | tail -3`
Expected: all pass. A test failing because it encoded the old keys gets the same mechanical update; ledger each one as a ruling.

- [ ] **Step 10: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/inputs.py src/bikeshed/observer/judge.py src/bikeshed/prompts/observer/think_vs_act.md src/bikeshed/observer/report.py tests/test_observer_inputs.py tests/test_observer_judge.py tests/test_observer_report.py tests/test_observer_observe.py
git commit -m "fix: think vs act pairs each note with the same round's moves and leaves out failed turns

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Private vs public reads the "before" journal only (fix 14b)

**Files:**
- Modify: `src/bikeshed/observer/inputs.py` (`_journal`, `private_public_inputs`)
- Test: `tests/test_observer_inputs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_private_public_reads_only_the_journal_written_before_the_reviews(community):
    store, conn = community
    add_run(conn, "r1", created=FUTURE)
    add_journal(conn, "r1", "a", "before", "Bea talked too much.")
    add_journal(conn, "r1", "a", "after", "Bea's review of me was fair.")
    add_review(conn, "r1", "a", "b", 2, less_of="rambling")
    who = make_who({i: store.public(i) for i in store.ids()})
    got = private_public_inputs(conn, closed_runs(conn)[0], "a", who)
    assert "Bea talked too much." in got["journal"] and "fair" not in got["journal"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_observer_inputs.py -q -k before_the_reviews 2>&1 | tail -3`
Expected: FAIL (`fair` is in the journal).

- [ ] **Step 3: Implement**

```python
def _journal(conn: sqlite3.Connection, run_id: str, agent_id: str, section: str | None = None) -> str:
    rows = conn.execute("SELECT section, text FROM journals WHERE run_id = ?1 AND agent_id = ?2 "
                        "AND (?3 IS NULL OR section = ?3) ORDER BY created_at", (run_id, agent_id, section)).fetchall()
    return "\n\n".join(f"### {r['section']} reviews\n{r['text'].strip()}" for r in rows) or NONE
```

In `private_public_inputs`, pass `section="before"`: the journal written before the agent saw its reviews.

- [ ] **Step 4: Run the inputs tests**

Run: `uv run pytest tests/test_observer_inputs.py -q 2>&1 | tail -3`
Expected: all pass, including `test_themes_inputs`, which still sees both sections.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/observer/inputs.py tests/test_observer_inputs.py
git commit -m "fix: private vs public compares reviews with the journal written before the reviews were read

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Export lists every model and the final round cap (fix 15)

**Files:**
- Modify: `src/bikeshed/export.py` (`_run_row`)
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

```python
def _provenance(conn, run_id, phase, model, max_rounds, commit="abc"):
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, ?, 0, ?, '{}', ?, '{}', 1, 'f', 't')",
        (run_id, phase, commit, json.dumps({"run": {"max_rounds": max_rounds, "close_rule": "critiqued"}}),
         json.dumps({"a": {"backend": "x", "model": model}})))


def test_runs_csv_lists_every_model_and_the_last_round_cap(tmp_path, community):
    _, conn = community
    _seed(conn)
    _provenance(conn, "r1", "start", "deepseek", 12, commit="start-commit")
    _provenance(conn, "r1", "resume", "qwen", 8, commit="resume-commit")
    _provenance(conn, "r1", "postrun", "narrator-model", 8)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = export(_cfg(tmp_path), tmp_path / "out")
    rows = {r["run_id"]: r for r in csv.DictReader((out / "runs.csv").open())}
    assert rows["r1"]["models"] == "deepseek;qwen" and rows["r1"]["max_rounds"] == "8"
    assert rows["r1"]["code_commit"] == "start-commit"
    assert rows["r2"]["models"] == "" and rows["r2"]["max_rounds"] == "" and rows["r2"]["code_commit"] == ""
    assert "- models: deepseek;qwen;" in (out / "runs" / "r1" / "transcript.md").read_text()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_export.py -q -k every_model 2>&1 | tail -3`
Expected: FAIL (`models == 'deepseek'`).

- [ ] **Step 3: Implement**

In `_run_row`, replace the `start = ...`, `config = ...` and `models = ...` lines with:

```python
    rows = conn.execute("SELECT * FROM run_provenance WHERE run_id = ? AND phase IN ('start', 'resume') ORDER BY id",
                        (run_id,)).fetchall()  # the run itself; post-run rows describe the cycle after it
    start = next((r for r in rows if r["phase"] == "start"), None)
    config = _loads(rows[-1]["config"]) if rows else {}  # the last start or resume: caps lowered at resume show
    models = {m.get("model", "") for r in rows for m in (_loads(r["models"]) or {}).values()}
```

and the `"models"` value with `";".join(sorted(models))`.

- [ ] **Step 4: Run the export tests**

Run: `uv run pytest tests/test_export.py -q 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/export.py tests/test_export.py
git commit -m "fix: export lists every model a run used and the round cap it ended with

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The narrative is marked unchecked (fix 16)

**Files:**
- Modify: `src/bikeshed/postrun/cycle.py:463-465` (header string)
- Modify: `src/bikeshed/cli.py` (`show`)
- Test: `tests/test_postrun.py` (`test_the_narrator_writes_a_user_only_narrative`), `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

In `test_the_narrator_writes_a_user_only_narrative`, after the `startswith` assertion, add:

```python
    assert "_Written by the narrator model from the account and not checked against the record. Where it disagrees " \
           "with account.md, the account is right. No agent ever sees this._" in text
```

In `tests/test_cli.py`, after `test_run_then_show`:

```python
def test_show_labels_the_narrative_as_unchecked(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config)
    run_id = run_id_from(result.output)
    label = "(The narrative below is the narrator model's retelling, not checked against the record.)"
    assert label not in invoke("show", run_id, "-c", config).output
    (tmp_path / "runs" / run_id / "report" / "narrative.md").write_text("# Narrative\n\nNARRATIVE-TEXT\n")
    shown = invoke("show", run_id, "-c", config).output
    assert shown.index(label) < shown.index("NARRATIVE-TEXT")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_postrun.py tests/test_cli.py -q -k "user_only_narrative or narrative_as_unchecked" 2>&1 | tail -4`
Expected: 2 failed.

- [ ] **Step 3: Implement**

`src/bikeshed/postrun/cycle.py`, the narrative write:

```python
        (report / "narrative.md").write_text(
            "# Narrative\n\n_Written by the narrator model from the account and not checked against the record. "
            "Where it disagrees with account.md, the account is right. No agent ever sees this._\n\n"
            + answer.reply.narrative.strip() + "\n"
```

(Keep the closing of the call as it is.) `src/bikeshed/cli.py`, in `show`'s loop:

```python
    for name in ("answer.md", "minority.md", "briefs.md", "account.md", "narrative.md"):
        path = report / name
        if path.exists():
            if name == "narrative.md":  # also covers narratives written before the header said so
                typer.echo("(The narrative below is the narrator model's retelling, not checked against the record.)\n")
            typer.echo(path.read_text())
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_postrun.py tests/test_cli.py -q -k "narrat" 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add src/bikeshed/postrun/cycle.py src/bikeshed/cli.py tests/test_postrun.py tests/test_cli.py
git commit -m "feat: the narrative says it is not checked against the record, in its header and in show

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Record the outcomes

**Files:**
- Modify: `docs/gen4-fixes.md`

- [ ] **Step 1: Add an outcome line under fixes 8, 9, 14, 15 and 16**

Each goes after that fix's last paragraph, indented four spaces like the "Checked against the code" lines:

- Fix 8: `*Done in sub-project C1 (`2026-10-15-bikeshed-gen4-measurement-corrections-design.md`):* the identity timeline marks a control agent's own changes "(control; reset before its next run)", and its Self-image section says it is compared with its seed. Both come from the agent's record, not commit text.`
- Fix 9: `*Done in sub-project C1:* "before" is the later of the end of the agent's latest run that ended before this one and its latest reset before this run ended.`
- Fix 14: `*C1 did two of these:* `think_vs_act` now pairs each note with the same round's moves and leaves out failed turns, and `private_public` reads only the "before" journal. Copying, credit accuracy and rule-gaming are sub-project C2. The disclosure and fabrication judges are checked by hand against the first generation 4 runs.`
- Fix 15: `*Done in sub-project C1:* `models` is the union over start and resume provenance rows, and `max_rounds` and `close_rule` come from the last of them. `transcript.md` agrees.`
- Fix 16: `*Done in sub-project C1:* the header says the narrative isn't checked against the record and that the account wins, and `bikeshed show` labels it the same way, including old narratives.`

- [ ] **Step 2: Split C in the development-order table**

Replace C's row with two rows:

```markdown
| C1 | Measurement corrections | Fixes 8, 9, 14 (`think_vs_act`, `private_public`), 15, 16 | No | **In progress** |
| C2 | New measurements | Fixes 7 (twin's brief slot), 14 (copying, credit accuracy, rule-gaming), 17 (norm overlap), library reuse from B | No | Any time; re-run over generation 4 |
```

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q > .superpowers/sdd/2026-10-15-bikeshed-gen4-measurement-corrections/suite.txt 2>&1; tail -6 .superpowers/sdd/2026-10-15-bikeshed-gen4-measurement-corrections/suite.txt`
Expected: everything passes, except `tests/test_process1_golden.py` if Andrew hasn't regenerated it yet.

- [ ] **Step 4: Commit**

```bash
pgrep -fl "bikeshed (run|postrun)"
git add docs/gen4-fixes.md
git commit -m "docs: record sub-project C1 outcomes; split C into C1 and C2

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

When C1 merges, set its row to `**Done**` with the merge commit.
