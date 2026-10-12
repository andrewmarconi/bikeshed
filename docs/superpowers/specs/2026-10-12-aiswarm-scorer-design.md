# AISwarm — Scorer Design (outside acceptance of a consensus)

- **Date:** 2026-10-12
- **Status:** Approved in brainstorming; spec awaiting review
- **Author:** Andrew Marconi (with Claude)
- **Motivation:** Run 20261012-141218-5a24 (Process 2, qwen4b, pizza benchmark) closed on a valid consensus whose synthesis was a plan to run a poll, not a topping combination. The community rule (critiqued once, all but at most one support) cannot tell an answer from a deferral, and should not: the harness never judges content.
- **Extends:** `2026-10-10-aiswarm-design.md` (§2 principles), `2026-10-11-aiswarm-process-2-design.md`, the Plan 3b Observer and Plan 3c/4c TUI specs. Where this document is more specific, it governs.

## 1. Purpose and scope

The community's goal becomes: get its consensus accepted by an outside **Scorer**. When a call for consensus passes the community's own rule, it is submitted to the Scorer, which rules only on whether the synthesis gives the concrete answer the goal asks for. Accepted: the run ends and every member the caller credited earns a Scorer point. Rejected: short feedback is posted and work continues.

The agents know the Scorer exists and what it checks. It never posts during rounds, never reads the board, and speaks only through its rulings.

Out of scope: appeals; the Scorer judging how good an answer is; points decaying, being spent or traded; a Scorer that reads the board or the votes; a per-run switch to turn the Scorer off.

## 2. Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Name | **Scorer** (the Observer is the user-only analysis and stays invisible) |
| What the Scorer rules on | Only whether the synthesis gives the concrete answer the goal asks for. Not quality, not reasoning, not values |
| When it rules | Only after a call passes the community's existing rule |
| Feedback | Only on rejection: one or two sentences on what is missing for the synthesis to answer the goal |
| Reward | Individual: everyone the accepted call's caller credited earns 1 Scorer point, the caller included if it credits itself |
| Applies to | Every run, both processes. No switch; earlier runs are history behind a harness-change marker |
| TUI status bar | Two lines: run line (round, phase, call tally, latest ruling) and agents line (`★ 4.2 ◆ 2` with a space after each symbol) |

**Principles (`2026-10-10-aiswarm-design.md` §2):** "observation never feeds back" is unchanged and still applies to the Observer. The Scorer is a declared part of the agents' environment, like the workspace and the consensus rule: its existence, its criterion and its rulings are facts the agents are told. It is not an orchestrator: it assigns no work, picks no speakers and acts only on a submitted call. Its criterion is values-neutral by construction (does the text answer the question asked), so the community's values remain its own.

## 3. Flow

1. A call passes the community rule (existing `stop_decision`), skipping spent calls (§3.3).
2. The runner submits it to the Scorer: input is the goal and the call's synthesis, nothing else.
3. The Scorer replies `{"accepted": true | false, "feedback": "..."}`; `feedback` is required (non-empty) when `accepted` is false.
4. **Accepted:** the run closes with reason `consensus` and the call id, as today; one `scorer_points` row per credited agent (§4); a public system entry `scorer_accepted` → call.
5. **Rejected:** a public system entry `scorer_rejected` (payload: call id, feedback) → call; the harness writes the feedback to `scorer_feedback.md` in the workspace as a new version authored by `system`; the call is spent; the next round starts. If the round budget is exhausted, the run closes with reason `budget`.

### 3.1 What agents are told

One paragraph added to both system prompts (`agent_system.md`, `process2_agent_system.md`):

> An outside Scorer reviews every call for consensus that passes. It accepts a call only if its synthesis gives the concrete answer the goal asks for; it does not judge whether the answer is good. If it rejects a call, its feedback appears on the board and in `scorer_feedback.md`, and work continues. When a call is accepted, every member credited in it earns a Scorer point.

The end-of-work sentence ("The work ends when a call_consensus has been critiqued at least once and is supported by all members but at most one") gains "… and the Scorer accepts it".

### 3.2 The Scorer

