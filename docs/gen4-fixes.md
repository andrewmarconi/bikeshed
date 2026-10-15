# Harness fixes for generation 4

Fixes 0–9 are about the control. Fixes 10–19 are the other gaps that generation 3 showed up.

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

## Goals and private briefs in generation 4

**What changes.** In generation 3 every queue goal was a bare question: no member knew anything the others didn't. Generation 4 adds Process 3 goal briefs (spec: `2026-10-14-bikeshed-goal-briefs-design.md`):

- A **shared brief** that every member sees.
- A **private brief** for each of some members, seen only by its holder.
- Some brief lines are **constraints** that the Scorer rules on. When a synthesis breaks one, the harness says whose brief held it, never what it said.

The goal moves from "agree on an opinion" to "pool what each of you knows". The starter set (`docs/goals/briefed-goals.toml`) has six goals:

| Goals | Category | Briefs |
|---|---|---|
| `dinner-listed`, `sunday-listed` | underspecified | shared brief only |
| `picnic-briefed`, `offsite-briefed` | distributed | shared + 5 private; the facts complement each other |
| `film-night`, `office-thermostat` | contested | shared + 5 private; the facts conflict, so someone's situation gives way |

The pizza benchmark stays unbriefed, so it stays comparable across generations.

**How briefs are dealt.** At run start, `briefs.assign` shuffles the members with the run seed. Private brief k goes to the k-th member and is recorded in `run_briefs`. If a goal has more briefs than members, the run won't start. If it has fewer, the members left over get none, and their view says "you received none".

**What six agents changes.** Every briefed goal in the starter set has five private briefs. With six members, one member in every briefed run holds nothing, and the plain shuffle picks who. That causes two problems:

- **The control comparison.** If `e` or `f` is the member without a brief, that run's control-vs-twin comparison measures "had a brief or not", not memory. With the plain shuffle that happens in about a third of briefed runs (2 of 6 seats).
- **Different facts.** Even when both hold a brief, they hold different ones. In a contested goal, one may hold the fact that gives way and the other not. This difference can't be removed in a shared run, so it has to be recorded and shown, not hidden.

**What generation 4 does** (fix 7, decided):

1. `assign` makes sure the control and its twin both hold a private brief whenever a goal has fewer briefs than members. The member without a brief comes from the other agents, still by the seeded shuffle.
2. The twin comparison shows which brief each held, and whether a call-out named either of them.
3. Option, not yet decided: add a sixth, text-only private brief to each briefed goal, so that no member goes without. Text only means a fact with no new constraint, so the Scorer's rules don't change. That makes step 1 a guard instead of the normal path, and removes the "you received none" member from every run. The cost is rewriting four goals: the four briefed goals in the starter set have not been run yet, so nothing earlier has to stay comparable with them.

## Fixes

Ordered by how badly each one breaks the comparison.

### Before anything else: the control needs a twin

0. **Give the control a twin in the roster.** Every comparison in this doc is control vs. twin, and generation 3 never had one. `metrics.twins()` (`observer/metrics.py:243`) defines a twin as a non-control agent whose first temperament equals the control's seed. Generation 3's five agents each had a different temperament (`lab/temperaments/a.md`–`e.md`), so the Observer's summary reads "Synth the Diplomat Agent (e) vs no twin found". The generation 3 control could only be compared with the room as a whole, never with an agent identical to it apart from memory.

   Options:
   - **Six agents:** a–d, the control `e`, and a twin `f` with `e`'s temperament and full memory. This keeps every role. It changes the room size and the quorum ("all but one in support" becomes five of six), and in a goal with five private briefs one member gets none.
   - **Five agents, two of them Synthesizers:** drop one role so `e` and its twin both fit. The room stays the same size, but loses a temperament.

   **Decided (2026-10-14): six agents.** Generation 4 runs a–d, the control `e`, and the twin `f`. The quorum stays `quorum_slack = 1`, so a call needs 5 of 6. To set up the roster, make `lab/temperaments/f.md` an exact copy of `e.md`: `metrics.twins()` compares the whitespace-normalised first version of `temperament.md` with `e`'s seed. `init-agents.sh` needs no change, because it creates one agent per file and only `e` is marked as the control. Points that follow from this:
   - `f` starts generation 4 with no memory, like everyone else, so the control-vs-twin gap starts at zero and grows run by run.
   - With six members, each agent's view (`view_entries = 15`) covers less of the board. Decide whether to raise it, and record the choice.
   - Fixes 1–5 and 7 must land before the first generation 4 run, or the twin is compared with a control that's still leaking memory.
   - Fix 14's copy metric matters more now. `e` and `f` share a temperament and see each other's posts, so similarity from copying has to be told apart from similarity due to memory.

   Original reasoning: six agents is probably the better choice: generation 4 is a new generation anyway, so room size isn't a continuity problem. Either way, `init-agents.sh` needs a twin entry, and `bikeshed observe` finds the twin automatically through `metrics.twins()`. With six agents and five private briefs, fix 7 matters more: the control or the twin will sometimes be the member without a brief.

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

