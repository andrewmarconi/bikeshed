# Minor fixes deferred from generation 4's sub-projects

*16 October 2026. Each sub-project ended with a whole-branch review. Findings graded Critical or Important were fixed before merge; the minor ones were deferred and are collected here so they outlive the session that found them. Nothing here changes what agents see or do, unless marked. Each item says where it was found; check it's still open before fixing, since later work may have touched the same code.*

Specs: B is `2026-10-15-bikeshed-gen4-agent-environment-design.md`, C1 is `…-gen4-measurement-corrections-design.md`, C2 is `…-gen4-new-measurements-design.md`, D is `…-gen4-baseline-run-design.md`, D2 is `…-gen4-baseline-variant-design.md` (all in `docs/superpowers/specs/`).

## Post-run (sub-project B)

- [ ] **Docstring.** `postrun/feedback.py:1` says "who interacted"; since B the rule is "who took part".
- [ ] **Abstaining counts as taking part.** A member whose only public act is abstaining in a Process 2 poll (public, not a pass, with a reason) counts as a participant and is reviewed. That's consistent with the rule, but the spec should say so outright.
- [ ] **Test gap.** No test covers participation through a Process 2 pre-action `write_file`; only a bare `files` row is tested.
- [ ] **Review cost.** Reviews grow as N² per run (36 with six agents), so post-runs take longer on oMLX. No fix decided; note it if the member count grows.

## Observer measurements (sub-project C1)

- [ ] **Test gap.** No test of an old run that names an agent missing from the store (the `observe` controls guard, and `before_moment` with an empty git log).
- [ ] **`private_public` with no "before" journal** sends the judge the text "(none)". It could return `None` and skip the judgment instead.
- [ ] **Self-image seed note** uses the agent's current control flag, not whether `before_moment` actually chose a reset for that run.

## Observer measurements (sub-project C2)

- [ ] **Copying table length.** The per-run Copying table can run to well over a thousand rows. Group it by copier entry or by earliest source.
- [ ] **Truncated `events.jsonl`.** One cut-off line makes `read_events` raise, so `observe` fails on that run's library reuse. Skip or report the bad line instead.
- [ ] **Amendments against their own norm.** A proposal that amends a norm is listed as overlapping that norm, which the 25% floor makes more likely. Skip or tag the case where `n.id == p.target`.
- [ ] **Credit-gap heading.** The credit-gap lines sit under "Credit reciprocity" with no heading of their own.

## Baseline (sub-project D)

- [ ] **One-way exclusion.** `baseline` refuses while a community run is live, but a community `run` doesn't check `lab/baselines/`, so "never compete for oMLX" holds only one way.
- [ ] **Concurrent baselines.** Two `baseline` invocations at once can both pick `-b1` (an uncaught `ProfileError`). If `prepare` dies before writing `baseline.toml`, it leaves a stray directory.
- [ ] **Crash between start and record.** A crash between `runner.start` and `record_run` would make `--resume` start a second run.
- [ ] **Late Process 3 refusal.** A Process 3 refusal in `runner.start` comes after introductions, leaving a directory whose `--resume` fails the same way. Check before introducing.
- [ ] **Malformed `baseline.toml`** stops `observe` (`tomllib` raises). Scratch connections are also never closed.
- [ ] **Baseline vs community rows** use every control and twin in the community, not the source run's agents. A source run that hasn't been observed yet is skipped without a word.
- [ ] **`goals.toml`.** The spec says the scratch community gets a `goals.toml`; `prepare` writes none. Fix the code or the spec.

## Model-variant baseline (sub-project D2)

- [ ] **`--resume` help.** `bikeshed baseline --resume` help reads "its directory name, e.g. RUN-b1"; add a variant example (`RUN-v1`).
- [ ] **Test gap.** No CLI test resumes a variant after a source backend has left the config.

## Not decided

- [ ] **Introduction length caps.** `qwen3-14b-fast` overruns introduction length limits, and the correction doesn't reliably bring it back under; two baseline attempts failed on it. Options: a third attempt, or `maxLength` in the schema. *Changes what agents experience*, so it can't land mid-generation without being recorded as a harness change.
