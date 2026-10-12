# Pre-actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In Process 2 react turns, `write_file` and `private_message` become capped pre-actions taken before the turn's one action, so writing and messaging no longer use up the public move.

**Architecture:** `agent.take_step` gains an optional `writer` callback and a `max_writes` cap; with no writer it behaves exactly as now (Process 1, think, poll). The Runner passes a writer only for Process 2 react turns; it posts each write immediately, flagged `"pre_action": true`, and reports the result back to the model. Stagnation and the Observer's per-turn metrics skip flagged entries; the view hides the flag from agents.

**Tech Stack:** Python 3.14, uv, pytest (async tests via the existing config), Pydantic, SQLite.

**Spec:** `docs/superpowers/specs/2026-10-12-bikeshed-pre-actions-design.md`

## Global Constraints

- Process 2 react turns only. Process 1, the think step and the poll are unchanged; `uv run pytest tests/test_process1_golden.py` must pass **without** `BIKESHED_WRITE_GOLDEN=1`.
- Caps: `max_reads` stays 3; new `RunSettings.max_writes: int = Field(3, ge=0)`, counted separately.
- Write-cap message: `You have used all {max_writes} writes; choose a posting action.`
- Writes in round 1 are refused with exactly `In the opening round your action must be \`propose\`.`
- Write results go back to the model as `Write result:\n<text>` (reads keep `Read result:\n<text>`).
- Flag: `"pre_action": True` in the payload of every entry a pre-action posts (including `artifact_changed` and `write_rejected`). Agents never see the flag.
- Prompt wording stays flat and values-neutral; nothing about what pre-actions are for.
- Observer and TUI stay read-only: `test_observer_isolation.py` and `test_watch_isolation.py` must pass.
- A lab run may be live: check `pgrep -fl "bikeshed (run|postrun)"` before committing; if one is running, work in a sibling worktree (`../Bikeshed-pre-actions`).

## Review Focus

1. A pre-action write that conflicts mid-turn must not turn the turn into a `pass`: the turn's action still posts (Task 3 test `test_a_conflicting_pre_action_write_does_not_cost_the_turn`).
2. Messages followed by `pass` must still count as an idle round, or a quiet board with a busy backchannel never stagnates (Task 4).
3. The flag must not leak into what agents see (Task 3 test `test_the_pre_action_flag_is_not_shown_to_agents`).
4. An outage or crash mid-turn after a pre-action has posted: the round's discard must remove the flagged entries and files so the replay starts clean (Task 3 test `test_resume_discards_pre_action_entries_of_a_partial_round`).
5. Older runs with unflagged `write_file`/`private_message` must still read as the turn's action in the Observer (Task 5 test `test_unflagged_messages_in_old_runs_are_the_turn`).

---

### Task 1: `take_step` accepts pre-action writes

**Files:**
- Modify: `src/bikeshed/actions.py` (after `READ_ACTIONS`, ~line 124)
- Modify: `src/bikeshed/agent.py:20-82` (`Reader` aliases, `system_prompt`, `take_step`)
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `actions.WRITE_ACTIONS = (WriteFile, PrivateMessage)`; `actions.PRE_ACTIONS = READ_ACTIONS + WRITE_ACTIONS`; `agent.Writer = Callable[[Any], str]`; `take_step(..., writer: Writer | None = None, max_writes: int = 0)`; `system_prompt(display, temperament, max_reads, template="agent_system.md", max_writes=0)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agent.py`)