7. **Make the control's brief comparable.** Process 3 deals the control a private brief like any other member, so the control's private facts change from run to run. Record the control's brief slot and compare like with like, or always give it the same slot.

   *Checked against the code:* the slot is already recorded. `run_briefs` stores `agent_id` per slot (`briefs.py:87`). `briefs.assign` (`briefs.py:73`) deals private brief k to the k-th member of a seeded shuffle of all agent ids, with nothing to tell control from twin. When a goal has fewer private briefs than members, the control sometimes gets no private brief at all. Neither `observer/briefs.py` nor the twin comparison (`observe.py:260`) looks at the control's slot. So the gap is on the comparison side, plus the choice of whether to fix the slot at assignment. Fixing the slot would change `assign`, which is part of the run's recorded behaviour.

8. **Label the control's after-run updates.** `e` still journals and rewrites its profile after every run, and the reset then wipes that work. Keep it: it shows how far one cycle moves a blank agent. Label it so the Observer stops reading a reset as `e`'s own change, as it did in runs 7 and 14.

   *Checked against the code:* the post-run cycle has no control handling at all. Nothing in `postrun/cycle.py` mentions controls, so `e`'s updates are committed under the same `"<id> updated <file>"` subjects as everyone else's. The reset itself is already labelled. `profiles.py:181` commits it as `reset control agent <id> to seed`, and the Observer's identity timeline recognises that as a `control reset` event (`observer/metrics.py:213`). The analysis that misreads it is `self_image` (fix 9), which diffs file snapshots and never looks at commit subjects. So the fix is mainly on the Observer side: the commits are already distinguishable.

9. **Observer: order after-run comparisons by time.** The `self_image` judge orders runs by creation, so it compared run 9 against run 8's later after-run cycle and reported reversals that never happened. Order by after-run timestamp, and treat a reset as a reset.

   *Checked against the code:* confirmed. `closed_runs` orders by `rowid`, which is creation order (`observer/metrics.py:57`). `self_image_inputs` (`observer/inputs.py:65`) takes the previous run in that order and reads "before" at that run's `end`. `run_end` includes the post-run steps (`metrics.py:47`). When run 8 was created first but its post-run finished after run 9's, run 9's "before" snapshot is *later* than its "now" snapshot, and the judge sees a reversal. Two changes are needed. Choose the earlier run by `end` (strictly before this run's snapshot), not by `rowid`. And when a `reset control agent` commit falls between the two snapshots, take "before" from the reset, or tell the judge about it.

## Answers to the open questions above

- **Fix 2, `public_profile`:** check it. An agent renamed "null" would be worse than a "null" temperament, because every other member sees the name.

  *Checked against the code:* it can. `public_profile` is all or nothing. A JSON `null` for the whole object keeps the profile (`postrun/cycle.py:344`). But once the object is sent, `name`, `headline` and `introduction` are required (`PublicReply`, `postrun/replies.py:49`), and `PublicProfile`'s validators (`profiles.py:38`) only strip whitespace and check length and printability. So `"null"` passes as a name, headline or introduction, and as pronouns. A model that means "keep" for one field has to resend the other fields, or it writes the string `"null"`. The fix 2 rule should cover these fields too. A name change also adds the old name to `former_names`, so a "null" rename would stay in the record even after it's reverted.
