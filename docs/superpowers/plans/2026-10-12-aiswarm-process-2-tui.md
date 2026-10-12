# Three-pane TUI (Plan 4c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `aiswarm watch` shows a Process 2 run as three panes (Table | Thinking / Private messages) over a status bar, coloured by action and by agent, with a `w` pop-up for the workspace and post-run grid; a Process 1 run shows a full-width Table.

**Architecture:** `state.py` keeps building plain data: each `FeedLine` now carries its parts (head, author, name, kind, target, body, recipient, stance, failure) and knows its pane and its plain `text`; `RunState` learns the run's process and the current Process 2 phase. `render.py` turns that data into Rich `Text` (colour applied to spans in code, never parsed from entry text) and builds the status bar and pop-up text. `app.py` lays out the panes, routes new lines by pane, swaps the post-run grid in, and hosts the `w` modal.

**Tech Stack:** Python 3.14, uv, Textual 8.2.8, Rich 15.0.0, pytest (asyncio auto; Textual `run_test` pilot).

**Spec:** `docs/superpowers/specs/2026-10-11-aiswarm-process-2-design.md` §8 (TUI), §10.7 (testing). The Plan 3c TUI spec `docs/superpowers/specs/2026-10-11-aiswarm-tui-design.md` still governs what §8 does not change (read-only, never writes the community, no files created).

## Global Constraints

- The watcher is read-only and never imported by the agent path (`tests/test_watch_isolation.py`); it creates no files.
- Colour is applied to spans in code (Rich `Text`), never parsed from entry text, so brackets in entries stay literal.
- Action styles (spec §8.2): propose green; refine, adopt blue; critique orange; question cyan; call_consensus bold magenta; vote stance word green (support) / red (object); abstain dim; write_file yellow; pass dim grey, `failed: <reason>` red; system entries dim italic.
- Agents: one colour each in id order (pink, teal, violet, gold, sky blue; then repeat), names bold, used in every pane and the status bar. Status words: `speaking…` bold, `thinking…` dim, `failed: …` red.
- Layout (spec §8.1): Table about half the width; Thinking and Private messages split the right half; all three wrap, follow and re-wrap on resize; `f` toggles following, `q` quits. Process 1: the Table takes the full width. The old agent cards, calls and workspace side panels are removed.
- Status bar: round N/M, phase (think / react / poll / post-run), per agent short name, ★ reputation and status (idle, thinking…, speaking…, polling…, failed: reason), control marked `ctl`, the most recent call's tally.
- `w` opens a pop-up with workspace files and the post-run grid. When the post-run starts, the grid replaces the Private messages pane.
- No new dependencies. Run with uv: `uv run pytest -q`.

## Rulings in this plan

- **Process 1 post-run:** P1 has no right column; when its post-run starts, the right column appears holding only the grid (the spec says only that the grid replaces Private messages, which P1 lacks).
- **Process 1 step status** stays `thinking…` (P1 has no speaking order); P2 shows `thinking…` (think), `speaking…` (react) and `polling…` (poll).
- **Think and private lines** use the spec's sketch format: `💭 #39 r7 Name (a): …` and `🔒 #44 r7 Name (b) → Name (c): …` (the 4a format `… think: …` / `… private_message: …` is replaced; its test is updated).
- **Status bar short name** is the agent's profile name, cut to 12 characters with `…`; the footer line stays (`q quit · f follow: on · w workspace`, plus unreadable-line count).
- **"Most recent open call"** is the newest call for consensus (the poll treats the latest call as the open one).

## Review Focus