```python
WRITE = json.dumps({"action": {"type": "write_file", "path": "plan.md", "base_version": 0, "content": "x",
                               "note": "n"}})
DM = json.dumps({"action": {"type": "private_message", "to": "b", "text": "hi"}})


def recording_writer():
    done = []

    def write(action):
        done.append(action.type)
        return f"done {action.type}"

    return done, write


async def wstep(model, writer, round=2, max_writes=3, validate=allow, reader=no_reads):
    return await take_step(client=model, system="SYS", view="VIEW", round=round, max_reads=3, reader=reader,
                           validate=validate, writer=writer, max_writes=max_writes)


async def test_pre_action_writes_then_an_action():
    done, writer = recording_writer()
    model = FakeModel([WRITE, DM, CRITIQUE])
    outcome = await wstep(model, writer)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["write_file", "private_message"]
    assert model.calls[1][1][-1]["content"] == "Write result:\ndone write_file"
    assert model.calls[2][1][-1]["content"] == "Write result:\ndone private_message"


async def test_writes_beyond_the_cap_are_corrected():
    done, writer = recording_writer()
    model = FakeModel([DM, DM, CRITIQUE])
    outcome = await wstep(model, writer, max_writes=1)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["private_message"]
    assert "You have used all 1 writes; choose a posting action." in model.calls[2][1][-1]["content"]


async def test_writes_are_refused_in_the_opening_round():
    done, writer = recording_writer()
    model = FakeModel([DM, PROPOSE])
    outcome = await wstep(model, writer, round=1)
    assert isinstance(outcome.action, Propose)
    assert done == []
    assert "In the opening round your action must be `propose`." in model.calls[1][1][-1]["content"]


async def test_an_invalid_write_uses_the_correction_and_then_fails():
    done, writer = recording_writer()

    def validate(action):
        if action.type == "private_message":
            raise ActionError("private_message.to must be another member: ['c']")

    outcome = await wstep(FakeModel([DM, DM]), writer, validate=validate)
    assert isinstance(outcome.action, Pass) and outcome.failure == "invalid_action"
    assert done == []


async def test_without_a_writer_a_write_is_the_action():
    outcome = await step(FakeModel([WRITE]))
    assert isinstance(outcome.action, WriteFile)


async def test_reads_and_writes_have_separate_caps():
    done, writer = recording_writer()
    model = FakeModel([LIST, DM, LIST, CRITIQUE])
    outcome = await take_step(client=model, system="SYS", view="VIEW", round=2, max_reads=2,
                              reader=lambda a: "x", validate=allow, writer=writer, max_writes=1)
    assert outcome.action.type == "critique" and outcome.failure is None
    assert done == ["private_message"]


def test_system_prompt_fills_in_max_writes():
    text = system_prompt("Ash (a)", "Calm.", 3, "process2_agent_system.md", max_writes=2)
    assert "up to 3 reads and up to 2 writes" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -q`
Expected: FAIL — `take_step() got an unexpected keyword argument 'writer'` and `system_prompt() got an unexpected keyword argument 'max_writes'` (the last test keeps failing until Task 2 edits the prompt).

- [ ] **Step 3: Implement**

In `src/bikeshed/actions.py`, after `READ_ACTIONS = (...)`:

```python
WRITE_ACTIONS = (WriteFile, PrivateMessage)  # Process 2: taken before the turn's action
PRE_ACTIONS = READ_ACTIONS + WRITE_ACTIONS
```

In `src/bikeshed/agent.py`, import `WRITE_ACTIONS` alongside `READ_ACTIONS`, add the alias after `Validator`:

```python
Writer = Callable[[Any], str]
```

Replace `system_prompt`:

```python
def system_prompt(
    display: str, temperament: str, max_reads: int, template: str = "agent_system.md", max_writes: int = 0
) -> str:
    return render(template, display=display, temperament=temperament.strip(), max_reads=max_reads,
                  max_writes=max_writes)
```

`take_step` gets two keyword parameters and a counter:

```python
async def take_step(
    *, client: ModelClient, system: str, view: str, round: int, max_reads: int,
    reader: Reader, validate: Validator, shape: type[BaseModel] = STEP, opening_rule: bool = True,
    writer: Writer | None = None, max_writes: int = 0,
) -> StepOutcome:
    messages: list[Message] = [{"role": "user", "content": view}]
    schema = step_schema(shape)
    reads = 0
    writes = 0
```

and, inside the `try:` right after the `READ_ACTIONS` block's `continue`, before the opening-round check:

```python
            if writer is not None and isinstance(action, WRITE_ACTIONS):
                if opening_rule and round == 1:
                    raise ActionError("In the opening round your action must be `propose`.")
                if writes >= max_writes:
                    raise ActionError(f"You have used all {max_writes} writes; choose a posting action.")
                validate(action)
                writes += 1
                messages += [
                    {"role": "assistant", "content": result.text},
                    {"role": "user", "content": "Write result:\n" + writer(action)},
                ]
                continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent.py -q -k "not fills_in_max_writes"`
Expected: PASS. Then `uv run pytest tests/test_process1_golden.py -q`: PASS (unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/actions.py src/bikeshed/agent.py tests/test_agent.py
git commit -m "feat: take_step accepts capped pre-action writes"
```

### Task 2: Process 2 prompt and the `max_writes` setting

**Files:**
- Modify: `src/bikeshed/prompts/process2_agent_system.md`
- Modify: `src/bikeshed/config.py:40` (`RunSettings`) and the default config template (`max_reads = 3` line, ~129)
- Modify: `lab/bikeshed.toml` (gitignored; add under `[run]`)
- Test: `tests/test_agent.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `system_prompt(..., max_writes=)` from Task 1.
- Produces: `RunSettings.max_writes` (int, default 3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent.py`:

```python
def test_process2_prompt_lists_writes_as_pre_actions():
    text = system_prompt("Ash (a)", "Calm.", 3, "process2_agent_system.md", max_writes=3)
    pre, actions = text.split("\nPre-actions:\n")[1].split("\nActions:\n")
    assert '"type": "write_file"' in pre and '"type": "private_message"' in pre and '"type": "read_file"' in pre
    assert '"type": "write_file"' not in actions and '"type": "private_message"' not in actions
    assert "without seeing the others' proposals, and you cannot write or message." in text


def test_process1_prompt_keeps_write_file_as_an_action():
    text = system_prompt("Ash (a)", "Calm.", 3)
    assert "Pre-actions" not in text and '"type": "write_file"' in text
```

Append to `tests/test_config.py`:

```python
def test_max_writes_defaults_to_three_and_cannot_be_negative():
    from pydantic import ValidationError

    from bikeshed.config import RunSettings

    assert RunSettings().max_writes == 3
    with pytest.raises(ValidationError):
        RunSettings(max_writes=-1)
```

(If `pytest` is not yet imported in `tests/test_config.py`, add `import pytest` at the top.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent.py tests/test_config.py -q`
Expected: FAIL — no `Pre-actions` block; `RunSettings` has no `max_writes`.

- [ ] **Step 3: Implement**

In `src/bikeshed/prompts/process2_agent_system.md`:

Replace the end of the round paragraph
`In the opening round everyone proposes at once instead, without seeing the others' proposals.`
with
`In the opening round everyone proposes at once instead, without seeing the others' proposals, and you cannot write or message.`

Replace
```
Before your action you may make up to $max_reads reads. A read shows you its result and you then reply again.

Reads:
```
with
```
Before your action you may take pre-actions: up to $max_reads reads and up to $max_writes writes, in any order. Each shows you its result and you then reply again.

Pre-actions:
```
and move the two lines
```
- {"type": "write_file", "path": "...", "base_version": <current version, 0 for a new file>, "content": "...", "note": "..."}: write a file in the shared workspace, which every member can read. The path is relative to the workspace (write `plan.md`, not `workspace/plan.md`). The write is rejected if the file changed since base_version.
- {"type": "private_message", "to": "<member id>", "text": "..."}: a message only that member will see.
```
out of `Actions:` to the end of the `Pre-actions:` list (after `read_reviews`), unchanged.

In `src/bikeshed/config.py`, `RunSettings`, after `max_reads`:

```python
    max_writes: int = Field(3, ge=0)  # Process 2: pre-action writes (write_file, private_message) per turn
```

In the default config template, after `max_reads = 3`:

```
max_writes = 3                         # Process 2: writes and messages allowed before each turn's action
```

In `lab/bikeshed.toml`, under `[run]`, add `max_writes = 3`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent.py tests/test_config.py tests/test_process1_golden.py -q`
Expected: PASS, including Task 1's `test_system_prompt_fills_in_max_writes`.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/prompts/process2_agent_system.md src/bikeshed/config.py tests/test_agent.py tests/test_config.py
git commit -m "feat: Process 2 prompt lists writes as pre-actions; max_writes setting"
```

### Task 3: The Runner posts pre-action writes

**Files:**
- Modify: `src/bikeshed/runner.py` (`_turn` ~171-205; new `_pre_write`, `_pre_write_file` after `_post`)
- Modify: `src/bikeshed/view.py:33-38` (`render_entry`)
- Modify: `tests/conftest.py` (`_process2`: per-turn reply lists)
- Test: `tests/test_process2.py`, `tests/test_view.py`

**Interfaces:**
- Consumes: `take_step(writer=, max_writes=)`, `system_prompt(max_writes=)`, `RunSettings.max_writes`.
- Produces: `Runner._pre_write(ctx: RoundContext, agent_id: str, action: Any) -> str`; entries flagged `"pre_action": True`; `view.HIDDEN_KEYS = frozenset({"pre_action"})`.

- [ ] **Step 1: Let the Process 2 fake answer differently within a turn**

In `tests/conftest.py`, `_process2`, replace the last line of `respond`:

```python
        reply = plan.get(rnd, default)
        if isinstance(reply, list):  # one reply per model call within the turn; the last one repeats
            reply = reply[min((len(messages) - 1) // 2, len(reply) - 1)]
        return json.dumps({"action": reply(content)})
```

and add to its docstring: `A plan value may be a list: the replies for successive model calls in one turn.`

- [ ] **Step 2: Write the failing tests**

In `tests/test_process2.py`, replace `test_private_messages_reach_only_their_recipient` with:

```python
async def test_private_messages_reach_only_their_recipient(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "SECRET-DM"}
    clients = {"a": process2(react={1: propose("A"), 2: [dm, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    [message] = rows(conn, run_id, "private_message")
    assert (message["author"], message["to_"], json.loads(message["visible_to"])) == ("a", "b", ["a", "b"])
    assert json.loads(message["payload"]) == {"to": "b", "text": "SECRET-DM", "pre_action": True}
    assert any("SECRET-DM" in v for v in views(clients["b"]))
    assert not any("SECRET-DM" in v for v in views(clients["c"]))
```

and append:

```python
WRITE_PLAN = lambda v: {"type": "write_file", "path": "plan.md", "base_version": 0, "content": "PLAN", "note": "n"}


async def test_a_turn_writes_messages_and_then_acts(tmp_path, community, process2, find, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "c", "text": "see plan.md"}
    ask = lambda v: {"type": "question", "target": find(v, "propose", "b")[0], "text": "Why?"}
    clients = {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, dm, ask]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    turn = [(r["type"], json.loads(r["payload"]).get("pre_action")) for r in conn.execute(
        "SELECT type, payload FROM entries WHERE run_id = ? AND round = 2 AND type NOT IN ('think') ORDER BY id",
        (run_id,))][:4]
    assert turn == [("write_file", True), ("artifact_changed", True), ("private_message", True), ("question", None)]
    assert (tmp_path / "runs" / run_id / "workspace" / "plan.md").read_text() == "PLAN"
    replies = [m[-1]["content"] for _, m, _ in clients["a"].calls if len(m) > 1]
    assert "Write result:\nplan.md is now v1." in replies and "Write result:\nMessage sent to c." in replies


async def test_a_conflicting_pre_action_write_does_not_cost_the_turn(community, process2, find, runner_factory):
    store, conn = community
    ask = lambda v: {"type": "question", "target": find(v, "propose", "a")[0], "text": "Why?"}
    clients = {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B"), 2: [WRITE_PLAN, ask]}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=2)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert [r["author"] for r in rows(conn, run_id, "question")] == ["b"]  # b's turn still acted
    [rejected] = rows(conn, run_id, "write_rejected")
    assert json.loads(rejected["payload"])["current_version"] == 1
    assert ("b", "write_conflict") in [tuple(r) for r in conn.execute(
        "SELECT agent_id, kind FROM failures WHERE run_id = ?", (run_id,))]
    replies = [m[-1]["content"] for _, m, _ in clients["b"].calls if len(m) > 1]
    assert "Write result:\nWrite rejected: plan.md changed since v0; it is now v1." in replies


async def test_round_one_writes_are_refused(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "early"}
    clients = {"a": process2(react={1: [dm, propose("A")]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=1)
    run_id = runner.start("G", AGENTS, process=2)
    await runner.run(run_id)
    assert rows(conn, run_id, "private_message") == []
    assert "a" in [p["author"] for p in rows(conn, run_id, "propose")]


async def test_the_pre_action_flag_is_not_shown_to_agents(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "hello"}
    clients = {"a": process2(react={1: propose("A"), 2: [dm, lambda v: {"type": "pass"}]}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, rng=NoShuffle(), max_rounds=3)
    await runner.run(runner.start("G", AGENTS, process=2))
    assert any("hello" in v for v in views(clients["b"]))
    assert not any("pre_action" in v for a in AGENTS for v in views(clients[a]))


async def test_resume_discards_pre_action_entries_of_a_partial_round(tmp_path, community, process2, runner_factory):
    store, conn = community

    def clients():
        return {"a": process2(react={1: propose("A"), 2: [WRITE_PLAN, lambda v: {"type": "pass"}]}),
                "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}

    class CrashingRunner(Runner):
        async def _step(self, agent_id, ctx):
            outcome = await super()._step(agent_id, ctx)
            if ctx.round == 2 and agent_id == "a":
                raise RuntimeError("crash")
            return outcome

    run_id = runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).start("G", AGENTS, process=2)
    crashing = CrashingRunner(settings=RunSettings(max_rounds=2, process=2), conn=conn, store=store,
                              runs_dir=tmp_path / "runs", clients=clients(), rng=NoShuffle())
    with pytest.raises(RuntimeError):
        await crashing.run(run_id)
    assert len(rows(conn, run_id, "write_file")) == 1  # posted before the crash
    await runner_factory(store, conn, clients(), rng=NoShuffle(), max_rounds=2).run(run_id)
    assert len(rows(conn, run_id, "write_file")) == 1  # the replayed round wrote it once, not twice
```

(Add `from bikeshed.config import RunSettings` to the imports of `tests/test_process2.py` if it is not there.)

In `tests/test_view.py`, append:

```python
def test_render_entry_hides_harness_flags():
    from bikeshed.board import Entry
    from bikeshed.view import render_entry

    entry = Entry(7, "r1", 2, "a", "private_message", {"to": "b", "text": "hi", "pre_action": True}, [], "b", "t")
    text = render_entry(entry, lambda agent_id: agent_id)
    assert "text: hi" in text and "pre_action" not in text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_process2.py tests/test_view.py -q`
Expected: FAIL — no `pre_action` flag, a `private_message` is still a whole turn, `pre_action` shows in rendered entries.

- [ ] **Step 4: Implement**

In `src/bikeshed/view.py`, above `render_entry`:

```python
HIDDEN_KEYS = frozenset({"pre_action"})  # harness bookkeeping, never shown to agents
```

and in `render_entry`:

```python
    body = [f"  {key}: {_fmt(value)}" for key, value in entry.payload.items() if key not in HIDDEN_KEYS]
```

In `src/bikeshed/runner.py`, `_turn`: pass `max_writes` to `system_prompt` and a writer for Process 2 react turns:

```python
            system = system_prompt(
                who(agent_id), self.store.private(agent_id)["temperament.md"], self.settings.max_reads, template,
                max_writes=self.settings.max_writes,
            )
```

```python
            writer = (lambda action: self._pre_write(ctx, agent_id, action)) if ctx.process == 2 and phase is None \
                else None
            outcome = await take_step(
                ...,  # existing arguments unchanged
                shape=shape, opening_rule=opening_rule,
                writer=writer, max_writes=self.settings.max_writes,
            )
```

Add after `_post` (leave `_post` itself unchanged, so Process 1 posts exactly as before):

```python
    def _pre_write(self, ctx: RoundContext, agent_id: str, action: Any) -> str:
        """A write taken before the turn's action: posted now, flagged, and reported back to the agent."""
        with transaction(self.conn):
            if isinstance(action, PrivateMessage):
                entry: Entry | None = ctx.board.append(
                    ctx.round, agent_id, "private_message", {"to": action.to, "text": action.text, "pre_action": True},
                    to=action.to, visible_to=[agent_id, action.to])
                result = f"Message sent to {action.to}."
            else:
                entry, result = self._pre_write_file(ctx, agent_id, action)
        if entry is not None:
            ctx.events.emit("action_posted", round=ctx.round, agent=agent_id, entry=entry.id, type=entry.type,
                            failure=None)
        return result

    def _pre_write_file(self, ctx: RoundContext, agent_id: str, action: WriteFile) -> tuple[Entry | None, str]:
        flag = {"pre_action": True}
        path = action.path
        try:
            version = ctx.ws.write(path, action.base_version, action.content, author=agent_id, note=action.note)
            path = ctx.ws.canonical_path(path)
        except (InvalidPath, OSError, GitError, UnicodeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self._fail(ctx.run_id, ctx.round, agent_id, "write_error", reason)
            ctx.board.append(ctx.round, SYSTEM, "write_rejected",
                             {"agent": agent_id, "path": path, "reason": reason, **flag})
            return None, f"Write rejected: {reason}"
        except WriteConflict as exc:
            self._fail(ctx.run_id, ctx.round, agent_id, "write_conflict", str(exc))
            ctx.board.append(ctx.round, SYSTEM, "write_rejected", {
                "agent": agent_id, "path": path, "base_version": action.base_version,
                "current_version": exc.current_version, **flag,
            })
            return None, f"Write rejected: {path} changed since v{action.base_version}; it is now v{exc.current_version}."
        entry = ctx.board.append(ctx.round, agent_id, "write_file",
                                 {"path": path, "version": version, "note": action.note, **flag})
        ctx.board.append(ctx.round, SYSTEM, "artifact_changed", {
            "agent": agent_id, "path": path, "from_version": action.base_version, "to_version": version,
            "note": action.note, **flag,
        }, refs=[entry.id])
        return entry, f"{path} is now v{version}."
```

`RoundContext` exposes `board`, `ws`, `run_id`, `round` and `events` (see `phases.py`); `Entry`, `SYSTEM`, `transaction`, `WriteConflict`, `InvalidPath`, `GitError` are already imported in `runner.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_process2.py tests/test_view.py tests/test_runner.py tests/test_process1_golden.py -q`
Expected: PASS. If `test_resume_discards_pre_action_entries_of_a_partial_round` fails because the crash propagates differently, check how `test_crash_mid_round_rolls_back_and_resumes` in `tests/test_runner.py` raises and resumes, and match it; the assertion (one `write_file` after the replay) is what matters.

- [ ] **Step 6: Commit**

```bash
git add src/bikeshed/runner.py src/bikeshed/view.py tests/conftest.py tests/test_process2.py tests/test_view.py
git commit -m "feat: Process 2 posts pre-action writes immediately and flags them"
```

### Task 4: Stagnation looks only at the turn's action

**Files:**
- Modify: `src/bikeshed/consensus.py:58-60` (`_idle_round`)
- Test: `tests/test_consensus.py`, `tests/test_process2.py`

**Interfaces:**
- Consumes: the `"pre_action": True` payload flag from Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consensus.py`:

```python
def test_pre_action_messages_do_not_prevent_stagnation():
    dm = lambda id, rnd: e(id, rnd, "a", "private_message", to="b", text="psst", pre_action=True)
    entries = base()[:4] + [dm(5, 2), e(6, 2, "a", "pass"), e(7, 2, "b", "pass"), e(8, 2, "c", "pass"),
                            dm(9, 3), e(10, 3, "a", "pass"), e(11, 3, "b", "pass"), e(12, 3, "c", "pass")]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) == StopDecision("stagnation")


def test_a_message_taken_as_the_action_is_still_progress():
    entries = base()[:4] + [e(5, 2, "a", "private_message", to="b", text="psst"), e(6, 2, "b", "pass"),
                            e(7, 2, "c", "pass")] + [e(8 + i, 3, a, "pass") for i, a in enumerate(AGENTS)]
    assert stop_decision(entries, AGENTS, 3, RunSettings()) is None
```

Append to `tests/test_process2.py`:

```python
async def test_messages_followed_by_pass_still_stagnate(community, process2, runner_factory):
    store, conn = community
    dm = lambda v: {"type": "private_message", "to": "b", "text": "psst"}
    clients = {"a": process2(react={r: [dm, lambda v: {"type": "pass"}] for r in range(2, 7)} | {1: propose("A")}),
               "b": process2(react={1: propose("B")}), "c": process2(react={1: propose("C")})}
    runner = runner_factory(store, conn, clients, max_rounds=6)
    result = await runner.run(runner.start("G", AGENTS, process=2))
    assert result.reason == "stagnation"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_consensus.py tests/test_process2.py -q -k "stagnat or progress"`
Expected: FAIL — flagged messages count as activity.

- [ ] **Step 3: Implement**

In `src/bikeshed/consensus.py`:

```python
def _idle_round(entries: Sequence[Entry], agent_ids: Sequence[str], round: int) -> bool:
    posted = [e for e in entries if e.round == round and e.author in agent_ids and e.type != "think"
              and not e.payload.get("pre_action")]  # stagnation is about each turn's action, not what came before it
    return bool(posted) and all(_is_idle(e, entries) for e in posted)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_consensus.py tests/test_process2.py tests/test_process1_golden.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bikeshed/consensus.py tests/test_consensus.py tests/test_process2.py
git commit -m "feat: stagnation ignores pre-action entries"
```

### Task 5: The Observer keys per-turn metrics on the action

**Files:**
- Modify: `src/bikeshed/observer/process2.py:16-22` (`_turns`)
- Modify: `src/bikeshed/observer/metrics.py:15-16, 94-104` (`ACTIONS`, new `PRE_ACTIONS`, `action_mix`)
- Modify: `src/bikeshed/observer/report.py:70-75` (`_metrics` table)
- Modify: `src/bikeshed/observer/observe.py:152-154` (`share` denominator)
- Test: `tests/test_observer_process2.py`, `tests/test_observer_metrics.py`

**Interfaces:**
- Consumes: the `"pre_action": True` payload flag.
- Produces: `metrics.PRE_ACTIONS = ("pre:write_file", "pre:private_message")`; `action_mix` counts flagged entries under those keys, never under `ACTIONS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observer_process2.py`:

```python
def test_pre_action_messages_are_not_the_turn(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"}, round=1)
    for agent in ("b", "a"):
        think(conn, "r1", agent, 2)
    add_entry(conn, "r1", "b", "private_message", {"to": "a", "text": "psst", "pre_action": True}, round=2,
              visible_to=["b", "a"], to="a")
    lead = add_entry(conn, "r1", "b", "propose", {"answer_summary": "y", "method": "m"}, round=2)
    add_entry(conn, "r1", "a", "adopt", {"target": lead, "reason": "r"}, refs=[lead], round=2)
    # round 2: b's turn is its propose (taken up by a), not its message
    assert speaking_order(conn, closed_runs(conn)[0])[0] == OrderStat(1, 1, 1)


def test_unflagged_messages_in_old_runs_are_the_turn(community):
    store, conn = community
    add_run(conn, "r1", process=2)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"}, round=1)
    pm(conn, "r1", "b", "a", round=2)
    add_entry(conn, "r1", "a", "pass", {}, round=2)
    assert [s.turns for s in speaking_order(conn, closed_runs(conn)[0])] == [1, 1]
```

Append to `tests/test_observer_metrics.py`:

```python
def test_action_mix_counts_pre_actions_apart_from_turns(community):
    store, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "t", "pre_action": True}, to="b",
              visible_to=["a", "b"])
    add_entry(conn, "r1", "a", "write_file", {"path": "p.md", "version": 1, "note": "n", "pre_action": True})
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "t"}, to="b", visible_to=["a", "b"])
    add_entry(conn, "r1", "a", "pass", {})
    mix = action_mix(conn, closed_runs(conn)[0])
    assert mix["a"]["pre:private_message"] == 1 and mix["a"]["pre:write_file"] == 1
    assert mix["a"]["private_message"] == 1 and mix["a"]["write_file"] == 0 and mix["a"]["pass"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_observer_process2.py tests/test_observer_metrics.py -q`
Expected: FAIL — the flagged message is taken as b's turn; `pre:` keys are missing.

Check the `OrderStat` fields used above against its definition in `src/bikeshed/observer/process2.py` (the existing `test_speaking_order_skips_the_blind_first_round` builds `OrderStat(position, turns, taken_up)`); adjust the expected values to that order if the fields differ.

- [ ] **Step 3: Implement**

In `src/bikeshed/observer/process2.py`, `_turns`:

```python
def _turns(rows: list[sqlite3.Row]) -> dict[tuple[int, str], int]:
    """(round, member) -> entry id of that member's react turn: its first entry in the round that is neither a think
    note nor a pre-action. In runs before pre-actions existed, a write or message was itself the turn."""
    turns: dict[tuple[int, str], int] = {}
    for r in rows:
        if r["type"] != "think" and not json.loads(r["payload"]).get("pre_action"):
            turns.setdefault((r["round"], r["author"]), r["id"])
    return turns
```

In `src/bikeshed/observer/metrics.py`, after `ACTIONS`:

```python
PRE_ACTIONS = ("pre:write_file", "pre:private_message")  # Process 2 writes taken before a turn's action
```

and in `action_mix`:

```python
    mix: dict[str, Counter] = {agent: Counter({a: 0 for a in ACTIONS + PRE_ACTIONS}) for agent in run.agent_ids}
    for row in _entries(conn, run.id):
        if row["author"] not in mix:
            continue
        kind = row["type"]
        payload = json.loads(row["payload"])
        if kind == "pass" and "failure" in payload:
            kind = "failed"
        elif payload.get("pre_action"):
            kind = f"pre:{kind}"
        if kind in ACTIONS or kind in PRE_ACTIONS:
            mix[row["author"]][kind] += 1
    return mix
```

In `src/bikeshed/observer/report.py`, import `PRE_ACTIONS` with `ACTIONS` and use `ACTIONS + PRE_ACTIONS` for the `_metrics` table header, separator and counts (three places in `_metrics`).

In `src/bikeshed/observer/observe.py`, `share` divides by turns only:

```python
    def share(run: m.RunInfo, action: str) -> float | None:
        total = sum(c[a] for c in mixes[run.id].values() for a in m.ACTIONS)
        return None if total == 0 else sum(c[action] for c in mixes[run.id].values()) / total
```

(The chart still plots `m.ACTIONS` only, as "share of turns".)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_observer_process2.py tests/test_observer_metrics.py tests/test_observer_report.py tests/test_observer_observe.py tests/test_observer_isolation.py tests/test_watch_isolation.py -q`
Expected: PASS. If a report test pins the exact metrics-table header, update it to include the two `pre:` columns.

- [ ] **Step 5: Full suite and commit**

Run: `uv run pytest -q`
Expected: PASS, golden unchanged.

```bash
git add src/bikeshed/observer tests/test_observer_process2.py tests/test_observer_metrics.py tests/test_observer_report.py
git commit -m "feat: observer counts pre-actions apart from turns"
```