- **Fix 5, top-k:** fill the control's list back up to k from the other members' methods. That keeps the amount of library context equal to the twin's, so the comparison isn't picking up a difference in how much the two were shown.
- **Fix 7, the slot:** keep `assign` as it is for most members, but make sure the control and the twin *both* hold a private brief whenever the goal has fewer briefs than members. A control with no brief against a twin with one tells you nothing about memory. Record which slot each got, and make the twin comparison show it.

## Other generation 3 gaps, outside the control

These came out of the generation 3 review (`learnings_gen3-v2.md` in the report folder). Process 3 doesn't address them. Fixes 10–17 have been checked against the code; 18 and 19 aren't code.

10. **Review pairs come from refs, so reviews form a star around whoever made the call.** `interactions()` (`postrun/feedback.py:9`) links two members only when one entry refers to another, or when they wrote the same file. Once everyone votes on one call, everyone's only link is to its caller. In runs 13 and 14 every peer review ran to or from the caller, and in run 14 that was `d`, who had copied `b`'s proposal word for word; `b`, the author, got one review. Consider linking every pair of members who both took part in a run, or adding a link from a call to the authors of the entries it copies or cites.

    *Done in sub-project B (`2026-10-15-bikeshed-gen4-agent-environment-design.md`):* every member who took part in the run (a public entry other than a pass, or a workspace file) reviews every other member who took part. Refs no longer decide the pairs.

