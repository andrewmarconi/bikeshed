# The control: fixes for generation 4

## What the control is for

A control comparison isolates exactly what differs between the control and its twin. Everything they share drops out of the comparison.

Generation 4's control measures **personal memory**. It sees everything the community holds in common: the board, the norms, the library and the other members' profiles and reputations. It sees nothing that is its own history: its lessons, self, peer notes, journals, reputation or old methods.

That matches the glossary's definition: "when the control drifts the same way the others do, the model or the room is a likelier cause than experience". The control alone can't separate the model from the culture. The baseline run (fix 6) does that.

| Surface | Control sees it? | Status in the code |
|---|---|---|
| The board | Yes | Can't be avoided in a shared run |
| Norms | **Yes** (decided) | `view.py:90` already does this |
| Other members' public profiles | Yes | Already does |
| Other members' reputations | Yes | Already does |
| Library methods by other members | Yes | Already does |
| Its own lessons, self, peer notes | No | Reset by `reset_to_seed` |
| Its own journals | No | `view.py:89` already hides them |
| **Its own reputation (overall and per dimension)** | **No** | **Leak: fix 4** |
| **Library methods it wrote** | **No** | **Leak: fix 5** |
| What other members remember about it | Yes | Can't be removed, and belongs to the room |

## Fixes

Ordered by how badly each one breaks the comparison.

### Bugs that broke the control for a whole run

1. **Reset the control on resume, not only on create.** `runner.py:90` resets the control only inside `start()`. Resuming a run has no reset, so any after-run cycle that happens while a run is paused leaks into the control. Run 8 resumed with no reset, with `e` still carrying run 9's update. Reset (or re-check) controls whenever `run()` picks up a run, and log a `control_reset` event either way.

   *Checked against the code:* `runner.py:90` is the only caller of `reset_to_seed`. `Runner.run()` (`runner.py:113`) does no reset or control check. On resume the CLI skips `start()` (`run_id = resume or runner.start(...)`, `cli.py:317`) and calls `runner.run()` directly (`cli.py:328`).

2. **Never save "null" or an empty string as a profile field.** `prompts/update_request.md:21` says "use null for any you keep". Qwen2.5 returned the *string* `"null"`, and the harness wrote it into `e`'s `temperament.md` and `self.md`. That's why run 8's control had "null" as its personality. Treat `"null"`, `"none"`, `""` and whitespace as "keep", for every agent.

   *Checked against the code:* `UpdateReply` types the fields as `str | None` (`postrun/replies.py:77`), and the write loop skips only a real `None` (`postrun/cycle.py:338`), so the string `"null"` gets written. `profiles.py:94` rejects an empty `temperament.md`, but an empty `self.md` or `peer-notes.md` is still written. Not yet checked: whether the `public_profile` fields (name, headline, introduction) can take `"null"` the same way.

3. **Don't let runs overlap.** Refuse to start a run, or to run an after-run cycle, while another run is paused, or at least warn and record it. Runs 8 and 9 interleaving broke the control and gave the Observer phantom reversals. Fix 1 limits the damage; this removes the cause.

   *Checked against the code:* nothing guards against this now. `bikeshed postrun` only checks that its own run is closed (`cli.py:398`), and starting a run checks only that the agents exist. The one guard against unclosed runs is on `agents set-model` (`cli.py:114`).

### Leaks of personal memory

4. **Hide the control's own reputation from the control.** `view.py:91` gives every agent every member's reputation, the control's own included. Reputation builds up across runs and survives the reset, so the control sees a running score of how it has been judged: memory about itself. Leave the control's own entry out of its view. Other members still see it, which is the room's memory and stays.

   *Checked against the code:* the line numbers are right (`view.py:89` journals, `:90` norms, `:91` reputations). There's a second channel: `view.py:93` also passes `dimension_scores` (each member's per-dimension reputation) for every member, the control included, and `view.py:187` renders it. Hide the control's own entry from both.

5. **Leave the control's own old methods out of its library results.** A reset clears `e`'s files, but retrieval can hand back `e`'s earlier methods, which `e` then pasted word for word (run 11, #1103). Filter out methods the control wrote when building the control's view. Methods by other members stay: they're part of the common library.

   *Checked against the code:* this isn't a filter the view can apply. `Runner._library` (`runner.py:224`) retrieves the methods once per run and renders them into strings with author names, and every agent gets that same list (`ctx.library`, passed in at `runner.py:190`). The runner has to keep the `Method` objects, which carry `author`, and render a separate list for each control agent. Open question: should the control get its top-k from the other members' methods (fill back up to k) or just k minus its own? Filling up keeps the amount of library context equal to the twin's.