1. **Entry text that looks like Rich markup** (`[bold]`, `[/]`, `[red]x`) must show literally and never raise a `MarkupError`. Pinned in Task 2 (render) and Task 3 (app).
2. **More than five agents**: colours repeat in id order; a sixth agent gets pink again. Pinned in Task 2.
3. **An author or recipient with no colour** (an id not among the run's agents, e.g. a private message to an unknown id): rendered bold without colour, no `KeyError`. Pinned in Task 2.
4. **Ticks while the `w` pop-up is open** (new entries, post-run starting): the app must keep updating its panes and the pop-up without querying the wrong screen. Pinned in Task 3.
5. **A community store from before Process 2** (no `runs.process` column, read-only so never migrated): `open_watcher` treats the run as Process 1. Pinned in Task 1.

---

### Task 1: State knows its process, phase and panes

**Files:**
- Modify: `src/aiswarm/watch/state.py` (`FeedLine`, `feed_line`, `feed_text`, `RunState.process`, `RunState.step_phase`, `apply`)
- Modify: `src/aiswarm/watch/source.py` (`open_watcher` passes the process)
- Test: `tests/test_watch_state.py`, `tests/test_watch_source.py`

**Interfaces:**
- Produces:
  - `FeedLine(entry_id: int, head: str, author: str, name: str, kind: str, target: str = "", body: str = "", to: str = "", to_name: str = "", stance: str = "", failure: str = "")` with properties `pane -> "table" | "thinking" | "private"` and `text -> str` (plain).
  - `feed_line(entry: Entry, who: Who) -> FeedLine`; `feed_text(entry, who) -> str` (= `feed_line(...).text`).
  - `RunState(run_id, goal, label, category, max_rounds, process=1, ...)`; `RunState.step_phase: str | None` ("think" | "react" | "poll" in Process 2, else None).

- [ ] **Step 1: Write the failing state tests**

In `tests/test_watch_state.py`, change the import to `from aiswarm.watch.state import FEED_MAX, RunState, feed_line, feed_text`, replace `test_feed_text_summarises_process2_entries` with the version below, and append the other tests:

```python
def test_feed_text_summarises_process2_entries():
    assert feed_text(entry(7, "think", {"text": "where I stand"}), who) == "💭 #7 r2 Ash (a): where I stand"
    assert feed_text(entry(8, "private_message", {"to": "b", "text": "psst"}), who) == "🔒 #8 r2 Ash (a) → Bea (b): psst"
    assert feed_text(entry(9, "abstain", {"target": 4, "reason": "unsure"}), who).endswith("abstain → #4: unsure")


def test_feed_lines_know_their_pane_and_parts():
    think = feed_line(entry(7, "think", {"text": "hm"}), who)
    pm = feed_line(entry(8, "private_message", {"to": "b", "text": "psst"}), who)
    vote = feed_line(entry(9, "vote", {"target": 4, "stance": "object", "reason": "no"}), who)
    failed = feed_line(entry(10, "pass", {"failure": "model_error"}), who)
    assert (think.pane, pm.pane, vote.pane, failed.pane) == ("thinking", "private", "table", "table")
    assert (pm.to, pm.to_name) == ("b", "Bea (b)")
    assert (vote.stance, vote.body, vote.target) == ("object", "no", " → #4")
    assert failed.failure == "model_error" and failed.text == "#10 r2 Ash (a) pass: (failed: model_error)"


def test_process_2_steps_set_the_phase_and_status():
    s = RunState("r1", "g", "r1", "ad hoc", 12, process=2)
    s.apply({"event": "run_started", "agents": ["a", "b"]})
    s.apply({"event": "agent_step_started", "round": 2, "agent": "a", "phase": "think"})
    assert s.step_phase == "think" and s.cards["a"].state == "thinking…"
    s.apply({"event": "agent_step_started", "round": 2, "agent": "b"})
    assert s.step_phase == "react" and s.cards["b"].state == "speaking…"
    s.apply({"event": "agent_step_started", "round": 2, "agent": "a", "phase": "poll"})
    assert s.step_phase == "poll" and s.cards["a"].state == "polling…"


def test_process_1_steps_keep_thinking_and_no_phase():
    s = state()
    s.apply({"event": "agent_step_started", "round": 1, "agent": "a"})
    assert s.cards["a"].state == "thinking…" and s.step_phase is None
```

- [ ] **Step 2: Write the failing source tests**

Append to `tests/test_watch_source.py`:

```python
def test_open_watcher_knows_the_process(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    add_run(conn, "r2", process=2)
    assert open_watcher(cfg, "r1").state.process == 1
    assert open_watcher(cfg, "r2").state.process == 2


def test_open_watcher_reads_a_store_from_before_process_2(tmp_path, community):
    cfg = cfg_for(tmp_path)
    store, conn = community
    add_run(conn, "r1")
    conn.execute("ALTER TABLE runs DROP COLUMN process")
    assert open_watcher(cfg, "r1").state.process == 1
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_watch_state.py tests/test_watch_source.py -q`
Expected: FAIL — `ImportError: cannot import name 'feed_line'` (collection error for the state file) and `AttributeError: 'RunState' object has no attribute 'process'`.

- [ ] **Step 4: Implement in `state.py`**

Replace `FeedLine` and `feed_text` with:

```python
PANES = {"think": "thinking", "private_message": "private"}  # every other entry goes to the Table


@dataclass(frozen=True)
class FeedLine:
    """One board entry, in parts, so render.py can colour each part without parsing text."""
    entry_id: int
    head: str          # "#12 r3"
    author: str        # agent id, or "system"
    name: str          # "Ash (a)"
    kind: str          # entry type
    target: str = ""   # " → #9"
    body: str = ""     # one line
    to: str = ""       # private messages: the recipient's id
    to_name: str = ""
    stance: str = ""   # votes
    failure: str = ""  # a pass that records a failure

    @property
    def pane(self) -> str:
        return PANES.get(self.kind, "table")

    @property
    def detail(self) -> str:
        if self.failure:
            return f"(failed: {self.failure})"
        if self.kind == "vote":
            return ": ".join(x for x in (self.stance, self.body) if x)
        return self.body

    @property
    def text(self) -> str:
        if self.kind == "think":
            return f"💭 {self.head} {self.name}: {self.body}"
        if self.kind == "private_message":
            return f"🔒 {self.head} {self.name} → {self.to_name}: {self.body}"
        head = f"{self.head} {self.name} {self.kind}{self.target}"
        return f"{head}: {self.detail}" if self.detail else head


def _one_line(text: str) -> str:
    return " ".join(text.split())


def feed_line(entry: Entry, who: Who) -> FeedLine:
    p = entry.payload
    target = f" → #{p['target']}" if p.get("target") is not None else ""
    body, stance, failure, to = "", "", "", ""
    if entry.type == "pass":
        failure = str(p.get("failure") or "")
    elif entry.type == "vote":
        stance, body = str(p.get("stance", "")), str(p.get("reason", ""))
    elif entry.type == "write_file":
        body = f"{p.get('path', '')} v{p.get('version', '')}: {p.get('note', '')}"
    elif entry.type in _SUMMARY:
        body = str(p.get(_SUMMARY[entry.type], ""))
    else:
        body = json.dumps(p, ensure_ascii=False)
    if entry.type == "private_message":
        to = str(p.get("to", ""))
    line = FeedLine(entry.id, f"#{entry.id} r{entry.round}", entry.author, who(entry.author), entry.type, target,
                    _one_line(body), to, who(to) if to else "", _one_line(stance), failure)
    excess = len(line.text) - FEED_MAX
    if excess > 0:  # only a runaway entry (e.g. raw JSON) is cut
        line = replace(line, body=line.body[: max(0, len(line.body) - excess - 1)] + "…")
    return line


def feed_text(entry: Entry, who: Who) -> str:
    return feed_line(entry, who).text
```

In `RunState`, add after `max_rounds: int`:

```python
    process: int = 1  # 2: think, then react in turn, with a poll
```

and after `bad_lines: int = 0`:

```python
    step_phase: str | None = None  # Process 2: "think" | "react" | "poll", from the latest step
```

In `apply`, replace the `agent_step_started` branch with:

```python
        elif name == "agent_step_started":
            if self.process == 2:
                phase = event.get("phase")
                self.step_phase = phase or "react"
                self.card(event["agent"]).state = {"think": "thinking…", "poll": "polling…"}.get(phase, "speaking…")
            else:
                self.card(event["agent"]).state = "thinking…"
```

In `refresh`, build lines with `feed_line` instead of `FeedLine(e.id, feed_text(e, who))`:

```python
        new = [feed_line(e, who) for e in entries if e.id > self.last_entry]
```

- [ ] **Step 5: Implement in `source.py`**

In `open_watcher`, pass the process (an older, never-migrated store has no column):

```python
    process = row["process"] if "process" in row.keys() else 1  # read-only: an older store is never migrated
    state = RunState(row["id"], row["goal"], row["goal_id"] or "ad hoc", row["goal_category"] or "ad hoc",
                     _max_rounds(conn, row["id"], cfg.run.max_rounds), process)
```

- [ ] **Step 6: Run the watch tests to verify they pass**

Run: `uv run pytest tests/test_watch_state.py tests/test_watch_source.py tests/test_watch_render.py tests/test_watch_app.py -q`
Expected: PASS (render and app still use `line.text`, which is unchanged for Process 1 entries).

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/watch/state.py src/aiswarm/watch/source.py tests/test_watch_state.py tests/test_watch_source.py
git commit -m "feat: watch state knows the run's process, the Process 2 phase, and each line's pane and parts"
```

---

### Task 2: Colour, status bar and pop-up text

**Files:**
- Modify: `src/aiswarm/watch/render.py` (add `AGENT_COLOURS`, `ACTION_STYLES`, `agent_colours`, `styled`, `phase_label`, `short`, `status`, `popup`; change `footer`; remove `header`, `cards`, `calls`, `CALLS_SHOWN`)
- Test: `tests/test_watch_render.py`

**Interfaces:**
- Consumes: Task 1 `FeedLine` (parts, `text`, `pane`), `RunState.process`, `RunState.step_phase`.
- Produces:
  - `agent_colours(agent_ids: Iterable[str]) -> dict[str, str]`
  - `styled(line: FeedLine, colours: Mapping[str, str]) -> Text` — `styled(line, c).plain == line.text` always.
  - `phase_label(s: RunState) -> str`; `short(name: str) -> str`
  - `status(s: RunState, colours: Mapping[str, str]) -> Text`
  - `popup(s: RunState) -> str`; `footer(s, following) -> str` (now ends with `· w workspace`)
  - Kept unchanged: `files`, `postrun`, `show_postrun`.

- [ ] **Step 1: Write the failing render tests**

Replace `tests/test_watch_render.py` with:

```python
from rich.text import Text

from aiswarm.board import Entry
from aiswarm.watch import render
from aiswarm.watch.state import AgentCard, CallLine, FeedLine, FileLine, PostRunRow, RunState, feed_line

COLOURS = render.agent_colours(["a", "b"])
PINK, TEAL = render.AGENT_COLOURS[0], render.AGENT_COLOURS[1]


def state(**kw):
    s = RunState("r1", "Pick a fruit.", "best-fruit", "contested", 12)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def who(agent_id):
    return {"a": "Ash (a)", "b": "Bea (b)", "system": "system"}.get(agent_id, f"{agent_id} ({agent_id})")


def line(type_, payload, author="a"):
    return feed_line(Entry(5, "r1", 2, author, type_, payload, [], "all", "t"), who)


def style_of(text: Text, fragment: str) -> str:
    start = text.plain.index(fragment)
    return " ".join(str(s.style) for s in text.spans if s.start <= start < s.end)


ALL = [line("propose", {"answer_summary": "Mango [bold]ripe[/] [red]x", "method": "m"}),
       line("critique", {"target": 3, "points": "too sweet"}, "b"),
       line("vote", {"target": 3, "stance": "object", "reason": "no"}),
       line("vote", {"target": 3, "stance": "support", "reason": ""}),
       line("pass", {"failure": "model_error"}), line("pass", {}),
       line("think", {"text": "[/] where I stand"}), line("private_message", {"to": "b", "text": "psst"}),
       line("private_message", {"to": "zz", "text": "to nobody"}),
       line("artifact_changed", {"path": "x"}, "system"), line("question", {"text": "why?"}, "q")]


def test_styled_text_reads_exactly_like_the_plain_line():
    for item in ALL:
        assert render.styled(item, COLOURS).plain == item.text


def test_brackets_in_entries_stay_literal():
    assert "Mango [bold]ripe[/] [red]x" in render.styled(ALL[0], COLOURS).plain


def test_action_words_and_agents_are_coloured():
    propose = render.styled(ALL[0], COLOURS)
    assert "green" in style_of(propose, "propose") and f"bold {PINK}" in style_of(propose, "Ash (a)")
    assert "dark_orange" in style_of(render.styled(ALL[1], COLOURS), "critique")
    assert "red" in style_of(render.styled(ALL[2], COLOURS), "object")
    assert "green" in style_of(render.styled(ALL[3], COLOURS), "support")
    assert "red" in style_of(render.styled(ALL[4], COLOURS), "(failed: model_error)")
    assert "dim" in style_of(render.styled(ALL[5], COLOURS), "pass")
    pm = render.styled(ALL[7], COLOURS)
    assert f"bold {TEAL}" in style_of(pm, "Bea (b)")
    assert style_of(render.styled(ALL[9], COLOURS), "artifact_changed") == "dim italic"


def test_unknown_agents_are_bold_without_a_colour():
    assert style_of(render.styled(ALL[8], COLOURS), "zz (zz)") == "bold"
    assert style_of(render.styled(ALL[10], COLOURS), "q (q)") == "bold"


def test_agent_colours_follow_id_order_and_repeat():
    colours = render.agent_colours(["f", "b", "a", "c", "d", "e"])
    assert [colours[x] for x in "abcde"] == list(render.AGENT_COLOURS)
    assert colours["f"] == render.AGENT_COLOURS[0]


def test_status_bar():
    s = state(process=2, phase="rounds", step_phase="react", round=7,
              cards={"a": AgentCard("a", "Ash", control=True, reputation=4.25, state="speaking…"),
                     "b": AgentCard("b", "Bartholomew-the-long", state="failed: model_error")},
              calls=[CallLine(40, "Bea (b)", 3, 1), CallLine(12, "Ash (a)", 1, 0)])
    bar = render.status(s, COLOURS)
    assert bar.plain == ("round 7/12 · react │ Ash ctl ★4.2 speaking… · Bartholomew… ★– failed: model_error"
                         " │ call #40: 3 for, 1 against")
    assert "bold" in style_of(bar, "speaking…") and "red" in style_of(bar, "failed: model_error")
    assert f"bold {PINK}" in style_of(bar, "Ash")
    assert render.status(state(), COLOURS).plain == "round 0/12 · waiting │ (no agents yet) │ no call yet"


def test_phase_label():
    assert render.phase_label(state(process=2, phase="rounds", step_phase="poll")) == "poll"
    assert render.phase_label(state(process=2, phase="post-run", step_phase="poll")) == "post-run"
    assert render.phase_label(state(phase="rounds")) == "rounds"


def test_popup_holds_the_workspace_and_post_run_grid():
    s = state(files=[FileLine("rules.md", 3, "c", 4)], postrun=[PostRunRow("journal", 5, 0, [])], postrun_seen=True)
    text = render.popup(s)
    assert "Workspace\nrules.md v3 (c, r4)" in text and "Post-run" in text and "journal" in text


def test_files_and_postrun_grid():
    assert render.files(state()) == "Workspace\n(no files yet)"
    rows = [PostRunRow("journal", 5, 0, []), PostRunRow("review", 3, 2, ["b", "d"])]
    text = render.postrun(state(postrun=rows, postrun_seen=True, current_step="review"))
    assert "done 5" in text and "failed 2 (b, d)" in text and "← running" in text
    assert render.postrun(state(closed_reason="budget")) == "Post-run\nno post-run"


def test_show_postrun_only_after_the_rounds():
    assert not render.show_postrun(state(phase="rounds"))
    assert render.show_postrun(state(closed_reason="budget"))


def test_footer():
    assert render.footer(state(), True) == "q quit · f follow: on · w workspace"
    assert render.footer(state(bad_lines=2), False) == \
        "q quit · f follow: off · w workspace · 2 unreadable event lines"


def test_feed_line_type_is_exported_for_the_app():
    assert FeedLine is type(ALL[0])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_watch_render.py -q`
Expected: FAIL — `AttributeError: module 'aiswarm.watch.render' has no attribute 'agent_colours'` at import time.

- [ ] **Step 3: Implement `render.py`**

Replace the module with:

```python
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
        return out.append(line.text, style="dim italic")
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


def status(s: RunState, colours: Mapping[str, str]) -> Text:
    out = Text(f"round {s.round}/{s.max_rounds} · {phase_label(s)} │ ")
    for i, card in enumerate(s.cards.values()):
        if i:
            out.append(" · ")
        out.append_text(_agent(short(card.name), card.agent_id, colours))
        if card.control:
            out.append(" ctl", style="dim")
        out.append(f" ★{'–' if card.reputation is None else f'{card.reputation:.1f}'} ")
        out.append(card.state, style="red" if card.state.startswith("failed") else STATUS_STYLES.get(card.state, ""))
    if not s.cards:
        out.append("(no agents yet)")
    out.append(" │ ")
    if s.calls:  # newest first; the poll treats the latest call as the open one
        c = s.calls[0]
        out.append(f"call #{c.entry_id}: {c.supporters} for, {c.objectors} against")
    else:
        out.append("no call yet")
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
```

- [ ] **Step 4: Run the render tests to verify they pass**

Run: `uv run pytest tests/test_watch_render.py -q`
Expected: PASS. (`tests/test_watch_app.py` now fails because `app.py` still calls `render.header`, `render.cards` and `render.calls`; Task 3 rewrites the app. Do not commit until Step 5 confirms only the app tests fail.)

- [ ] **Step 5: Confirm the only failures are the app's**

Run: `uv run pytest tests/test_watch_*.py -q`
Expected: only `tests/test_watch_app.py` tests fail, with `AttributeError: module 'aiswarm.watch.render' has no attribute 'header'`.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/watch/render.py tests/test_watch_render.py
git commit -m "feat: watch colours entries by action and agent, and builds the status bar and workspace pop-up"
```

---

### Task 3: Three panes, status bar and `w` pop-up

**Files:**
- Modify: `src/aiswarm/watch/app.py` (rewrite layout; `Feed` holds `Text`; `WorkspacePopup` modal)
- Test: `tests/test_watch_app.py` (rewrite)

**Interfaces:**
- Consumes: Task 1 `FeedLine.pane`, `RunState.process`; Task 2 `render.agent_colours`, `render.styled`, `render.status`, `render.popup`, `render.postrun`, `render.show_postrun`, `render.footer`.
- Produces: widget ids `#table`, `#right`, `#thinking`, `#private`, `#postrun`, `#status`, `#footer`; screen class `WorkspacePopup` with a `Static#popup-text`.

- [ ] **Step 1: Write the failing app tests**

Replace `tests/test_watch_app.py` with:

```python
import json

from observer_helpers import add_entry, add_run
from rich.text import Text
from textual.widgets import RichLog, Static

from aiswarm.config import Config
from aiswarm.watch.app import WatchApp, WorkspacePopup
from aiswarm.watch.source import open_watcher


def setup(tmp_path, community, process=1):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running", goal_id="best-fruit", category="contested",
            process=process)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango [ripe]", "method": "m"})
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    return conn, events, open_watcher(cfg, "r1")


def text(app, widget_id):
    content = app.query_one(f"#{widget_id}", Static).content
    return content.plain if isinstance(content, Text) else str(content)


def pane(app, widget_id):
    """A pane's visible text, with wrapped lines joined back up."""
    return " ".join(strip.text.strip() for strip in app.query_one(f"#{widget_id}", RichLog).lines)


def shown(app, widget_id):
    return app.query_one(f"#{widget_id}").display


def append(events, *items):
    with events.open("a") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


async def test_process_1_run_has_a_full_width_table_and_a_status_bar(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#table", RichLog).border_title == "Table — Pick a fruit."
        assert not shown(app, "right")
        assert app.query_one("#table").region.width > 130
        assert text(app, "status").startswith("round 1/12 · rounds │ Alpha ★– idle · Bea ★– idle")
        assert "Mango [ripe]" in pane(app, "table")
        add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "Too sweet."})
        app.tick()
        await pilot.pause()
        assert "Too sweet." in pane(app, "table")


async def test_process_2_routes_entries_to_three_panes(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt [it]."}, visible_to=["a"])
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "psst"}, visible_to=["a", "b"], to="b")
    append(events, {"event": "agent_step_started", "round": 1, "agent": "b"})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert shown(app, "right") and shown(app, "thinking") and shown(app, "private")
        assert not shown(app, "postrun")
        assert 60 <= app.query_one("#table").region.width <= 80
        assert "💭" in pane(app, "thinking") and "I doubt [it]." in pane(app, "thinking")
        assert "🔒" in pane(app, "private") and "→ Bea (b): psst" in pane(app, "private")
        assert "Mango [ripe]" in pane(app, "table") and "I doubt" not in pane(app, "table")
        assert "psst" not in pane(app, "table")
        assert text(app, "status").startswith("round 1/12 · react │") and "speaking…" in text(app, "status")


async def test_panes_wrap_and_rewrap_on_resize(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    points = " ".join(f"word{n}" for n in range(30))
    add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": points})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        log = app.query_one("#table", RichLog)
        width = log.scrollable_content_region.width
        assert all(strip.cell_length <= width for strip in log.lines)
        assert "word0" in pane(app, "table") and "word29" in pane(app, "table")
        narrow = len(log.lines)
        await pilot.resize_terminal(160, 30)
        await pilot.pause()
        assert len(log.lines) < narrow and "word29" in pane(app, "table")


async def test_follow_toggle_and_quit(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f")
        assert "follow: off" in text(app, "footer") and "w workspace" in text(app, "footer")
        await pilot.press("f")
        assert "follow: on" in text(app, "footer")
        await pilot.press("q")
    assert app.return_code == 0 or app.return_code is None


async def test_post_run_grid_replaces_private_messages(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert shown(app, "postrun") and shown(app, "thinking") and not shown(app, "private")
        assert "post-run" in text(app, "status")


async def test_process_1_post_run_opens_a_right_column_with_the_grid(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert shown(app, "right") and shown(app, "postrun")
        assert not shown(app, "thinking") and not shown(app, "private")


async def test_w_pop_up_shows_workspace_and_grid_and_keeps_updating(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    add_entry(conn, "r1", "a", "write_file", {"path": "rules.md", "version": 1, "note": "n"})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, WorkspacePopup)
        popup = lambda: str(app.screen.query_one("#popup-text", Static).content)  # noqa: E731
        assert "rules.md v1" in popup() and "Post-run" in popup()
        add_entry(conn, "r1", "b", "write_file", {"path": "notes.md", "version": 1, "note": "n"})
        add_entry(conn, "r1", "a", "think", {"text": "while the pop-up is open"}, visible_to=["a"])
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert "notes.md v1" in popup()
        await pilot.press("w")
        await pilot.pause()
        assert not isinstance(app.screen, WorkspacePopup)
        assert "while the pop-up is open" in pane(app, "thinking") and shown(app, "postrun")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_watch_app.py -q`
Expected: FAIL — `ImportError: cannot import name 'WorkspacePopup'`.

- [ ] **Step 3: Implement `app.py`**

Replace the module with:

```python
"""The Textual view. Thin: every tick it polls the Watcher and renders RunState through render.py."""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import RichLog, Static

from aiswarm.watch import render
from aiswarm.watch.source import Watcher

PANES = ("table", "thinking", "private")


class Feed(RichLog):
    """A wrapping pane. RichLog wraps at the width a line was written at, so it keeps the lines and rewraps."""

    def __init__(self, title: str, **kwargs):
        super().__init__(wrap=True, min_width=20, **kwargs)  # lines are Rich Text: nothing is parsed as markup
        self.border_title = title
        self.items: list[Text] = []
        self.wrapped_at = 0

    def add(self, item: Text, scroll_end: bool) -> None:
        self.items.append(item)
        self.write(item, scroll_end=scroll_end)

    def on_resize(self, event: Resize) -> None:
        width = self.scrollable_content_region.width
        if self._size_known and width != self.wrapped_at and self.items:
            at_end = self.is_vertical_scroll_end
            self.clear()
            for item in self.items:
                self.write(item, scroll_end=at_end)
        self.wrapped_at = width
        super().on_resize(event)


class WorkspacePopup(ModalScreen):
    """Workspace files and the post-run grid. The app refreshes it every tick while it is open."""

    DEFAULT_CSS = """
    WorkspacePopup { align: center middle; }
    #popup { width: 80%; height: auto; max-height: 80%; border: round $primary; background: $panel; padding: 0 1; }
    """
    BINDINGS = [("w", "close", "Close"), ("escape", "close", "Close")]

    def __init__(self, content: str):
        super().__init__()
        self.initial = content

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="popup"):
            yield Static(self.initial, id="popup-text", markup=False)

    def show(self, content: str) -> None:
        self.query_one("#popup-text", Static).update(content)

    def action_close(self) -> None:
        self.dismiss()


class WatchApp(App):
    CSS = """
    #body { height: 1fr; }
    #table { width: 1fr; border: round $primary; }
    #right { width: 1fr; }
    #thinking, #private { height: 1fr; border: round $primary; }
    #postrun { height: 1fr; border: round $primary; padding: 0 1; }
    #status { height: auto; padding: 0 1; background: $boost; }
    #footer { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("f", "toggle_follow", "Follow"), ("w", "workspace", "Workspace")]

    def __init__(self, watcher: Watcher, interval: float = 1.0):
        super().__init__()
        self.watcher = watcher
        self.interval = interval
        self.following = True
        self.written = 0
        self.timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Feed(f"Table — {self.watcher.state.goal}", id="table")
            with Vertical(id="right"):
                yield Feed("Thinking", id="thinking")
                yield Feed("Private messages", id="private")
                yield Static(id="postrun", markup=False)
        yield Static(id="status")
        yield Static(id="footer", markup=False)

    def on_mount(self) -> None:
        # Held, not queried each tick: while the pop-up is open, queries would search the pop-up's screen.
        self.feeds = {name: self.query_one(f"#{name}", Feed) for name in PANES}
        self.right = self.query_one("#right", Vertical)
        self.grid = self.query_one("#postrun", Static)
        self.grid.border_title = "Post-run"
        self.status_bar = self.query_one("#status", Static)
        self.footer_line = self.query_one("#footer", Static)
        self.tick()
        self.timer = self.set_interval(self.interval, self.tick)

    def tick(self) -> None:
        self.watcher.poll()
        s = self.watcher.state
        colours = render.agent_colours(s.cards)
        new = s.feed[self.written:]
        if new:
            # scrolled up: don't drag the reader down
            ends = {name: self.written == 0 or feed.is_vertical_scroll_end for name, feed in self.feeds.items()}
            for line in new:
                self.feeds[line.pane].add(render.styled(line, colours), scroll_end=self.following and ends[line.pane])
            self.written = len(s.feed)
        post = render.show_postrun(s)
        two = s.process == 2
        self.right.display = two or post
        self.feeds["thinking"].display = two
        self.feeds["private"].display = two and not post
        self.grid.display = post
        self.grid.update(render.postrun(s))
        self.status_bar.update(render.status(s, colours))
        self._footer()
        if isinstance(self.screen, WorkspacePopup):
            self.screen.show(render.popup(s))
        if s.finished and self.timer is not None:
            self.timer.stop()

    def _footer(self) -> None:
        self.footer_line.update(render.footer(self.watcher.state, self.following))

    def action_toggle_follow(self) -> None:
        self.following = not self.following
        if self.following:
            for feed in self.feeds.values():
                feed.scroll_end(animate=False)
        self._footer()

    def action_workspace(self) -> None:
        self.push_screen(WorkspacePopup(render.popup(self.watcher.state)))
```

- [ ] **Step 4: Run the app tests to verify they pass**

Run: `uv run pytest tests/test_watch_app.py -q`
Expected: PASS (7 tests). If `test_process_2_routes_entries_to_three_panes`'s width bounds fail by a cell or two because of borders, keep the bounds as written and check the CSS first: `#table` and `#right` must both be `width: 1fr`.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (499 before this plan, plus the new tests, minus the replaced ones).

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/watch/app.py tests/test_watch_app.py
git commit -m "feat: aiswarm watch shows Table, Thinking and Private messages panes over a status bar, with a w pop-up"
```