11. **Library retrieval ranks by similarity alone.** `retrieve()` (`library.py:129`) sorts every embedded method by cosine similarity to the goal, with no weight for reception. In generation 3 it mostly returned methods that were never adopted (method #4 seven times), and handed a $20 Sunday plan to the offsite run. Weight by reception (adopted, in the winning answer), and log which retrieved text reappears in proposals. Separately, the spec stores methods with the goal text only, but the method *text* is written by an agent who may have quoted its private brief. Check that brief facts can't reach a later run through method text.

    *Decided in sub-project B:* ranking stays similarity-only. Each method's reception is already in its label, so agents can weigh it themselves. Logging which retrieved text reappears moves to sub-project C, with fix 14's copy metric. Brief facts can't leak through method text into a run where they are still private: queue goals run once, and post-run reveals every brief to every member. A briefed goal must not be re-run within a generation.

12. **A refine doesn't change the call's text, and agents vote as though it does.** Run 5: `d` supported #229 because "my refinement (#233) successfully embedded" checks that #229 never contained. Run 7: `c` praised patches #358 didn't have. Either make an accepted refine produce a new version of the call, or make the view say plainly that a refine leaves the call's text unchanged.

    *Checked against the code:* confirmed. A refine is stored as its own entry (`target`, `change`, `reason`) with a ref to the target (`runner.py:281`, `actions.py:42`), and nothing ever changes the target entry. There's also no such thing as an "accepted" refine: nothing records whether a refine was taken up. The action list in `agent_system.md:18` and `process2_agent_system.md:22` gives the refine shape with no description, unlike propose, adopt and the others. So the prompt never says what a refine does. Any entry can be the target of a refine, including a `call_consensus`. The only target check is that a vote must target a call (`agent.py:126`). Making a refine produce a new version of the call would mean changing what voters are voting on, because votes attach to one call id. Saying plainly in the prompt that a refine is a separate entry is the smaller change. It's a prompt change, so it changes the Process 1 golden recording.

    *Done in sub-project B:* both action lists now say a refine is "a change to an entry, posted as its own entry; the target's text stays as it was." Refines are still stored and voted on as before.

13. **Agents see earlier moves within the same round, and copy them.** Run 12: six entries share one reason. Run 14: three agents post `b`'s vote reason word for word. Decide whether to change this (blind votes within a round) or only measure it. Measuring it is fix 14.

    *Checked against the code:* this is Process 2 working as designed, not a bug. All of generation 3 ran under Process 2 (`runs.process = 2` for every run in `lab/`). From round 2, its react phase is `sequential_act` (`phases.py:77`): "Agents speak one at a time in a seeded order; each sees what was said before its turn." Round 1 react, the think phase and the poll are all simultaneous, and their results are posted only after everyone has finished. Process 1 is fully simultaneous. Run 14's events confirm it. In round 2, `d` posted call #1257, then `b`, `a` and `e` voted one after another in the react phase (#1258–1260), each able to see the votes before it. `c` voted in the poll (#1262) after all of those. So blind votes within a round would change the Process 2 spec, which chose sequential react on purpose. Alternatives are a separate process variant, or keeping the design and measuring the effect through fix 14 and the existing `speaking_order` metric (`observer/process2.py:92`).

    *Decided in sub-project B:* no change. Process 3 keeps Process 2's sequential react, so generation 4's rounds match generation 3's. Sub-project C measures the effect (fix 14 and `speaking_order`).

14. **Observer: measure what shaped generation 3's outcomes.**
    - Add: copying (exact and n-gram matches against earlier entries and the library), credit accuracy (does a call credit the entries it copies?), and rule-gaming in private notes.
    - `think_vs_act` pairs notes with moves from earlier rounds (run 6, `b` and `d`) and counts failed turns as divergence, which gives a flat 25–40% divergence rate in every run. Pair each note with the move that follows it in the same round, and leave out failures.
    - `private_public` mixes in "after" journals, written once the agent has read its reviews. Use only the "before" journal.
    - Process 3's disclosure and fabrication judges are new. Check them against hand-read runs before trusting their counts.

    *Checked against the code:*
    - **Copying, credit accuracy, rule-gaming:** none is measured. The Observer has credit metrics (`metrics.credits`, `credit_pairs` and `scoring.self_credit`, used at `observe.py:222`), but they count who credits whom, not whether a call credits the entries it copied. No metric compares text across entries or against the library.
    - **`think_vs_act`:** confirmed. `think_vs_act_inputs` (`observer/inputs.py:51`) hands the judge all of the agent's think notes in one block and all its public entries in another. The judge pairs them however it likes. Round labels (`[rN]`) are in the rendered entries, but nothing makes the judge pair a note with the move after it. Failed turns are recorded as `pass` entries with a `failure` payload, and they are public, so they land in "public actions" and can be read as divergence. `observer/process2.py:102` already knows how to spot these entries. The inputs just don't leave them out.
    - **`private_public`:** confirmed. `_journal` (`inputs.py:18`) joins every journal section for the run, "before" and "after", in time order, and `private_public_inputs` (`inputs.py:44`) passes all of it.

15. **Export: `runs.csv` shows the start record only.** Its model column read DeepSeek for runs 8 and 9, which mostly ran on other models, and `max_rounds` shows the start value, not the caps lowered at resume. List every model a run used (from traces or every provenance row), and the final `max_rounds`.

    *Checked against the code:* confirmed. `_run_row` (`export.py:166`) reads only the first `run_provenance` row with `phase = 'start'` for both `models` and `max_rounds` (`export.py:172`–`187`). Resume and postrun provenance rows are ignored. The per-run Markdown summary uses the same row (`export.py:241`), so it has the same problem. Per-entry model is already exported from traces (`export.py:229`), so the data exists.

16. **Narrator: `narrative.md` was wrong on facts in at least seven runs** (1, 4, 5, 7, 9, 10 and 12). It credited the wrong agent, misstated which call won, and left out the run 12 rejection. Either check the narrative against the record before it's saved, or label it as unchecked wherever it appears.

    *Checked against the code:* the mechanism, not the seven runs. The narrator gets the code-built account, the answer and the minority report (`postrun/cycle.py:434`, `prompts/narrative_request.md`), and its reply is saved without any check. The account already includes Scorer rulings, rejections included (`report.py`, the "Scorer" section), so the run 12 rejection was in its input. Leaving it out was the narrator's error, not missing data. `narrative.md` already says "Written by the narrator model from the account" in its header, but nothing says it's unchecked. `bikeshed show` prints it straight after `account.md` with nothing to tell them apart (`cli.py:455`). The narrator never reaches agents (it's the last post-run step and the file is only in `report/`). So this affects only what the Observer and Andrew read, and the cheapest fix is the label.

17. **Is the norm list in the after-run context?** Proposal #14 restated Norm 1 almost word for word, and no voter noticed it was already law. Check whether the norm critique and vote steps show the adopted norms.

    *Checked against the code:* they do. The update step (where proposals are written), the critique and the vote all render the adopted norms under "Current community norms" (`postrun/cycle.py:317`, `:376`, `:392`; `prompts/norms_critique_request.md:7`, `norms_vote_request.md:7`). Norm 1 was adopted in run 9 (2026-10-13 20:57), before #14 was proposed in run 10 (2026-10-14 02:49), so it was in front of the proposer and every voter. #14 was rejected anyway. The gap is that nothing points out the overlap: there's no duplicate check, and `render_proposal` (`norms.py:148`) shows a new norm without linking it to similar ones. Whether to add a check is a values question, since a harness flag saying "this repeats Norm 1" could count as steering. Measuring it in the Observer avoids that.

18. **One model for the whole generation.** This is procedure, not code. Generation 3 ran on four models, and the headline finding is that the model sets the culture. Change models only between generations, and record the choice in the generation's notes.

19. **The goal-briefs spec calls the outgoing cohort "generation 1"** (`docs/superpowers/specs/2026-10-14-bikeshed-goal-briefs-design.md`, lines 3 and 7). The report calls it generation 3. Pick one numbering.

## Summary

- Fix 0 comes first: without a twin there's no control comparison at all. Decided: six agents, with `f` as `e`'s twin.
- Fixes 1–3 are bugs that broke the control for a whole run.
- Fixes 4–5 close the two leaks of personal memory, so the control matches the definition above. Fix 4 covers per-dimension reputation as well as the overall score. Fix 5 needs a library list for each control agent, not a filter in the view.
- Fix 6 adds the baseline that separates the model from the culture. It needs its own spec first.
- Fixes 7–9 are measurement problems. Fix 7's slot is already recorded, but the comparison ignores it. Fix 8 is mostly an Observer change, since reset commits are already labelled. Fix 9 is confirmed.
- Fixes 10–19 are generation 3 gaps outside the control, and 10–17 are checked against the code. Three of them aren't bugs:
  - 13 is Process 2's sequential react, working as designed.
  - 17 is already handled, because voters do see the adopted norms.
  - 16 is narrator error, not missing input.

  12, 14 and 15 are confirmed as described.

## Development order

Each sub-project gets its own spec, plan and implementation. Anything that changes what agents see or do must land before generation 4's first run; Observer and export work only reads the record and can land later.

| | Sub-project | Covers | Changes what agents experience? | Status |
|---|---|---|---|---|
| A | Control integrity | Fixes 1–5, 7 (dealing briefs) | Yes | **Done** (merged 2026-10-15, `73a14b7`) |
| F | Process 3 and naming | `docs/nomenclature-cleanup.md` | Yes (new `--process 3` value) | **Done** (merged 2026-10-15, `49e0480`) |
| B | Agent environment | Fixes 10, 11, 12, 13 (decision) | Yes | **In progress**; before generation 4 run 1 |
| C | Measurement | Fixes 7 (twin comparison), 8, 9, 14, 15, 16, 17 | No | Any time; re-run over generation 4 |
| D | Baseline run | Fix 6 | No (separate scratch community) | Before the first pizza benchmark of generation 4 |
| E | Setup and procedure | Fix 0 (`f.md`), 18, sixth briefs, `view_entries` | Config and goals only | Repo side done in A; lab edits and model choice are Andrew's, after F |

F comes straight after A, ahead of B:

- **It sets the tag every generation 4 run carries.** Runs record `process` at start, and it can't change. If F isn't in place, the first generation 4 run is recorded as Process 2.
- **B and C build on it.** B's specs refer to Process 3. C's twin and brief comparisons split by process, and the Observer headings F renames ("Briefs (Process 4)", now "Briefs") are the ones C extends.
- **It's small and mostly mechanical.** It has two real decisions, from the nomenclature note, items 4 and 5:
  - Does `--process 3` refuse a goal with no briefs, and how does the unbriefed pizza benchmark fit?
  - Do briefed goals stay allowed under Processes 1 and 2?

F also corrects one of A's edits. A changed the goal-briefs spec's archive path to `lab/generation-{x}/`. The archive actually lives at `lab/archive/generation-3/`, so the spec goes back to `lab/archive/generation-{x}/`.