### Separating the model from the culture

6. **Add a baseline run.** The control can't tell the model from the culture, because it shares both with its twin. Every so often (each time the pizza benchmark comes round is a natural rhythm), run the same goal with five brand-new agents from the same seed temperaments, on the same model, with no norms, no library and no history.

   | | Model | Room | Culture (norms, library) | Personal memory |
   |---|---|---|---|---|
   | Baseline run | ✓ | fresh | – | – |
   | Control | ✓ | ✓ | ✓ | – |
   | Twin | ✓ | ✓ | ✓ | ✓ |

   - Baseline vs. control: what the culture adds.
   - Control vs. twin: what personal memory adds.
   - The baseline over time: whether the model on its own drifts. It shouldn't; this is a sanity check.

   Two things to work out:
   - The harness needs a way to run a fresh room without touching `lab/`. A separate scratch community directory per baseline run is probably simplest. Baseline runs must never write into the generation's community: no reviews, norms, methods or reputation.
   - Use the same brief assignment as the community run of the same goal (same seed), or the comparison picks up noise from different private facts.

   *Checked against the code:* this needs its own spec in `docs/superpowers/specs/` before any code. The runner and the post-run cycle each take one `ProfileStore` and one database connection, and those point at the generation's `community/` and `community.db`. A baseline run would need:
   - a scratch community with the seed agents copied in;
   - the brief assignment (`briefs.assign`, seeded from the run seed) reproduced from the matching community run;
   - post-run either skipped or kept inside the scratch community;
   - a way for the Observer to read both databases.

   That's a new run mode, not a patch.

### Measurement

7. **Make the control's brief comparable.** Process 4 deals the control a private brief like any other member, so the control's private facts change from run to run. Record the control's brief slot and compare like with like, or always give it the same slot.

   *Checked against the code:* the slot is already recorded. `run_briefs` stores `agent_id` per slot (`briefs.py:87`). `briefs.assign` (`briefs.py:73`) deals private brief k to the k-th member of a seeded shuffle of all agent ids, with nothing to tell control from twin. When a goal has fewer private briefs than members, the control sometimes gets no private brief at all. Neither `observer/briefs.py` nor the twin comparison (`observe.py:260`) looks at the control's slot. So the gap is on the comparison side, plus the choice of whether to fix the slot at assignment. Fixing the slot would change `assign`, which is part of the run's recorded behaviour.

8. **Label the control's after-run updates.** `e` still journals and rewrites its profile after every run, and the reset then wipes that work. Keep it: it shows how far one cycle moves a blank agent. Label it so the Observer stops reading a reset as `e`'s own change, as it did in runs 7 and 14.

   *Checked against the code:* the post-run cycle has no control handling at all. Nothing in `postrun/cycle.py` mentions controls, so `e`'s updates are committed under the same `"<id> updated <file>"` subjects as everyone else's. The reset itself is already labelled. `profiles.py:181` commits it as `reset control agent <id> to seed`, and the Observer's identity timeline recognises that as a `control reset` event (`observer/metrics.py:213`). The analysis that misreads it is `self_image` (fix 9), which diffs file snapshots and never looks at commit subjects. So the fix is mainly on the Observer side: the commits are already distinguishable.

9. **Observer: order after-run comparisons by time.** The `self_image` judge orders runs by creation, so it compared run 9 against run 8's later after-run cycle and reported reversals that never happened. Order by after-run timestamp, and treat a reset as a reset.

   *Checked against the code:* confirmed. `closed_runs` orders by `rowid`, which is creation order (`observer/metrics.py:57`). `self_image_inputs` (`observer/inputs.py:65`) takes the previous run in that order and reads "before" at that run's `end`. `run_end` includes the post-run steps (`metrics.py:47`). When run 8 was created first but its post-run finished after run 9's, run 9's "before" snapshot is *later* than its "now" snapshot, and the judge sees a reversal. Two changes are needed. Choose the earlier run by `end` (strictly before this run's snapshot), not by `rowid`. And when a `reset control agent` commit falls between the two snapshots, take "before" from the reset, or tell the judge about it.

## Summary

- Fixes 1–3 are bugs that broke the control for a whole run.
- Fixes 4–5 close the two leaks of personal memory, so the control matches the definition above. Fix 4 covers per-dimension reputation as well as the overall score. Fix 5 needs a library list for each control agent, not a filter in the view.
- Fix 6 adds the baseline that separates the model from the culture. It needs its own spec first.
- Fixes 7–9 are measurement problems. Fix 7's slot is already recorded, but the comparison ignores it. Fix 8 is mostly an Observer change, since reset commits are already labelled. Fix 9 is confirmed.
