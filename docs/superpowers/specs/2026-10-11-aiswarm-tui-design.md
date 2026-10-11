# AISwarm — TUI Design (Plan 3c)

- **Date:** 2026-10-11
- **Status:** Approved; Plan 3c implemented
- **Author:** Andrew Marconi (with Claude)
- **Extends:** `2026-10-10-aiswarm-design.md` §8 (TUI) and the Plan 3a/3b addenda. Where this document is more specific, it governs; the principles still apply, above all **observation never feeds back**.

## 1. Purpose and scope

A live, read-only terminal view of a run, for the user only. It shows the board as it grows, what each agent is doing, open calls for consensus, workspace changes, and — after the rounds — the post-run cycle step by step. It can also open a finished run.

Out of scope: replay with a round slider, an entry detail view, reasoning traces, any interaction that changes the community.

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Attachment | A separate command, `aiswarm watch [RUN_ID]`, run in a second terminal. It never runs inside `aiswarm run`, so a TUI fault cannot affect a run; `aiswarm run` keeps its plain output. This replaces the base spec's "`aiswarm run` opens a Textual view". |
| Scope | Live view of the rounds, plus the post-run cycle, plus opening past runs. |
| Model | A pure `RunState` built from two read-only sources — the run's `events.jsonl` (live status) and the community database (content) — polled about once a second. Textual widgets only render it. |
| User-only detail | Cards show the control marker and failures. Reasoning traces are not shown. |
| Library | Textual 8.2.8 (latest stable, released 2026-06-30). |

## 3. Command

`aiswarm watch [RUN_ID] [-c CONFIG]`

- No `RUN_ID`: the most recent run (by rowid).
- No runs, or an unknown id: a clean CLI error before the UI opens.

Keys: `q` quit; `f` toggle following (auto-scroll to new entries). Scrolling the feed up pauses following.

## 4. Layout

```
┌ <goal id or "ad hoc"> (<category>) · run <id> · round <n>/<max> · <phase> ─────────┐
│ Board (one line per entry, newest at the bottom)    │ Agents                        │
│ #301 r4 Shogun (a) critique → #295: "The method…"   │ Shogun (a)  ★4.0  thinking…   │
│ #302 r4 Maya (e) adopt → #298: "Agreed, …"          │   last: critique #301         │
│ #303 r4 Sam (b) pass (failed: invalid_action)       │ Maya (e) [control] ★5.0 idle  │
│                                                     ├───────────────────────────────┤
│                                                     │ Calls for consensus           │
│                                                     │ #299 by Sam (b): 3 for, 0 against│
│                                                     ├───────────────────────────────┤
│                                                     │ Workspace: rules.md v3 (c, r4)│
└─────────────────────────────────────────────────────┴───────────────────────────────┘
 q quit · f follow on/off · <n unreadable event lines, if any>
```

- Feed lines: `#<id> r<round> <Name (id)> <type>[ → #<target>]: <summary>`, truncated to about 200 characters. The summary is the type's main text field (answer summary, points, reason, change, synthesis, note), or the failure for a failed pass.
- Agent cards: display name, `[control]` if control, ★ reputation (the existing `reputation()` overall, "–" with no reviews), state (`thinking…` between `agent_step_started` and `agent_step_finished`, otherwise `idle`; `failed: <kind>` when the last step failed), and the last action with its entry id.
- Calls: each `call_consensus` of the run with supporters and objectors (from `call_status`), newest first.
- Workspace: latest version of each file in the run (`files` table), with author and round.
- After `run_closed`, the calls and workspace panels give way to the **post-run grid**: one row per step in `STEPS` (journal, review, responses, update, norms_critique, norms_vote, library, narrative) with done and failed counts, the agents whose step failed, and a marker on the step currently running (expected call counts are not recorded, so pending cannot be shown). The workspace panel is built from the run's `write_file` entries, since the `files` table has no round. A run with no post-run shows "no post-run".

Phases shown in the header: `waiting` (no events yet), `rounds`, `paused — resume with aiswarm run --resume <id>`, `closed: <reason>`, `post-run`, `post-run paused — resume with aiswarm postrun <id>`, `done`.

## 5. Data flow

Package `src/aiswarm/watch/`:

| Module | Responsibility |
|---|---|
| `source.py` | `EventTail`: reads new complete lines from `events.jsonl` by byte offset; a partial last line waits; unparseable lines are skipped and counted. Database access via `connect_readonly` and `ProfileStore(create=False)`. |
| `state.py` | Pure dataclasses: `RunState`, `AgentCard`, `FeedLine`, `CallLine`, `FileLine`, `PostRunRow`. `RunState.apply(event)` folds one event; `RunState.refresh(conn, store)` pulls new entries (id > last seen), calls with votes, file versions, and — only when the phase changes or a post-run step finishes — names, headlines and reputation. |
| `app.py` | The Textual `App`: header, feed, cards, calls, workspace, post-run grid; `set_interval(1.0)` tick calling the source and state; key bindings. |

On each tick: tail events → `apply` each → `refresh` from the database → re-render. A finished run (last post-run event is `postrun_finished`, or the run is closed with no post-run activity) is loaded once and polling stops.

## 6. Errors

| Situation | Behaviour |
|---|---|
| Events file missing | Phase `waiting`; picked up when it appears |
| Database locked or refresh error | Keep the previous state; retry next tick; never crash |
| Unparseable event line | Skipped; footer shows the count |
| Run paused | Phase text names the resume command |

## 7. Isolation

The watcher writes nothing: read-only database connection, `ProfileStore(create=False)`, no files created. A test asserts no agent-path module imports `aiswarm.watch`, and that running the watcher leaves the community database bytes and git HEAD unchanged.

## 8. Testing

TDD with pytest; no model calls.

- **State:** round counter and phases; thinking / idle / failed cards and last action; calls with votes; file versions; post-run grid counts and failed agents; past-run replay; a run with no post-run; reputation refreshed only on phase change or post-run step.
- **Tail:** only new bytes read; a half-written line waits; broken lines skipped and counted; missing file → waiting.
- **Resilience:** a refresh that raises leaves the previous state.
- **App:** Textual `App.run_test()` with `Pilot` — renders header, feed, cards and calls for a fixture run; appended events and entries appear; `f` toggles following; `q` quits; after `run_closed` the post-run grid replaces the calls panel.
- **CLI:** no runs / unknown id → clean error; no id → latest run.
- **Isolation:** import check; community database bytes and git HEAD unchanged.

## 9. Dependencies

`textual` (8.2.8 or later; verify the latest stable release when the plan is written).