- Prompt `prompts/scorer.md` (flat, so provenance records it): the goal, the synthesis, the criterion, the reply shape. It never sees names, credits, votes, the board or the workspace.
- Backend from a new `[scorer]` config section, `judge = "<backend name>"` (e.g. `claude-judge`). Provenance records the backend as part of the run's models.
- Validation: the reply must parse as the shape above; a rejection without feedback is invalid. One correction round, as the Observer's judge has.
- Failure (model error, or invalid twice): the run pauses (`run_paused` with detail `scorer: …`). Nothing is fabricated. `aiswarm run --resume` asks the Scorer again before any new round.
- Event `scorer_ruled` in events.jsonl: call id, accepted, feedback, reported model.

### 3.3 Spent calls

A call with a `scorer_rejected` entry is spent:
- `stop_decision` ignores it (it can never close the run).
- The Process 2 poll does not poll on it; if the latest call is spent, there is no poll.
- Votes on it are kept on the board but count for nothing.
- The view shows it with its rejection (the system entry follows it on the board).

## 4. Scorer points

- Table `scorer_points (run_id, agent_id, call_id, contribution, created_at)`: one row per point; totals are always counted from rows.
- Awarded only on acceptance, to each `agent_id` in the call's `credits` that is a member of the run (an unknown id earns nothing). The caller earns a point when it credits itself.
- Never decay, never reset (the control agent's reset leaves its points).
- **Agent view:** each member line gains its lifetime count, e.g. `- Bea (b) — headline · ★4.0 (3 reviews) · 2 Scorer points`. No ranking section.
- **Post-run journal step:** told the ruling(s) of this run (accepted, or rejected with feedback) and who earned points.

## 5. Process interactions

- Stagnation and budget rules unchanged; a rejection counts as activity, not stagnation.
- Resume: a run paused at the Scorer resumes by asking it again; a run paused mid-round resumes as today.
- Run report (`answer.md`): states the Scorer's ruling(s); an accepted synthesis is the answer.
- Process 1 golden recording changes where the Scorer step and prompts change it; it is regenerated deliberately.

## 6. TUI (`aiswarm watch`)

- Status bar becomes two lines:
  ```
  round 7/12 · react │ call #40: 3 for, 1 against │ Scorer: rejected #31
  Ash ctl ★ 4.2 ◆ 2 speaking…   ·   Bea ★ 3.9 ◆ 0 idle   ·   Cyd ★ – ◆ 1 thinking…
  ```
  Line 2 wraps rather than being cut. A space follows `★` and `◆`.
- `scorer_accepted` / `scorer_rejected` entries appear in the Table styled as system entries; rejections in red.

## 7. Observer

- Per run: the ruling(s), each rejection's feedback, points awarded (who, for what).
- Summary: trend chart of points per agent; accepted vs rejected rulings per run; how often callers credit themselves.
- The Observer never reads to or from the Scorer; it reports the Scorer's rulings like any other record.

## 8. Errors

- Scorer model error or invalid reply twice: pause, nothing fabricated.
- No `[scorer]` configured: `aiswarm run` refuses to start with a message naming the missing section.
- Credit naming a non-member: no point for it.

## 9. Testing

TDD with `FakeModel`; no assertions on real model content.

1. Accepted: run closes `consensus`; points rows for exactly the credited members, self-credit included; `scorer_accepted` entry.
2. Rejected: `scorer_rejected` entry and a new `scorer_feedback.md` version; the call is spent (later votes do not count; it cannot close the run); the next round runs; a later call can be accepted; a rejection in the last round closes on `budget`.
3. Scorer failure pauses; resume asks again before any round.
4. Process 2: no poll on a spent call.
5. View shows members' points; post-run journal request includes the ruling.
6. Scorer input contains only the goal and the synthesis.
7. TUI: two-line status bar, `★ 4.2 ◆ 2` spacing, rejection styling.
8. Observer: rulings, points, summary charts.
9. Config: run refuses without `[scorer]`; provenance records the Scorer prompt and backend.

## 10. Delivery

One spec, two plans: **5a** engine (Scorer, spent calls, points, prompts, view, post-run, config, report); **5b** TUI and Observer.

## 11. Dependencies

None new.
