# AISwarm Community Memory Implementation Plan (Plan 2b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the community a memory that outlives each run: a code-built `account.md` and an optional narrator's `narrative.md` for the user; a Method Library of every proposed and refined method with its reception, retrieved by embedding similarity into the next run's View; and agent-proposed norms and rating dimensions, critiqued and voted on after each run, written to `norms.md` and rated in later reviews.

**Architecture:** Everything is plain code around a few new model calls. `account.md` is assembled from the board at close. The post-run cycle gains four steps after `update` — `norms_critique`, `norms_vote` (then a code-only decision), `library` (record + embed methods), and `narrative` — using the same resumable step machinery as Plan 2a. Seeding a run embeds the goal and shows the top methods. Adopted dimensions add per-dimension ratings to reviews and per-dimension reputation to the View.

**Tech Stack:** Python 3.14, uv, openai SDK (chat + embeddings against oMLX), pydantic v2, typer, sqlite3, git, pytest + pytest-asyncio. No new dependencies (`array` from the standard library packs vectors).

**Spec:** `docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md` §4 (Plan 2b), extending `docs/superpowers/specs/2026-10-10-aiswarm-design.md`.

## Global Constraints

- Python `>=3.14`, managed by `uv`. No new dependencies.
- No orchestrator model: code only routes, validates, stores, and applies the consensus rule. The narrator writes for the user only; its output never appears in any View or agent request.
- Observation never feeds back: reasoning traces, `account.md`, and `narrative.md` never appear in any View or agent request.
- The Method Library and `account.md` contain only agents' own words (quoted from board entries) plus counts computed by code.
- Privacy invariants from Plan 2a still hold (private files, journals, `private_note`, self-reviews).
- Prompts are values-neutral. Use the prompt text in this plan verbatim. Norms and dimensions come only from agent proposals; the code never suggests any.
- Norm adoption rule = the run consensus rule: the proposer counts as a supporter unless it votes `object`; adopted when supporters among the run's agents ≥ `max(1, n_agents − quorum_slack)` **and** at least one critique from someone other than the proposer.
- One proposal per agent per run. Limits: norm/amendment text and dimension question ≤ 500 chars (`NORM_MAX`); dimension name ≤ 40 chars (`DIMENSION_NAME_MAX`), unique case-insensitively.
- A dimension adopted after run R is rated only in cycles of later runs (never in run R's own reviews).
- Defaults: `[library] top_k = 3`; `[report] narrator` and `[library] embeddings` unset (features off).
- Post-run failures: `failures` row with `round = 0`, `kind = "postrun:<step>:<kind>"`; the code-only steps use `agent_id = "system"`, the narrator `agent_id = "narrator"`.
- Markdown files: never hard-wrap lines.

## Review Focus

1. **The embeddings server is down or unconfigured when a run starts:** the run proceeds with no library section and records a `library_unavailable` event. Test in Task 4.
2. **An amendment targets a norm number that doesn't exist, or a dimension reuses an existing name:** one correction, then the update step fails; no proposal stored. Test in Task 7.
3. **A proposal everyone supports but nobody else critiques:** rejected, exactly like an unchallenged consensus call. Test in Task 8.
4. **`aiswarm postrun` rerun after norms were decided:** no re-voting, no duplicated or renumbered norms, `norms.md` unchanged. Test in Task 8.
5. **A run from before this plan (no `account.md`) or no narrator configured:** the cycle completes; the narrative step is skipped without a narrator and builds its account from the board with one. Tests in Task 10.

---

## File Structure

```
src/aiswarm/
  config.py              # MODIFY: ReportSettings, LibrarySettings, backend-name validation, DEFAULT_CONFIG
  models/base.py         # MODIFY: Embedder protocol
  models/openai_compat.py# MODIFY: embed()
  models/fake.py         # MODIFY: FakeEmbedder
  models/factory.py      # MODIFY: make_embedder
  db.py                  # MODIFY: migration 3 (methods, norm_proposals, norm_critiques, norm_votes, norms, dimensions, review_ratings)
  report.py              # MODIFY: account(), account.md
  library.py             # CREATE: Reception, Method, record_methods, embed_missing, retrieve, render_methods
  norms.py               # CREATE: proposals, critiques, votes, decide, norms, dimensions, render_norms, render_proposal
  reviews.py             # MODIFY: Rating, set_ratings, ratings_for, dimension_reputations, render_review(ratings=)
  profiles.py            # MODIFY: write_community_file
  view.py                # MODIFY: library section, per-dimension scores on member lines
  runner.py              # MODIFY: embedder + library retrieval at seeding
  postrun/replies.py     # MODIFY: ProposalReply, Norm* replies, DimensionRating, NarrativeReply
  postrun/cycle.py       # MODIFY: norms_critique, norms_vote, decide, library, narrative steps; proposals in update; dimension ratings in review
  cli.py                 # MODIFY: narrator/embedder wiring, show prints account + narrative
  prompts/
    review_request.md          # MODIFY: $dimension_block, $dimension_json
    update_request.md          # MODIFY: norms + proposal
    norms_critique_request.md  # CREATE
    norms_vote_request.md      # CREATE
    narrator_system.md         # CREATE
    narrative_request.md       # CREATE
tests/
  conftest.py            # MODIFY: postrun_reply handles every step
  test_config.py, test_openai_compat.py, test_models_base.py, test_report.py, test_library.py (new),
  test_norms.py (new), test_reviews.py, test_view.py, test_runner.py, test_postrun.py, test_ask.py, test_cli.py
```

Run tests with `uv run pytest -q`. Pipe through `tail` only with `set -o pipefail`.

---

### Task 1: Settings and an embeddings client

**Files:**
- Modify: `src/aiswarm/config.py`, `src/aiswarm/models/base.py`, `src/aiswarm/models/openai_compat.py`, `src/aiswarm/models/fake.py`, `src/aiswarm/models/factory.py`
- Test: `tests/test_config.py`, `tests/test_openai_compat.py`, `tests/test_models_base.py`

**Interfaces:**
- Produces: `ReportSettings(narrator: str | None = None)`; `LibrarySettings(embeddings: str | None = None, top_k: int = 3)`; `Config.report`, `Config.library` (both validated to name configured backends; `embeddings` must be an `openai` backend); `Embedder` protocol (`async embed(texts: list[str]) -> list[list[float]]`); `OpenAICompatClient.embed`; `FakeEmbedder(dims=64, fail_times=0)` (deterministic bag-of-words vectors, records `calls`); `make_embedder(backend: OpenAIBackend) -> OpenAICompatClient`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python


def test_report_and_library_settings(tmp_path):
    cfg = load_config(write(tmp_path, '''
[models.local]
kind = "openai"
model = "m"
[models.embed]
kind = "openai"
model = "e"
[report]
narrator = "local"
[library]
embeddings = "embed"
top_k = 5
'''))
    assert (cfg.report.narrator, cfg.library.embeddings, cfg.library.top_k) == ("local", "embed", 5)
    bare = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n'))
    assert (bare.report.narrator, bare.library.embeddings, bare.library.top_k) == (None, None, 3)


@pytest.mark.parametrize("extra", [
    '[report]\nnarrator = "nope"\n',
    '[library]\nembeddings = "nope"\n',
    '[models.c]\nkind = "claude_cli"\n[library]\nembeddings = "c"\n',
])
def test_report_and_library_must_name_suitable_backends(tmp_path, extra):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n' + extra))
```

In `tests/test_openai_compat.py`, add an embeddings reply and route by path. After `MODELS_REPLY = ...` add:

```python
EMBED_REPLY = {"object": "list", "model": "e", "data": [
    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
], "usage": {"prompt_tokens": 2, "total_tokens": 2}}
```

and change `_Handler.do_POST`'s last line from `self._send(CHAT_REPLY)` to:

```python
        self._send(EMBED_REPLY if self.path.endswith("/embeddings") else CHAT_REPLY)
```

Append:

```python


async def test_embed_returns_vectors_in_input_order(server):
    vectors = await OpenAICompatClient(backend(server)).embed(["first", "second"])
    path, body = server.requests[0]
    assert path == "/v1/embeddings" and body["input"] == ["first", "second"] and body["model"] == "qwen-test"
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
```

Append to `tests/test_models_base.py`:

```python


async def test_fake_embedder_is_deterministic_and_can_fail():
    from aiswarm.models.fake import FakeEmbedder

    embedder = FakeEmbedder(dims=16, fail_times=1)
    with pytest.raises(ModelError):
        await embedder.embed(["x"])
    a, b, c = await embedder.embed(["burden score rotation", "burden score rotation", "unrelated words"])
    assert a == b and a != c and len(a) == 16
    assert embedder.calls[-1] == ["burden score rotation", "burden score rotation", "unrelated words"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_openai_compat.py tests/test_models_base.py -q`
Expected: FAIL — `'Config' object has no attribute 'report'`, `'OpenAICompatClient' object has no attribute 'embed'`, `cannot import name 'FakeEmbedder'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/config.py`, change the pydantic import to `from pydantic import BaseModel, Field, model_validator`, add after `RunSettings`:

```python
class ReportSettings(BaseModel):
    narrator: str | None = None  # a backend that writes report/narrative.md for the user


class LibrarySettings(BaseModel):
    embeddings: str | None = None  # an openai backend serving an embeddings model
    top_k: int = Field(3, ge=0)
```

and in `Config` add the two fields and a validator:

```python
    report: ReportSettings = Field(default_factory=ReportSettings)
    library: LibrarySettings = Field(default_factory=LibrarySettings)

    @model_validator(mode="after")
    def _known_backends(self) -> "Config":
        for label, name in (("report.narrator", self.report.narrator), ("library.embeddings", self.library.embeddings)):
            if name is not None and name not in self.models:
                raise ValueError(f"{label} names unknown model backend {name!r}")
        if self.library.embeddings and not isinstance(self.models[self.library.embeddings], OpenAIBackend):
            raise ValueError("library.embeddings must name an openai backend")
        return self
```

Append to `DEFAULT_CONFIG` (before the closing `"""`):

```

[report]
# narrator = "claude"                  # a backend that writes report/narrative.md (for you; agents never see it)

[library]
# embeddings = "embed"                 # an openai backend serving an embeddings model, e.g.:
#   [models.embed]
#   kind = "openai"
#   base_url = "http://localhost:8000/v1"
#   model = "mlx-community--all-MiniLM-L6-v2-4bit"
top_k = 3                              # methods from earlier runs shown in each View
```

In `src/aiswarm/models/base.py`, after `ModelClient`:

```python
class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

In `src/aiswarm/models/openai_compat.py`, add to `OpenAICompatClient`:

```python
    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await asyncio.wait_for(
                self._client.embeddings.create(model=self.backend.model, input=texts), timeout=self.backend.timeout_s
            )
        except TimeoutError as exc:
            raise ModelError(f"timed out after {self.backend.timeout_s}s") from exc
        except APIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
```

In `src/aiswarm/models/fake.py`, add `import re` and `import zlib` at the top and append:

```python


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are similar."""

    def __init__(self, dims: int = 64, fail_times: int = 0):
        self.dims = dims
        self.fail_times = fail_times
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ModelError("fake outage")
        vectors = []
        for text in texts:
            vector = [0.0] * self.dims
            for word in re.findall(r"[a-z]+", text.lower()):
                vector[zlib.crc32(word.encode()) % self.dims] += 1.0
            vectors.append(vector)
        return vectors
```

In `src/aiswarm/models/factory.py`, append:

```python


def make_embedder(backend: OpenAIBackend) -> OpenAICompatClient:
    return OpenAICompatClient(backend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_openai_compat.py tests/test_models_base.py -q`
Expected: PASS (including `test_default_config_is_valid`).

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/config.py src/aiswarm/models tests/test_config.py tests/test_openai_compat.py tests/test_models_base.py
git commit -m "feat: add report/library settings and an embeddings client"
```

---

### Task 2: `account.md` — the code-built account

**Files:**
- Modify: `src/aiswarm/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Entry`, `StopDecision`, `call_status`, `Who`.
- Produces: `account(goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> str`; `write_report` also writes `report/account.md`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report.py` (add imports `from aiswarm.board import SYSTEM, Entry`, `from aiswarm.consensus import StopDecision`, `from aiswarm.report import account` if not present):

```python


def _e(id, round, author, type, **payload):
    refs = [payload["target"]] if "target" in payload else list(payload.get("refs", []))
    return Entry(id, "r1", round, author, type, payload, refs, "all", "t")


WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", SYSTEM: "system"}.get


def test_account_lists_key_moments_in_agents_own_words():
    entries = [
        _e(1, 0, SYSTEM, "goal", goal="G"),
        _e(2, 1, "a", "propose", answer_summary="RED-SUMMARY", method="RED-METHOD"),
        _e(3, 1, "b", "propose", answer_summary="BLUE", method="M"),
        _e(4, 2, "b", "critique", target=2, about="method", points="TOO-VAGUE"),
        _e(5, 3, "a", "refine", target=2, change="SHARPER", reason="r"),
        _e(6, 3, "c", "adopt", target=2, reason="PERSUADED"),
        _e(7, 4, "c", "adopt", target=2, reason="AGAIN"),
        _e(8, 4, "a", "call_consensus", synthesis="SYNTH", refs=[2], credits=[]),
        _e(9, 5, "b", "vote", target=8, stance="object", reason="NOT-YET"),
        _e(10, 5, SYSTEM, "run_closed", reason="budget", call_id=None),
    ]
    text = account("Pick a colour", entries, StopDecision("budget"), WHO)
    assert text.startswith("# Account\n\n**Goal:** Pick a colour\n\n**Ended:** budget after 5 rounds\n")
    assert '- #2 Alpha (a): "RED-SUMMARY" — method: "RED-METHOD"' in text
    assert '- #4 Bea (b) critiqued #2 (Alpha (a)): "TOO-VAGUE" → #5 Alpha (a) refined it: "SHARPER"' in text
    assert '- r3: Cyd (c) adopted #2 (Alpha (a)): "PERSUADED"' in text and "AGAIN" not in text
    assert '- #8 by Alpha (a) in r4: "SYNTH"' in text and "critiqued: no" in text
    assert '  - r5 Bea (b) object: "NOT-YET"' in text


def test_account_handles_a_quiet_run_and_long_quotes():
    entries = [_e(1, 0, SYSTEM, "goal", goal="G"), _e(2, 1, "a", "propose", answer_summary="x" * 400, method="m")]
    text = account("G", entries, StopDecision("stagnation"), WHO)
    assert "**Ended:** stagnation after 1 round\n" in text
    assert '"' + "x" * 300 + '…"' in text
    assert text.count("_None._") == 3


def test_write_report_writes_account(tmp_path):
    entries = [_e(1, 0, SYSTEM, "goal", goal="G"), _e(2, 1, "a", "propose", answer_summary="S", method="M")]
    write_report(tmp_path, "G", entries, StopDecision("budget"), WHO)
    assert (tmp_path / "report" / "account.md").read_text().startswith("# Account")
```

(If `tests/test_report.py` imports `write_report` under another name, use that.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'account'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/report.py`, add `from typing import Any` and:

```python
def _quote(text: Any, limit: int = 300) -> str:
    flat = " ".join(str(text).split())
    return f'"{flat[:limit]}…"' if len(flat) > limit else f'"{flat}"'


def account(goal: str, entries: list[Entry], decision: StopDecision, who: Who) -> str:
    """The run's key moments, built from the refs graph. Quotes only agents' own words."""
    by_id = {e.id: e for e in entries}
    rounds = max((e.round for e in entries), default=0)
    ended = decision.reason + (f" (call #{decision.call_id})" if decision.call_id else "")
    out = ["# Account", "", f"**Goal:** {goal}", "",
           f"**Ended:** {ended} after {rounds} round{'' if rounds == 1 else 's'}", ""]

    def owner(entry_id: Any) -> str:
        target = by_id.get(entry_id)
        return f"#{entry_id} ({who(target.author)})" if target else f"#{entry_id}"

    out += ["## Opening proposals", ""]
    opening = [
        f"- #{e.id} {who(e.author)}: {_quote(e.payload.get('answer_summary', ''))} — method: {_quote(e.payload.get('method', ''))}"
        for e in entries if e.type == "propose" and e.round == 1
    ]
    out += opening or ["_None._"]

    out += ["", "## Critiques that changed something", ""]
    turns = []
    for critique in entries:
        if critique.type != "critique":
            continue
        target = critique.payload.get("target")
        follow = next((e for e in entries if e.id > critique.id and e.type in ("refine", "adopt")
                       and e.payload.get("target") == target), None)
        if follow:
            verb = "refined" if follow.type == "refine" else "adopted"
            said = _quote(follow.payload.get("change") or follow.payload.get("reason", ""))
            turns.append(f"- #{critique.id} {who(critique.author)} critiqued {owner(target)}: "
                         f"{_quote(critique.payload.get('points', ''))} → #{follow.id} {who(follow.author)} {verb} it: {said}")
    out += turns or ["_None._"]

    out += ["", "## Adoptions", ""]
    seen: set[tuple[str, Any]] = set()
    adoptions = []
    for e in entries:
        key = (e.author, e.payload.get("target"))
        if e.type == "adopt" and key not in seen:
            seen.add(key)
            adoptions.append(f"- r{e.round}: {who(e.author)} adopted {owner(key[1])}: {_quote(e.payload.get('reason', ''))}")
    out += adoptions or ["_None._"]

    out += ["", "## Calls for consensus", ""]
    calls = [e for e in entries if e.type == "call_consensus"]
    for call in calls:
        status = call_status(entries, call.id)
        supporters = ", ".join(sorted(who(a) for a in status.supporters)) or "none"
        objectors = ", ".join(sorted(who(a) for a in status.objectors)) or "none"
        out.append(f"- #{call.id} by {who(call.author)} in r{call.round}: {_quote(call.payload.get('synthesis', ''))} — "
                   f"supporters: {supporters}; objectors: {objectors}; critiqued: {'yes' if status.critiqued else 'no'}")
        out += [f"  - r{v.round} {who(v.author)} {v.payload.get('stance')}: {_quote(v.payload.get('reason', ''))}"
                for v in entries if v.type == "vote" and v.payload.get("target") == call.id]
    if not calls:
        out.append("_None._")
    return "\n".join(out) + "\n"
```

In `write_report`, before writing `answer.md`, add:

```python
    (out / "account.md").write_text(account(goal, entries, decision, who))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/report.py tests/test_report.py
git commit -m "feat: write a code-built account.md of each run's key moments"
```

---

### Task 3: Storage and the Method Library

**Files:**
- Modify: `src/aiswarm/db.py` (append migration 3)
- Create: `src/aiswarm/library.py`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `Entry`, `SYSTEM`, `now_iso`, `Embedder`.
- Produces:
  - Tables: `methods`, `norm_proposals`, `norm_critiques`, `norm_votes`, `norms`, `dimensions`, `review_ratings` (all of Plan 2b's storage).
  - `Reception(adopted_by: list[str], critiques: int, refines: int, questions: int, in_winning: bool)` with `.label(who) -> str`.
  - `Method(id, run_id, entry_id, author, kind, goal, text, reception: Reception)`.
  - `reception_of(entry: Entry, entries: Sequence[Entry]) -> Reception`.
  - `record_methods(conn, run_id, goal, entries) -> int` (upsert per entry; returns count).
  - `async embed_missing(conn, embedder) -> int`.
  - `async retrieve(conn, embedder, goal, top_k, exclude_run=None) -> list[tuple[Method, float]]` (no embed call when nothing to search or `top_k <= 0`).
  - `render_methods(methods: Sequence[Method], who, max_chars=600) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_library.py`:

```python
import pytest

from aiswarm.board import SYSTEM, Board
from aiswarm.library import embed_missing, reception_of, record_methods, render_methods, retrieve
from aiswarm.models.fake import FakeEmbedder

WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)"}.get


def board_run(conn, make_run, run_id, method_a="rotate chores weekly by burden score"):
    make_run(conn, run_id, ("a", "b", "c"))
    board = Board(conn, run_id)
    board.append(0, SYSTEM, "goal", {"goal": "G"})
    a = board.append(1, "a", "propose", {"answer_summary": "S", "method": method_a})
    b = board.append(1, "b", "propose", {"answer_summary": "T", "method": "draw lots for every task"})
    board.append(2, "b", "critique", {"target": a.id, "about": "method", "points": "p"}, refs=[a.id])
    r = board.append(2, "c", "refine", {"target": a.id, "change": "add a swap market", "reason": "r"}, refs=[a.id])
    board.append(3, "c", "adopt", {"target": a.id, "reason": "r"}, refs=[a.id])
    board.append(3, "b", "question", {"target": a.id, "text": "?"}, refs=[a.id])
    call = board.append(4, "c", "call_consensus", {"synthesis": "s", "refs": [a.id], "credits": []}, refs=[a.id])
    board.append(4, SYSTEM, "run_closed", {"reason": "consensus", "call_id": call.id})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = ?", (run_id,))
    return board.entries(), a, b, r


def test_reception_counts_what_others_did_with_an_entry(community, make_run):
    store, conn = community
    entries, a, b, r = board_run(conn, make_run, "r1")
    rec = reception_of(a, entries)
    assert (rec.adopted_by, rec.critiques, rec.refines, rec.questions, rec.in_winning) == (["c"], 1, 1, 1, True)
    assert rec.label(WHO) == "adopted by Cyd (c) · critiqued 1× · refined 1× · questioned 1× · cited by the winning call"
    assert reception_of(b, entries).label(WHO) == "adopted by no one · critiqued 0× · refined 0× · questioned 0×"


def test_record_methods_is_idempotent_and_includes_refines(community, make_run):
    store, conn = community
    entries, a, b, r = board_run(conn, make_run, "r1")
    assert record_methods(conn, "r1", "G", entries) == 3
    assert record_methods(conn, "r1", "G", entries) == 3
    rows = conn.execute("SELECT entry_id, kind, text FROM methods ORDER BY entry_id").fetchall()
    assert [(row["entry_id"], row["kind"], row["text"]) for row in rows] == [
        (a.id, "propose", "rotate chores weekly by burden score"),
        (b.id, "propose", "draw lots for every task"),
        (r.id, "refine", "add a swap market"),
    ]


async def test_embed_and_retrieve_by_similarity_excluding_the_current_run(community, make_run):
    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1")
    record_methods(conn, "r1", "G", entries)
    embedder = FakeEmbedder()
    assert await embed_missing(conn, embedder) == 3
    assert await embed_missing(conn, embedder) == 0
    found = await retrieve(conn, embedder, "how to rotate chores by burden", top_k=2)
    assert [m.text for m, _ in found][0] == "rotate chores weekly by burden score"
    assert len(found) == 2 and found[0][1] >= found[1][1]
    assert await retrieve(conn, embedder, "anything", top_k=2, exclude_run="r1") == []


async def test_retrieve_makes_no_call_when_nothing_to_search(community):
    store, conn = community
    embedder = FakeEmbedder(fail_times=5)
    assert await retrieve(conn, embedder, "goal", top_k=3) == []
    assert await retrieve(conn, embedder, "goal", top_k=0) == []
    assert embedder.calls == []


async def test_render_methods(community, make_run):
    store, conn = community
    entries, *_ = board_run(conn, make_run, "r1", method_a="m" * 700)
    record_methods(conn, "r1", "G", entries)
    await embed_missing(conn, FakeEmbedder())
    [(method, _)] = await retrieve(conn, FakeEmbedder(), "m" * 700, top_k=1)
    [line] = render_methods([method], WHO, max_chars=100)
    assert line.startswith(f"- #{method.entry_id} in run r1 by Alpha (a) (propose); goal: G\n  method: " + "m" * 100 + "…")
    assert "\n  reception: adopted by Cyd (c)" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_library.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.library'`.

- [ ] **Step 3: Add the migration**

Append a third string to `MIGRATIONS` in `src/aiswarm/db.py` (keep the list's closing `]` on its own line):

```python
    """
    CREATE TABLE methods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        entry_id INTEGER NOT NULL REFERENCES entries(id),
        author TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('propose', 'refine')),
        goal TEXT NOT NULL,
        text TEXT NOT NULL,
        reception TEXT NOT NULL,
        embedding BLOB,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, entry_id)
    );
    CREATE TABLE norm_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id),
        author TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('norm', 'amendment', 'dimension')),
        text TEXT NOT NULL DEFAULT '',
        target INTEGER,
        name TEXT NOT NULL DEFAULT '',
        question TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'adopted', 'rejected')),
        created_at TEXT NOT NULL,
        UNIQUE (run_id, author)
    );
    CREATE TABLE norm_critiques (
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        author TEXT NOT NULL,
        points TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (proposal_id, author)
    );
    CREATE TABLE norm_votes (
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        author TEXT NOT NULL,
        stance TEXT NOT NULL CHECK (stance IN ('support', 'object')),
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (proposal_id, author)
    );
    CREATE TABLE norms (
        number INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        updated_at TEXT NOT NULL
    );
    CREATE TABLE dimensions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        question TEXT NOT NULL,
        proposal_id INTEGER NOT NULL REFERENCES norm_proposals(id),
        adopted_run TEXT NOT NULL REFERENCES runs(id)
    );
    CREATE TABLE review_ratings (
        review_id INTEGER NOT NULL REFERENCES reviews(id),
        dimension_id INTEGER NOT NULL REFERENCES dimensions(id),
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        reason TEXT NOT NULL,
        PRIMARY KEY (review_id, dimension_id)
    );
    """,
```

- [ ] **Step 4: Implement `library.py`**

Create `src/aiswarm/library.py`:

```python
"""The Method Library: every proposed or refined method, in its author's words, with how it was received."""

import json
import math
import sqlite3
from array import array
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from aiswarm.board import SYSTEM, Entry
from aiswarm.db import now_iso
from aiswarm.models.base import Embedder

Who = Callable[[str], str]


@dataclass(frozen=True)
class Reception:
    adopted_by: list[str]
    critiques: int
    refines: int
    questions: int
    in_winning: bool

    def label(self, who: Who) -> str:
        adopted = ", ".join(who(a) for a in self.adopted_by) or "no one"
        parts = [f"adopted by {adopted}", f"critiqued {self.critiques}×", f"refined {self.refines}×",
                 f"questioned {self.questions}×"]
        if self.in_winning:
            parts.append("cited by the winning call")
        return " · ".join(parts)


@dataclass(frozen=True)
class Method:
    id: int
    run_id: str
    entry_id: int
    author: str
    kind: str
    goal: str
    text: str
    reception: Reception


def reception_of(entry: Entry, entries: Sequence[Entry]) -> Reception:
    closed = next((e for e in reversed(entries) if e.type == "run_closed"), None)
    call_id = closed.payload.get("call_id") if closed else None
    winning = next((e for e in entries if e.id == call_id), None) if call_id else None
    others = [e for e in entries if e.payload.get("target") == entry.id and e.author not in (entry.author, SYSTEM)]
    return Reception(
        adopted_by=sorted({e.author for e in others if e.type == "adopt"}),
        critiques=sum(e.type == "critique" for e in others),
        refines=sum(e.type == "refine" for e in others),
        questions=sum(e.type == "question" for e in others),
        in_winning=winning is not None and entry.id in winning.refs,
    )


def record_methods(conn: sqlite3.Connection, run_id: str, goal: str, entries: Sequence[Entry]) -> int:
    count = 0
    for entry in entries:
        if entry.type not in ("propose", "refine") or entry.author == SYSTEM:
            continue
        text = entry.payload.get("method" if entry.type == "propose" else "change", "")
        conn.execute(
            "INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, entry_id) DO UPDATE SET "
            "text = excluded.text, reception = excluded.reception",
            (run_id, entry.id, entry.author, entry.type, goal, text,
             json.dumps(asdict(reception_of(entry, entries))), now_iso()),
        )
        count += 1
    return count


def _pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


def _method(row: sqlite3.Row) -> Method:
    return Method(id=row["id"], run_id=row["run_id"], entry_id=row["entry_id"], author=row["author"],
                  kind=row["kind"], goal=row["goal"], text=row["text"], reception=Reception(**json.loads(row["reception"])))


async def embed_missing(conn: sqlite3.Connection, embedder: Embedder) -> int:
    rows = conn.execute("SELECT id, text FROM methods WHERE embedding IS NULL ORDER BY id").fetchall()
    if not rows:
        return 0
    vectors = await embedder.embed([row["text"] for row in rows])
    for row, vector in zip(rows, vectors):
        conn.execute("UPDATE methods SET embedding = ? WHERE id = ?", (_pack(vector), row["id"]))
    return len(rows)


async def retrieve(
    conn: sqlite3.Connection, embedder: Embedder, goal: str, top_k: int, exclude_run: str | None = None,
) -> list[tuple[Method, float]]:
    if top_k <= 0:
        return []
    rows = conn.execute(
        "SELECT * FROM methods WHERE embedding IS NOT NULL AND run_id IS NOT ?", (exclude_run,)
    ).fetchall()
    if not rows:
        return []
    [query] = await embedder.embed([goal])
    scored = [(_method(row), _cosine(query, _unpack(row["embedding"]))) for row in rows]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)[:top_k]


def render_methods(methods: Sequence[Method], who: Who, max_chars: int = 600) -> list[str]:
    def clip(text: str) -> str:
        return text if len(text) <= max_chars else text[:max_chars] + "…"

    return [
        f"- #{m.entry_id} in run {m.run_id} by {who(m.author)} ({m.kind}); goal: {clip(m.goal)}\n"
        f"  method: {clip(m.text)}\n  reception: {m.reception.label(who)}"
        for m in methods
    ]
```

(`run_id IS NOT ?` with `None` matches every row, so `exclude_run=None` excludes nothing.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_library.py -q`
Expected: PASS. Then `set -o pipefail; uv run pytest -q 2>&1 | tail -3` — all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/db.py src/aiswarm/library.py tests/test_library.py
git commit -m "feat: add Plan 2b storage and the Method Library"
```

---

### Task 4: Methods from earlier runs in the View

**Files:**
- Modify: `src/aiswarm/view.py`, `src/aiswarm/runner.py`
- Test: `tests/test_view.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `retrieve`, `render_methods` (Task 3); `Embedder`, `FakeEmbedder` (Task 1).
- Produces: `ViewInputs.library: tuple[str, ...] = ()`; `collect_inputs(..., library: Sequence[str] = ())`; View section `# Methods from earlier runs` (trimmed, after recent entries, before workspace files); `Runner(..., embedder: Embedder | None = None, library_top_k: int = 3)`; events `library_retrieved` (`methods`: list of method ids) and `library_unavailable` (`detail`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_view.py`:

```python


def test_view_shows_methods_from_earlier_runs(tmp_path, community, make_run):
    store, conn, board, ws = setup(tmp_path, community, make_run)
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15, library=["- #9 in run old by Bea (b) (propose); goal: G\n  method: LIB-METHOD"])
    view = build_view(inputs, budget_chars=50_000)
    assert "# Methods from earlier runs\n- #9 in run old" in view
    assert view.index("# Methods from earlier runs") < view.index("# Workspace files")
    assert "# Methods from earlier runs" not in view_for("a", 2, store, board, ws)
```

Append to `tests/test_runner.py`:

```python


async def test_runs_show_relevant_methods_from_earlier_runs(tmp_path, community, runner_factory, make_run):
    from aiswarm.events import read_events
    from aiswarm.library import embed_missing, record_methods
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    make_run(conn, "old", ("a", "b"))
    old = Board(conn, "old")
    old.append(0, SYSTEM, "goal", {"goal": "Pick a colour"})
    old.append(1, "b", "propose", {"answer_summary": "x", "method": "LIB-METHOD pick colour by vote"})
    conn.execute("UPDATE runs SET status = 'closed' WHERE id = 'old'")
    record_methods(conn, "old", "Pick a colour", old.entries())
    embedder = FakeEmbedder()
    await embed_missing(conn, embedder)
    seen = FakeModel([json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}})])
    runner = runner_factory(store, conn, {"a": seen}, max_rounds=1)
    runner.embedder = embedder
    run_id = runner.start("Pick a colour", ["a"])
    await runner.run(run_id)
    assert "# Methods from earlier runs" in seen.calls[0][1][0]["content"]
    assert "LIB-METHOD" in seen.calls[0][1][0]["content"]
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert any(e["event"] == "library_retrieved" for e in events)


async def test_a_run_proceeds_without_the_library_when_embeddings_fail(tmp_path, community, runner_factory):
    from aiswarm.events import read_events
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO entries (run_id, round, author, type, payload, created_at) VALUES ('old', 1, 'b', 'propose', '{}', 't')")
    conn.execute("INSERT INTO methods (run_id, entry_id, author, kind, goal, text, reception, embedding, created_at) "
                 "VALUES ('old', 1, 'b', 'propose', 'g', 't', '{\"adopted_by\": [], \"critiques\": 0, \"refines\": 0, "
                 "\"questions\": 0, \"in_winning\": false}', x'0000803f', 't')")
    seen = FakeModel([json.dumps({"action": {"type": "propose", "answer_summary": "x", "method": "m"}})])
    runner = runner_factory(store, conn, {"a": seen}, max_rounds=1)
    runner.embedder = FakeEmbedder(fail_times=5)
    run_id = runner.start("Pick a colour", ["a"])
    result = await runner.run(run_id)
    assert result.status == "closed"
    assert "# Methods from earlier runs" not in seen.calls[0][1][0]["content"]
    events = read_events(tmp_path / "runs" / run_id / "events.jsonl")
    assert any(e["event"] == "library_unavailable" for e in events)
```

(Make sure `tests/test_runner.py` imports `Board` and `SYSTEM` from `aiswarm.board`; add `from aiswarm.board import SYSTEM, Board` if missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_view.py tests/test_runner.py -q`
Expected: FAIL — `collect_inputs() got an unexpected keyword argument 'library'`; no library section in the run's view.

- [ ] **Step 3: Implement**

In `src/aiswarm/view.py`:
- add `from collections.abc import Callable, Mapping, Sequence` (extend the existing import);
- add to `ViewInputs` (after `reputations`): `library: tuple[str, ...] = ()`;
- add `library: Sequence[str] = (),` to `collect_inputs`'s keyword parameters (after `half_life`), and `library=tuple(library),` to its `ViewInputs(...)`;
- in `build_view`'s trimmed-sections loop, insert the library section after recent entries:

```python
    for title, items in (
        ("# Entries that respond to you", [render_entry(e, who) for e in about_me]),
        ("# Recent board entries", [render_entry(e, who) for e in recent]),
        ("# Methods from earlier runs", list(inputs.library)),
        ("# Workspace files", files),
        ("# Community members", members),
    ):
```

In `src/aiswarm/runner.py`:
- imports: `from aiswarm.library import render_methods, retrieve` and `from aiswarm.models.base import Embedder, ModelClient, ModelError`;
- `Runner.__init__` gains keyword params `embedder: Embedder | None = None, library_top_k: int = 3` stored as `self.embedder`, `self.library_top_k`;
- add the method:

```python
    async def _library(self, run_id: str, goal: str, events: EventLog) -> list[str]:
        """Methods from earlier runs most similar to the goal, rendered for the View. Never fails the run."""
        if self.embedder is None:
            return []
        try:
            found = await retrieve(self.conn, self.embedder, goal, self.library_top_k, exclude_run=run_id)
        except ModelError as exc:
            events.emit("library_unavailable", detail=str(exc))
            return []
        events.emit("library_retrieved", methods=[method.id for method, _ in found])
        who = make_who({i: self.store.public(i) for i in self.store.ids()})
        return render_methods([method for method, _ in found], who)
```

- in `run`, after `self.conn.execute("UPDATE runs SET status = 'running' ...")`, add `library = await self._library(run_id, goal, events)`; pass `library` to `_step` (`self._step(a, rnd, goal, agent_ids, board, ws, events, library)`); add the `library: Sequence[str]` parameter to `_step` and `library=library` to its `collect_inputs(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_view.py tests/test_runner.py -q`
Expected: PASS. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/view.py src/aiswarm/runner.py tests/test_view.py tests/test_runner.py
git commit -m "feat: show relevant methods from earlier runs in each View"
```

---

### Task 5: The cycle records and embeds methods

**Files:**
- Modify: `src/aiswarm/postrun/cycle.py`
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: `record_methods`, `embed_missing` (Task 3); `Embedder` (Task 1).
- Produces: `PostRun(..., embedder: Embedder | None = None)`; `STEPS == ("journal", "review", "responses", "update", "library")`; the `library` step is one call with `agent_id="system"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postrun.py`:

```python


async def test_cycle_records_and_embeds_methods(tmp_path, community, make_run, postrun_reply):
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    post = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder())
    assert (await post.run(run_id)).status == "done"
    rows = conn.execute("SELECT author, embedding FROM methods WHERE run_id = ?", (run_id,)).fetchall()
    assert sorted(r["author"] for r in rows) == ["a", "b", "c"] and all(r["embedding"] for r in rows)
    assert steps(conn)[("library", "system", "")] == "done"


async def test_methods_are_recorded_without_an_embedder(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    await cycle(store, conn, tmp_path, {a: FakeModel(responder(postrun_reply)) for a in "abc"}).run(run_id)
    assert conn.execute("SELECT COUNT(*) FROM methods WHERE embedding IS NULL").fetchone()[0] == 3


async def test_an_embeddings_outage_pauses_at_the_library_step(tmp_path, community, make_run, postrun_reply):
    from aiswarm.models.fake import FakeEmbedder

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    post = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder(fail_times=9))
    assert (await post.run(run_id)).status == "paused"
    assert steps(conn)[("update", "a", "")] == "done"
    assert conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0] == 3
    later = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, embedder=FakeEmbedder())
    assert (await later.run(run_id)).status == "done"
    assert conn.execute("SELECT COUNT(*) FROM methods WHERE embedding IS NULL").fetchone()[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q -k "methods or library"`
Expected: FAIL — `PostRun.__init__() got an unexpected keyword argument 'embedder'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/postrun/cycle.py`:
- imports: `from aiswarm.library import embed_missing, record_methods` and `from aiswarm.models.base import Embedder, ModelClient, ModelError`;
- `STEPS = ("journal", "review", "responses", "update", "library")`;
- `PostRun.__init__` gains keyword param `embedder: Embedder | None = None` stored as `self.embedder`;
- in `_calls`, before the final `raise`:

```python
        if step == "library":
            return [_Call(step, "system", "", lambda: self._library(ctx))]
```

- add the step:

```python
    async def _library(self, ctx: _Context) -> None:
        record_methods(self.conn, ctx.run_id, ctx.goal, ctx.entries)
        if self.embedder is not None:
            await embed_missing(self.conn, self.embedder)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: PASS (existing tests unaffected: without an embedder the library step only records). Full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/postrun/cycle.py tests/test_postrun.py
git commit -m "feat: record and embed each run's methods in the post-run cycle"
```

---

### Task 6: Norms and dimensions — storage, decision, rendering

**Files:**
- Create: `src/aiswarm/norms.py`
- Modify: `src/aiswarm/profiles.py` (`write_community_file`)
- Test: `tests/test_norms.py`

**Interfaces:**
- Consumes: migration 3 tables (Task 3); `now_iso`, `transaction`.
- Produces: `NORM_MAX = 500`, `DIMENSION_NAME_MAX = 40`; `Proposal(id, run_id, author, kind, text, target, name, question, status)`; `Dimension(id, name, question, adopted_run)`; `add_proposal(conn, *, run_id, author, kind, text="", target=None, name="", question="") -> None` (upsert while open); `proposals(conn, run_id, status=None) -> list[Proposal]`; `add_norm_critique(conn, proposal_id, author, points)`; `add_norm_vote(conn, proposal_id, author, stance, reason)`; `critiques_of(conn, proposal_id) -> list[tuple[str, str]]`; `current_norms(conn) -> list[tuple[int, str]]`; `dimensions(conn, exclude_run=None) -> list[Dimension]`; `decide(conn, run_id, agent_ids, quorum_slack) -> list[Proposal]` (closes every open proposal of the run; returns them with their new status); `render_norms(conn) -> str` (`""` when nothing adopted); `render_proposal(p, who, norms: Mapping[int, str]) -> str`; `ProfileStore.write_community_file(filename, content, message) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_norms.py`:

```python
from aiswarm.norms import (
    add_norm_critique, add_norm_vote, add_proposal, critiques_of, current_norms, decide, dimensions, proposals,
    render_norms, render_proposal,
)

AGENTS = ["a", "b", "c", "d"]
WHO = {"a": "Alpha (a)", "b": "Bea (b)", "c": "Cyd (c)", "d": "Dee (d)"}.get


def run(conn, run_id="r1"):
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES (?, 'g', 'closed', '[]', 't')", (run_id,))
    return run_id


def support(conn, proposal_id, *authors, stance="support"):
    for author in authors:
        add_norm_vote(conn, proposal_id, author, stance, "because")


def test_proposal_with_quorum_and_an_outside_critique_is_adopted(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Cite entry ids.")
    [p] = proposals(conn, "r1")
    add_norm_critique(conn, p.id, "b", "Too strict?")
    support(conn, p.id, "b", "c")                        # a counts as supporter: a, b, c = 3 of 4
    [decided] = decide(conn, "r1", AGENTS, quorum_slack=1)
    assert decided.status == "adopted"
    assert current_norms(conn) == [(1, "Cite entry ids.")]
    assert render_norms(conn) == "1. Cite entry ids.\n"
    assert critiques_of(conn, p.id) == [("b", "Too strict?")]


def test_unchallenged_or_short_of_quorum_is_rejected(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Unchallenged.")
    add_proposal(conn, run_id="r1", author="b", kind="norm", text="Short.")
    p1, p2 = proposals(conn, "r1")
    add_norm_critique(conn, p1.id, "a", "my own critique does not count")
    support(conn, p1.id, "b", "c", "d")
    add_norm_critique(conn, p2.id, "c", "fine")
    support(conn, p2.id, "c")
    support(conn, p2.id, "b", "d", stance="object")        # b, the author, objects to its own proposal
    assert [p.status for p in decide(conn, "r1", AGENTS, quorum_slack=1)] == ["rejected", "rejected"]
    assert current_norms(conn) == [] and render_norms(conn) == ""
    assert decide(conn, "r1", AGENTS, quorum_slack=1) == []      # nothing left open


def test_amendments_replace_text_and_dimensions_are_unique(community):
    store, conn = community
    run(conn, "r1")
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Old text.")
    add_proposal(conn, run_id="r1", author="b", kind="dimension", name="Credit-giving", question="Do they credit others?")
    for p in proposals(conn, "r1"):
        add_norm_critique(conn, p.id, "d", "ok")
        support(conn, p.id, "a", "b", "c")
    decide(conn, "r1", AGENTS, quorum_slack=1)
    run(conn, "r2")
    add_proposal(conn, run_id="r2", author="c", kind="amendment", target=1, text="New text.")
    add_proposal(conn, run_id="r2", author="d", kind="dimension", name="credit-giving", question="dup")
    for p in proposals(conn, "r2"):
        add_norm_critique(conn, p.id, "a", "ok")
        support(conn, p.id, "a", "b", "c", "d")
    assert [p.status for p in decide(conn, "r2", AGENTS, quorum_slack=1)] == ["adopted", "rejected"]
    assert current_norms(conn) == [(1, "New text.")]
    assert [(d.name, d.adopted_run) for d in dimensions(conn)] == [("Credit-giving", "r1")]
    assert dimensions(conn, exclude_run="r1") == []
    assert render_norms(conn) == '1. New text.\n\n## Rating dimensions\n\n- "Credit-giving": Do they credit others?\n'


def test_a_proposal_can_be_revised_only_while_open(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="First.")
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Second.")
    assert [p.text for p in proposals(conn, "r1")] == ["Second."]
    decide(conn, "r1", AGENTS, quorum_slack=1)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="Third.")
    assert [(p.text, p.status) for p in proposals(conn, "r1")] == [("Second.", "rejected")]


def test_render_proposal(community):
    store, conn = community
    run(conn)
    add_proposal(conn, run_id="r1", author="a", kind="norm", text="N.")
    add_proposal(conn, run_id="r1", author="b", kind="amendment", target=1, text="A.")
    add_proposal(conn, run_id="r1", author="c", kind="dimension", name="Care", question="Q?")
    norm, amendment, dimension = proposals(conn, "r1")
    texts = {1: "Current."}
    assert render_proposal(norm, WHO, texts) == f'Proposal {norm.id} by Alpha (a): a new norm: "N."'
    assert render_proposal(amendment, WHO, texts) == f'Proposal {amendment.id} by Bea (b): amend norm 1 (now: "Current.") to: "A."'
    assert render_proposal(dimension, WHO, texts) == f'Proposal {dimension.id} by Cyd (c): a new rating dimension "Care": Q?'


def test_write_community_file_commits(community):
    store, conn = community
    store.write_community_file("norms.md", "1. X.\n", "norms adopted")
    assert (store.root / "norms.md").read_text() == "1. X.\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_norms.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'aiswarm.norms'`.

- [ ] **Step 3: Implement**

In `src/aiswarm/profiles.py`, add to `ProfileStore`:

```python
    def write_community_file(self, filename: str, content: str, message: str) -> None:
        (self.root / filename).write_text(content)
        self._repo.commit([filename], message, author="community")
```

Create `src/aiswarm/norms.py`:

```python
"""Community norms and rating dimensions: agents propose, critique, and vote; code applies the consensus rule."""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from aiswarm.db import now_iso, transaction

NORM_MAX = 500
DIMENSION_NAME_MAX = 40
Who = Callable[[str], str]


@dataclass(frozen=True)
class Proposal:
    id: int
    run_id: str
    author: str
    kind: str
    text: str
    target: int | None
    name: str
    question: str
    status: str


@dataclass(frozen=True)
class Dimension:
    id: int
    name: str
    question: str
    adopted_run: str


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(row["id"], row["run_id"], row["author"], row["kind"], row["text"], row["target"],
                    row["name"], row["question"], row["status"])


def add_proposal(
    conn: sqlite3.Connection, *, run_id: str, author: str, kind: str, text: str = "", target: int | None = None,
    name: str = "", question: str = "",
) -> None:
    conn.execute(
        "INSERT INTO norm_proposals (run_id, author, kind, text, target, name, question, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, author) DO UPDATE SET kind = excluded.kind, "
        "text = excluded.text, target = excluded.target, name = excluded.name, question = excluded.question, "
        "created_at = excluded.created_at WHERE norm_proposals.status = 'open'",
        (run_id, author, kind, text, target, name, question, now_iso()),
    )


def proposals(conn: sqlite3.Connection, run_id: str, status: str | None = None) -> list[Proposal]:
    sql, params = "SELECT * FROM norm_proposals WHERE run_id = ?", [run_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    return [_proposal(row) for row in conn.execute(sql + " ORDER BY id", params)]


def add_norm_critique(conn: sqlite3.Connection, proposal_id: int, author: str, points: str) -> None:
    conn.execute(
        "INSERT INTO norm_critiques (proposal_id, author, points, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (proposal_id, author) DO UPDATE SET points = excluded.points, created_at = excluded.created_at",
        (proposal_id, author, points, now_iso()),
    )


def add_norm_vote(conn: sqlite3.Connection, proposal_id: int, author: str, stance: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO norm_votes (proposal_id, author, stance, reason, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (proposal_id, author) DO UPDATE SET stance = excluded.stance, reason = excluded.reason, "
        "created_at = excluded.created_at",
        (proposal_id, author, stance, reason, now_iso()),
    )


def critiques_of(conn: sqlite3.Connection, proposal_id: int) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT author, points FROM norm_critiques WHERE proposal_id = ? ORDER BY rowid", (proposal_id,))
    return [(row["author"], row["points"]) for row in rows]


def current_norms(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    return [(row["number"], row["text"]) for row in conn.execute("SELECT number, text FROM norms ORDER BY number")]


def dimensions(conn: sqlite3.Connection, exclude_run: str | None = None) -> list[Dimension]:
    rows = conn.execute("SELECT * FROM dimensions WHERE adopted_run IS NOT ? ORDER BY id", (exclude_run,))
    return [Dimension(row["id"], row["name"], row["question"], row["adopted_run"]) for row in rows]


def _apply(conn: sqlite3.Connection, p: Proposal) -> bool:
    if p.kind == "norm":
        number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM norms").fetchone()[0]
        conn.execute("INSERT INTO norms (number, text, proposal_id, updated_at) VALUES (?, ?, ?, ?)",
                     (number, p.text, p.id, now_iso()))
        return True
    if p.kind == "amendment":
        updated = conn.execute("UPDATE norms SET text = ?, proposal_id = ?, updated_at = ? WHERE number = ?",
                               (p.text, p.id, now_iso(), p.target))
        return updated.rowcount == 1
    try:
        conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES (?, ?, ?, ?)",
                     (p.name, p.question, p.id, p.run_id))
    except sqlite3.IntegrityError:
        return False
    return True


def decide(conn: sqlite3.Connection, run_id: str, agent_ids: Sequence[str], quorum_slack: int) -> list[Proposal]:
    """Close every open proposal of the run with the run consensus rule; apply the adopted ones."""
    members = set(agent_ids)
    needed = max(1, len(members) - quorum_slack)
    decided = []
    with transaction(conn):
        for p in proposals(conn, run_id, "open"):
            stances = {p.author: "support"}
            for row in conn.execute("SELECT author, stance FROM norm_votes WHERE proposal_id = ?", (p.id,)):
                stances[row["author"]] = row["stance"]
            supporters = {a for a, s in stances.items() if s == "support" and a in members}
            critiqued = conn.execute(
                "SELECT 1 FROM norm_critiques WHERE proposal_id = ? AND author != ?", (p.id, p.author)
            ).fetchone() is not None
            status = "adopted" if critiqued and len(supporters) >= needed and _apply(conn, p) else "rejected"
            conn.execute("UPDATE norm_proposals SET status = ? WHERE id = ?", (status, p.id))
            decided.append(replace(p, status=status))
    return decided


def render_norms(conn: sqlite3.Connection) -> str:
    norms, dims = current_norms(conn), dimensions(conn)
    if not norms and not dims:
        return ""
    lines = [f"{number}. {text}" for number, text in norms] or ["(no norms adopted yet)"]
    if dims:
        lines += ["", "## Rating dimensions", ""] + [f'- "{d.name}": {d.question}' for d in dims]
    return "\n".join(lines) + "\n"


def render_proposal(p: Proposal, who: Who, norms: Mapping[int, str]) -> str:
    if p.kind == "norm":
        what = f'a new norm: "{p.text}"'
    elif p.kind == "amendment":
        what = f'amend norm {p.target} (now: "{norms.get(p.target, "?")}") to: "{p.text}"'
    else:
        what = f'a new rating dimension "{p.name}": {p.question}'
    return f"Proposal {p.id} by {who(p.author)}: {what}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_norms.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiswarm/norms.py src/aiswarm/profiles.py tests/test_norms.py
git commit -m "feat: store norm proposals, critiques and votes; decide by the consensus rule"
```

---

### Task 7: Agents propose norms and dimensions in the update step

**Files:**
- Modify: `src/aiswarm/postrun/replies.py`, `src/aiswarm/postrun/cycle.py`, `src/aiswarm/prompts/update_request.md`
- Modify: `tests/conftest.py` (responder handles every Plan 2b step), `tests/test_ask.py`
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: `add_proposal`, `current_norms`, `dimensions`, `render_norms`, `NORM_MAX`, `DIMENSION_NAME_MAX` (Task 6).
- Produces: `ProposalReply(kind, text="", target=None, name="", question="")` validated per kind; `UpdateReply.proposal: ProposalReply | None = None`; the update request shows current norms and offers one proposal.

- [ ] **Step 1: Update the test responder**

In `tests/conftest.py`, replace the body of `_postrun_reply` after `step = marker.group(1)` with:

```python
    if step == "journal":
        return json.dumps({"journal": "Thoughts."})
    if step == "review":
        ids = [int(i) for i in re.findall(r"^#(\d+) \[r", request, re.M)]
        ratings = [{"dimension": name, "rating": 3, "reason": "Fine."}
                   for name in re.findall(r'^- "(.+?)": ', request, re.M)]
        reply = {"more_of": "More.", "less_of": "Less.", "private_note": "Between us.",
                 "evidence": ids[:1] or [1], "overall": 4, "overall_reason": "Solid."}
        if ratings:  # only when the request lists rating dimensions (Task 9)
            reply["dimension_ratings"] = ratings
        return json.dumps(reply)
    if step == "responses":
        ids = [int(i) for i in re.findall(r"^Review (\d+) of", request, re.M)]
        return json.dumps({"responses": [{"review_id": i, "stance": "accept", "reason": "Fair."} for i in ids]})
    if step == "update":
        return json.dumps({"journal": "After reviews.", "lessons": "Listen first.", "self_description": None,
                           "temperament": None, "peer_notes": None, "public_profile": None, "proposal": None})
    if step == "norms_critique":
        ids = [int(i) for i in re.findall(r"^Proposal (\d+) by", request, re.M)]
        return json.dumps({"critiques": [{"proposal_id": i, "points": "Consider edge cases."} for i in ids]})
    if step == "norms_vote":
        ids = [int(i) for i in re.findall(r"^Proposal (\d+) by", request, re.M)]
        return json.dumps({"votes": [{"proposal_id": i, "stance": "support", "reason": "Agreed."} for i in ids]})
    if step == "narrative":
        return json.dumps({"narrative": "It went the way the account says."})
    raise ValueError(f"no default reply for post-run step {step!r}")
```

(The responder only sends `dimension_ratings` when a review request lists dimensions, which first happens in Task 9; until then `ReviewReply` would reject the key.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_postrun.py`:

```python


async def test_update_can_make_one_proposal_and_sees_current_norms(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(proposal={"kind": "norm", "text": "Cite ids."})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    [p] = proposals(conn, run_id)
    assert (p.author, p.kind, p.text) == ("a", "norm", "Cite ids.")
    update = next(m[0]["content"] for _, m, _ in clients["b"].calls if m[0]["content"].startswith("# Post-run: update"))
    assert "## Community norms\n\n(none adopted yet)" in update
    assert '"proposal"' in update


async def test_an_amendment_must_target_an_existing_norm(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        proposal={"kind": "amendment", "target": 7, "text": "x"})))
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert result.failed == 1 and proposals(conn, run_id) == []
    assert steps(conn)[("update", "a", "")] == "failed"


async def test_a_dimension_must_not_reuse_a_name(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('old', 'a', 'dimension', 'Care', 'Q', 'adopted', 't')")
    conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES ('Care', 'Q', 1, 'old')")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, update=update_reply(
        proposal={"kind": "dimension", "name": "care", "question": "Again?"})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert proposals(conn, run_id) == []
```

In `tests/test_postrun.py`, update `update_reply`'s `base` dict to include `"proposal": None`.

In `tests/test_ask.py`, in `test_postrun_templates_render`, add `norms="N", norm_max=7, dimension_max=8,` to the `update_request.md` render call.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py tests/test_ask.py -q`
Expected: FAIL — the three new tests fail: `proposal` is an extra key `UpdateReply` still forbids, so no proposal is stored and the request has no norms section. (`test_postrun_templates_render` passes either way: extra template values are harmless.)

- [ ] **Step 4: Implement**

In `src/aiswarm/postrun/replies.py`, change the pydantic import to include `model_validator`, import `from aiswarm.norms import DIMENSION_NAME_MAX, NORM_MAX`, and add before `UpdateReply`:

```python
class ProposalReply(_Reply):
    kind: Literal["norm", "amendment", "dimension"]
    text: str = Field("", max_length=NORM_MAX)
    target: int | None = None
    name: str = Field("", max_length=DIMENSION_NAME_MAX)
    question: str = Field("", max_length=NORM_MAX)

    @model_validator(mode="after")
    def _fields_for_kind(self) -> "ProposalReply":
        if self.kind in ("norm", "amendment") and not self.text.strip():
            raise ValueError("a norm or an amendment needs text")
        if self.kind == "amendment" and self.target is None:
            raise ValueError("an amendment needs the number of the norm it replaces (target)")
        if self.kind == "dimension" and not (self.name.strip() and self.question.strip()):
            raise ValueError("a dimension needs a name and a question")
        return self
```

and add to `UpdateReply`: `proposal: ProposalReply | None = None`.

In `src/aiswarm/prompts/update_request.md`, insert after the `## Reviews of you, with your responses` block (before `Now write:`):

```

## Community norms

$norms
```

and after the `public_profile` bullet add:

```

You may also make one proposal to the community; every member will see it, may critique it, and will vote on it. Use null for none.
- proposal: {"kind": "norm", "text": "..."} for a new norm, {"kind": "amendment", "target": <norm number>, "text": "..."} to replace an existing norm's text, or {"kind": "dimension", "name": "...", "question": "..."} for a new dimension that members rate each other on in reviews (text and question $norm_max characters at most, name $dimension_max).
```

and change the reply line's ending from `"public_profile": {...} or null}` to `"public_profile": {...} or null, "proposal": {...} or null}`.

In `src/aiswarm/postrun/cycle.py`:
- imports: `from aiswarm.norms import DIMENSION_NAME_MAX, NORM_MAX, add_proposal, current_norms, dimensions, render_norms`;
- in `_update`, add to the `render(...)` call: `norms=render_norms(self.conn) or "(none adopted yet)", norm_max=NORM_MAX, dimension_max=DIMENSION_NAME_MAX,`;
- replace `answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, UpdateReply)` with:

```python
        numbers = {number for number, _ in current_norms(self.conn)}
        taken = {d.name.casefold() for d in dimensions(self.conn)}

        def check(reply: UpdateReply) -> None:
            p = reply.proposal
            if p and p.kind == "amendment" and p.target not in numbers:
                raise ReplyError(f"there is no norm {p.target} to amend; current norms: {sorted(numbers) or 'none'}")
            if p and p.kind == "dimension" and p.name.strip().casefold() in taken:
                raise ReplyError(f"a dimension named {p.name.strip()!r} already exists")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, UpdateReply, check)
```

- after the public-profile block and before building `journal`, add:

```python
        if reply.proposal is not None:
            p = reply.proposal
            add_proposal(self.conn, run_id=ctx.run_id, author=agent_id, kind=p.kind, text=p.text.strip(),
                         target=p.target, name=p.name.strip(), question=p.question.strip())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py tests/test_ask.py -q`
Expected: PASS. Full suite — all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/postrun/replies.py src/aiswarm/postrun/cycle.py src/aiswarm/prompts/update_request.md tests/conftest.py tests/test_ask.py tests/test_postrun.py
git commit -m "feat: let agents propose a norm, amendment, or rating dimension after each run"
```

---

### Task 8: Deliberation — critique, vote, decide, `norms.md`

**Files:**
- Create: `src/aiswarm/prompts/norms_critique_request.md`, `src/aiswarm/prompts/norms_vote_request.md`
- Modify: `src/aiswarm/postrun/replies.py`, `src/aiswarm/postrun/cycle.py`
- Test: `tests/test_postrun.py`

**Interfaces:**
- Consumes: Task 6 functions; `PostRun` (Tasks 5, 7).
- Produces: `NormCritique`, `NormCritiquesReply(critiques: list[NormCritique])`, `NormVote`, `NormVotesReply(votes: list[NormVote])`; `PostRun(..., quorum_slack: int = 1)`; `STEPS == ("journal", "review", "responses", "update", "norms_critique", "norms_vote", "library")`; after the vote step a code-only decision closes the run's proposals and keeps `community/norms.md` equal to `render_norms`; event `norms_decided` (`adopted`, `rejected`: proposal ids).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postrun.py`:

```python


def proposing(postrun_reply, kind="norm", **fields):
    proposal = {"kind": kind, **fields}
    return FakeModel(responder(postrun_reply, update=update_reply(proposal=proposal)))


async def test_a_critiqued_proposal_with_quorum_is_adopted_into_norms_md(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import critiques_of, proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = proposing(postrun_reply, text="Cite entry ids.")
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert (result.status, result.failed) == ("done", 0)
    [p] = proposals(conn, run_id)
    assert p.status == "adopted"
    assert [author for author, _ in critiques_of(conn, p.id)] == ["a", "b", "c"]
    assert (store.root / "norms.md").read_text() == "1. Cite entry ids.\n"
    vote = next(m[0]["content"] for _, m, _ in clients["b"].calls if m[0]["content"].startswith("# Post-run: norms_vote"))
    assert f"Proposal {p.id} by Alpha (a): a new norm" in vote and "critique by Cyd (c): Consider edge cases." in vote
    names = [e["event"] for e in read_events(tmp_path / "runs" / run_id / "events.jsonl")]
    assert "norms_decided" in names


async def test_a_proposal_nobody_else_critiques_is_rejected(tmp_path, community, make_run, postrun_reply):
    from aiswarm.norms import proposals

    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    no_critique = lambda request: json.dumps({"critiques": []})
    clients = {a: FakeModel(responder(postrun_reply, norms_critique=no_critique)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, norms_critique=no_critique,
                                       update=update_reply(proposal={"kind": "norm", "text": "Unchallenged."})))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert [p.status for p in proposals(conn, run_id)] == ["rejected"]
    assert not (store.root / "norms.md").exists()


async def test_no_proposals_means_no_deliberation_calls(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, clients).run(run_id)
    asked = [m[0]["content"].split("\n")[0] for c in clients.values() for _, m, _ in c.calls]
    assert not any("norms_" in line for line in asked)


async def test_rerun_after_the_decision_does_not_vote_again(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = proposing(postrun_reply, text="Cite entry ids.")
    await cycle(store, conn, tmp_path, clients).run(run_id)
    again = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    await cycle(store, conn, tmp_path, again).run(run_id)
    assert [len(again[a].calls) for a in "abc"] == [0, 0, 0]
    assert (store.root / "norms.md").read_text() == "1. Cite entry ids.\n"
    assert conn.execute("SELECT COUNT(*) FROM norms").fetchone()[0] == 1


async def test_critiques_must_name_open_proposals_and_votes_cover_each(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    bad_critique = lambda request: json.dumps({"critiques": [{"proposal_id": 999, "points": "?"}]})
    partial_vote = lambda request: json.dumps({"votes": []})
    clients = {a: FakeModel(responder(postrun_reply)) for a in "c"}
    clients["b"] = FakeModel(responder(postrun_reply, norms_critique=bad_critique, norms_vote=partial_vote))
    clients["a"] = proposing(postrun_reply, text="X.")
    result = await cycle(store, conn, tmp_path, clients).run(run_id)
    assert steps(conn)[("norms_critique", "b", "")] == "failed"
    assert steps(conn)[("norms_vote", "b", "")] == "failed"
    assert result.failed == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py -q -k "proposal or deliberation or decision or critiques_must"`
Expected: FAIL — proposals stay `open`; no `norms.md`.

- [ ] **Step 3: Write the prompts (verbatim)**

Create `src/aiswarm/prompts/norms_critique_request.md`:

```
# Post-run: norms_critique

After run $run_id, members made these proposals to the community.

$proposals

## Current community norms

$norms

You may critique any of these proposals. Other members will read your critiques before they vote.

Reply with JSON only: {"critiques": [{"proposal_id": <proposal id>, "points": "..."}]}, with an empty list if you have none.
```

Create `src/aiswarm/prompts/norms_vote_request.md`:

```
# Post-run: norms_vote

After run $run_id, members made these proposals to the community. The critiques they received are listed under each.

$proposals

## Current community norms

$norms

Vote on each proposal. A proposal is adopted if it has the support of all but at most $slack of the members, and at least one critique from someone other than its author.

Reply with JSON only: {"votes": [{"proposal_id": <proposal id>, "stance": "support" or "object", "reason": "..."}]}, with one vote for each proposal.
```

- [ ] **Step 4: Implement**

In `src/aiswarm/postrun/replies.py`, append:

```python


class NormCritique(_Reply):
    proposal_id: int
    points: Text


class NormCritiquesReply(_Reply):
    critiques: list[NormCritique]


class NormVote(_Reply):
    proposal_id: int
    stance: Literal["support", "object"]
    reason: Text


class NormVotesReply(_Reply):
    votes: list[NormVote]
```

In `src/aiswarm/postrun/cycle.py`:
- imports: extend the replies import with `NormCritiquesReply, NormVotesReply`; extend the norms import with `add_norm_critique, add_norm_vote, critiques_of, decide, proposals, render_proposal`;
- `STEPS = ("journal", "review", "responses", "update", "norms_critique", "norms_vote", "library")`;
- `PostRun.__init__` gains keyword `quorum_slack: int = 1` stored as `self.quorum_slack`;
- in `run`, restructure the step loop so the decision runs after the vote step whether or not calls were pending:

```python
        for step in STEPS:
            pending = [call for call in self._calls(step, ctx) if not self._done(run_id, call)]
            if pending:
                outcomes = await asyncio.gather(*(self._attempt(ctx, call) for call in pending))
                failed += sum(outcome != "done" for outcome in outcomes)
                # Pause only for an outage: every call hit a model error and nothing in this step ever finished.
                # A resumed step whose only pending calls belong to one dead backend must not stall everyone else.
                if all(outcome == "model_error" for outcome in outcomes) and not self._step_started(run_id, step):
                    ctx.events.emit("postrun_paused", step=step)
                    return PostRunResult(run_id, "paused", failed)
            if step == "norms_vote":
                self._decide(ctx)
```

- in `_calls`, before the final `raise`:

```python
        if step in ("norms_critique", "norms_vote"):
            if not proposals(self.conn, ctx.run_id, "open"):
                return []
            method = self._norms_critique if step == "norms_critique" else self._norms_vote
            return [_Call(step, a, "", lambda a=a, m=method: m(ctx, a)) for a in ctx.agent_ids]
```

- add the steps and the decision:

```python
    def _proposals_text(self, ctx: _Context, with_critiques: bool) -> str:
        texts = dict(current_norms(self.conn))
        blocks = []
        for p in proposals(self.conn, ctx.run_id, "open"):
            lines = [render_proposal(p, ctx.who, texts)]
            if with_critiques:
                critiques = critiques_of(self.conn, p.id)
                lines += [f"  critique by {ctx.who(a)}: {points}" for a, points in critiques] or ["  (no critiques)"]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    async def _norms_critique(self, ctx: _Context, agent_id: str) -> str | None:
        open_ids = {p.id for p in proposals(self.conn, ctx.run_id, "open")}
        request = render("norms_critique_request.md", run_id=ctx.run_id, proposals=self._proposals_text(ctx, False),
                         norms=render_norms(self.conn) or "(none adopted yet)")

        def check(reply: NormCritiquesReply) -> None:
            unknown = [c.proposal_id for c in reply.critiques if c.proposal_id not in open_ids]
            if unknown:
                raise ReplyError(f"proposal {unknown[0]} is not one of the open proposals {sorted(open_ids)}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, NormCritiquesReply, check)
        with transaction(self.conn):
            for c in answer.reply.critiques:
                add_norm_critique(self.conn, c.proposal_id, agent_id, c.points)
        return answer.reasoning

    async def _norms_vote(self, ctx: _Context, agent_id: str) -> str | None:
        expected = sorted(p.id for p in proposals(self.conn, ctx.run_id, "open"))
        request = render("norms_vote_request.md", run_id=ctx.run_id, proposals=self._proposals_text(ctx, True),
                         norms=render_norms(self.conn) or "(none adopted yet)", slack=self.quorum_slack)

        def check(reply: NormVotesReply) -> None:
            if sorted(v.proposal_id for v in reply.votes) != expected:
                raise ReplyError(f"give exactly one vote for each proposal id: {expected}")

        answer = await ask(self.clients[agent_id], self._system(ctx, agent_id), request, NormVotesReply, check)
        with transaction(self.conn):
            for v in answer.reply.votes:
                add_norm_vote(self.conn, v.proposal_id, agent_id, v.stance, v.reason)
        return answer.reasoning

    def _decide(self, ctx: _Context) -> None:
        decided = decide(self.conn, ctx.run_id, ctx.agent_ids, self.quorum_slack)
        content = render_norms(self.conn)
        path = self.store.root / "norms.md"
        if content and (not path.exists() or path.read_text() != content):
            self.store.write_community_file("norms.md", content, f"norms after run {ctx.run_id}")
        if decided:
            ctx.events.emit("norms_decided", adopted=[p.id for p in decided if p.status == "adopted"],
                            rejected=[p.id for p in decided if p.status == "rejected"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py -q`
Expected: PASS. Full suite — all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/prompts/norms_critique_request.md src/aiswarm/prompts/norms_vote_request.md src/aiswarm/postrun/replies.py src/aiswarm/postrun/cycle.py tests/test_postrun.py
git commit -m "feat: deliberate on proposals after each run and keep norms.md current"
```

---

### Task 9: Rating dimensions in reviews and reputation

**Files:**
- Modify: `src/aiswarm/reviews.py`, `src/aiswarm/postrun/replies.py`, `src/aiswarm/postrun/cycle.py`, `src/aiswarm/prompts/review_request.md`, `src/aiswarm/view.py`, `src/aiswarm/cli.py`
- Test: `tests/test_reviews.py`, `tests/test_postrun.py`, `tests/test_view.py`, `tests/test_ask.py`

**Interfaces:**
- Consumes: `dimensions` (Task 6).
- Produces: `Rating(dimension: str, rating: int, reason: str)`; `set_ratings(conn, review_id, ratings: Sequence[tuple[int, int, str]])`; `ratings_for(conn, review_ids) -> dict[int, list[Rating]]`; `dimension_reputations(conn, agent_id, half_life) -> list[tuple[str, Reputation]]`; `render_review(..., ratings: Sequence[Rating] = ())`; `render_public_reviews` includes ratings; `DimensionRating(dimension, rating 1–5, reason)` and `ReviewReply.dimension_ratings: list[DimensionRating] = []`; `ViewInputs.dimension_scores: Mapping[str, list[tuple[str, Reputation]]]`; member lines end with ` · <name> <score>` per rated dimension.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reviews.py`:

```python


def adopt_dimension(conn, name="Care", run_id="r0"):
    closed_run(conn, run_id, "2025-12-31T00:00:00+00:00")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES (?, 'a', 'dimension', ?, 'Q?', 'adopted', 't')", (run_id, name))
    pid = conn.execute("SELECT MAX(id) FROM norm_proposals").fetchone()[0]
    return conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES (?, 'Q?', ?, ?) "
                        "RETURNING id", (name, pid, run_id)).fetchone()[0]


def test_dimension_ratings_store_render_and_feed_reputation(community):
    from aiswarm.reviews import dimension_reputations, ratings_for, render_review, set_ratings

    store, conn = community
    care = adopt_dimension(conn)
    closed_run(conn, "r1", "2026-01-01T00:00:00+00:00")
    r = review(conn, "r1", "b", "a")
    set_ratings(conn, r.id, [(care, 2, "Rushed.")])
    set_ratings(conn, r.id, [(care, 5, "Careful.")])
    [rating] = ratings_for(conn, [r.id])[r.id]
    assert (rating.dimension, rating.rating, rating.reason) == ("Care", 5, "Careful.")
    assert "  Care: 5/5 — Careful." in render_review(r, WHO, private=False, ratings=[rating])
    assert "Care: 5/5" in render_public_reviews(conn, "a", WHO)
    [(name, rep)] = dimension_reputations(conn, "a", half_life=5)
    assert (name, rep.score, rep.count) == ("Care", 5.0, 1)
    assert dimension_reputations(conn, "b", half_life=5) == []
```

Append to `tests/test_postrun.py`:

```python


async def test_reviews_rate_dimensions_adopted_in_earlier_runs_only(tmp_path, community, make_run, postrun_reply):
    from aiswarm.reviews import ratings_for

    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('old', 'a', 'dimension', 'Care', 'Do they take care?', 'adopted', 't')")
    conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES ('Care', 'Do they take care?', 1, 'old')")
    run_id = finished_run(conn, tmp_path, make_run)
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = proposing(postrun_reply, kind="dimension", name="Candour", question="Are they candid?")
    await cycle(store, conn, tmp_path, clients).run(run_id)
    review_request = next(m[0]["content"] for _, m, _ in clients["b"].calls if m[0]["content"].startswith("# Post-run: review"))
    assert '- "Care": Do they take care?' in review_request and "Candour" not in review_request
    ids = [r["id"] for r in conn.execute("SELECT id FROM reviews")]
    assert all([x.dimension for x in ratings_for(conn, ids)[i]] == ["Care"] for i in ids)


async def test_a_review_missing_a_dimension_rating_is_corrected(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES ('old', 'g', 'closed', '[]', 't')")
    conn.execute("INSERT INTO norm_proposals (run_id, author, kind, name, question, status, created_at) "
                 "VALUES ('old', 'a', 'dimension', 'Care', 'Q?', 'adopted', 't')")
    conn.execute("INSERT INTO dimensions (name, question, proposal_id, adopted_run) VALUES ('Care', 'Q?', 1, 'old')")
    run_id = finished_run(conn, tmp_path, make_run)
    unrated = json.dumps({"more_of": "m", "less_of": "l", "private_note": "p", "evidence": [2], "overall": 3,
                          "overall_reason": "r"})
    clients = {a: FakeModel(responder(postrun_reply)) for a in "bc"}
    clients["a"] = FakeModel(responder(postrun_reply, review=lambda request: unrated))
    await cycle(store, conn, tmp_path, clients).run(run_id)
    assert steps(conn)[("review", "a", "a")] == "failed"
```

Append to `tests/test_view.py`:

```python


def test_member_lines_show_dimension_scores(tmp_path, community, make_run):
    from aiswarm.reviews import Reputation

    store, conn, board, ws = setup(tmp_path, community, make_run)
    inputs = collect_inputs(agent_id="a", goal="Pick a colour", round=2, board=board, store=store, workspace=ws,
                            view_entries=15)
    inputs = replace(inputs, dimension_scores={"b": [("Care", Reputation(4.5, 2)), ("Candour", Reputation(None, 0))]})
    view = build_view(inputs, budget_chars=50_000)
    assert "- Bea (b) — Bea headline · no reviews yet · Care 4.5\n" in view
```

(add `from dataclasses import replace` at the top of `tests/test_view.py`).

In `tests/test_ask.py`, add `dimension_block="", dimension_json="",` to the `review_request.md` render call.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reviews.py tests/test_postrun.py tests/test_view.py tests/test_ask.py -q`
Expected: FAIL — `cannot import name 'set_ratings'`; `ViewInputs` has no `dimension_scores`; reviews fail on the extra `dimension_ratings` key.

- [ ] **Step 3: Implement storage and rendering**

In `src/aiswarm/reviews.py`, add:

```python
@dataclass(frozen=True)
class Rating:
    dimension: str
    rating: int
    reason: str


def set_ratings(conn: sqlite3.Connection, review_id: int, ratings: Sequence[tuple[int, int, str]]) -> None:
    for dimension_id, rating, reason in ratings:
        conn.execute(
            "INSERT INTO review_ratings (review_id, dimension_id, rating, reason) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (review_id, dimension_id) DO UPDATE SET rating = excluded.rating, reason = excluded.reason",
            (review_id, dimension_id, rating, reason),
        )


def ratings_for(conn: sqlite3.Connection, review_ids: Sequence[int]) -> dict[int, list[Rating]]:
    out: dict[int, list[Rating]] = {i: [] for i in review_ids}
    if not review_ids:
        return out
    marks = ", ".join("?" for _ in review_ids)
    rows = conn.execute(
        f"SELECT rr.review_id, d.name, rr.rating, rr.reason FROM review_ratings rr JOIN dimensions d "
        f"ON d.id = rr.dimension_id WHERE rr.review_id IN ({marks}) ORDER BY d.id", list(review_ids))
    for row in rows:
        out[row["review_id"]].append(Rating(row["name"], row["rating"], row["reason"]))
    return out


def dimension_reputations(conn: sqlite3.Connection, agent_id: str, half_life: float) -> list[tuple[str, Reputation]]:
    rows = conn.execute(
        "SELECT d.name, rr.rating, (SELECT COUNT(*) FROM runs later WHERE later.status = 'closed' "
        "AND (later.created_at, later.id) > (u.created_at, u.id)) AS age "
        "FROM review_ratings rr JOIN reviews r ON r.id = rr.review_id JOIN runs u ON u.id = r.run_id "
        "JOIN dimensions d ON d.id = rr.dimension_id WHERE r.reviewee = ? AND r.is_self = 0 ORDER BY d.id",
        (agent_id,),
    ).fetchall()
    grouped: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append((0.5 ** (row["age"] / half_life), row["rating"]))
    return [(name, Reputation(sum(w * r for w, r in pairs) / sum(w for w, _ in pairs), len(pairs)))
            for name, pairs in grouped.items()]
```

Change `render_review`'s signature to add `ratings: Sequence[Rating] = ()` (keyword, after `response`) and, after the `overall` line, add:

```python
    lines += [f"  {r.dimension}: {r.rating}/5 — {r.reason}" for r in ratings]
```

In `render_public_reviews`, fetch `ratings = ratings_for(conn, [r.id for r in reviews])` and pass `ratings=ratings[r.id]` to each `render_review` call.

- [ ] **Step 4: Implement the review step and the View**

In `src/aiswarm/postrun/replies.py`, add before `ReviewReply`:

```python
class DimensionRating(_Reply):
    dimension: Text
    rating: int = Field(ge=1, le=5)
    reason: Text
```

and to `ReviewReply`: `dimension_ratings: list[DimensionRating] = Field(default_factory=list)`.

In `src/aiswarm/prompts/review_request.md`, replace

```
- overall_reason: why.

$visibility

Reply with JSON only: {"more_of": "...", "less_of": "...", "private_note": "...", "evidence": [<entry ids>], "overall": <1-5>, "overall_reason": "..."}
```

with

```
- overall_reason: why.
$dimension_block
$visibility

Reply with JSON only: {"more_of": "...", "less_of": "...", "private_note": "...", "evidence": [<entry ids>], "overall": <1-5>, "overall_reason": "..."$dimension_json}
```

In `src/aiswarm/postrun/cycle.py` `_review`:
- import `set_ratings` from `aiswarm.reviews`;
- before `request = render(...)`, compute:

```python
        dims = dimensions(self.conn, exclude_run=ctx.run_id)
        dimension_block = "" if not dims else (
            f"\nAlso rate {'yourself' if own else 'them'} on each of the community's rating dimensions, "
            "as an integer from 1 to 5 with a reason:\n" + "\n".join(f'- "{d.name}": {d.question}' for d in dims) + "\n"
        )
        dimension_json = "" if not dims else ', "dimension_ratings": [{"dimension": "<name>", "rating": <1-5>, "reason": "..."}]'
```

- pass `dimension_block=dimension_block, dimension_json=dimension_json` to `render`;
- extend `check`:

```python
            names = sorted(d.name.casefold() for d in dims)
            if sorted(x.dimension.strip().casefold() for x in reply.dimension_ratings) != names:
                raise ReplyError(f"rate each dimension exactly once: {[d.name for d in dims]}")
```

- after `add_review(...)`, capture its return as `stored = add_review(...)` and add:

```python
        by_name = {d.name.casefold(): d.id for d in dims}
        set_ratings(self.conn, stored.id, [(by_name[x.dimension.strip().casefold()], x.rating, x.reason)
                                           for x in r.dimension_ratings])
```

- in `_update`, show ratings to the reviewee: compute `ratings = ratings_for(self.conn, [r.id for r in reviews])` (import `ratings_for`) and pass `ratings=ratings[r.id]` in its `render_review` calls.

In `src/aiswarm/view.py`:
- import `dimension_reputations` alongside `reputation`;
- `ViewInputs` gains `dimension_scores: Mapping[str, list[tuple[str, Reputation]]] = field(default_factory=dict)`;
- `collect_inputs` adds `dimension_scores={i: dimension_reputations(board.conn, i, half_life) for i in store.ids()},`;
- in the members loop, extend the suffix:

```python
        rep = inputs.reputations.get(agent_id)
        suffix = f" · {rep.label()}" if rep else ""
        suffix += "".join(f" · {name} {score.score:.1f}" for name, score in inputs.dimension_scores.get(agent_id, [])
                          if score.count and score.score is not None)
```

In `src/aiswarm/cli.py` `agents_show`, import `dimension_reputations, ratings_for`; after the reputation line print each dimension (`typer.echo(f"{name}: {rep.label()}")` for `name, rep in dimension_reputations(...)`), and pass `ratings=ratings[r.id]` (from `ratings_for`) to `render_review` in the reviews listing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reviews.py tests/test_postrun.py tests/test_view.py tests/test_ask.py -q`
Expected: PASS. Full suite — all pass.

- [ ] **Step 6: Commit**

```bash
git add src/aiswarm/reviews.py src/aiswarm/postrun/replies.py src/aiswarm/postrun/cycle.py src/aiswarm/prompts/review_request.md src/aiswarm/view.py src/aiswarm/cli.py tests/test_reviews.py tests/test_postrun.py tests/test_view.py tests/test_ask.py
git commit -m "feat: rate adopted dimensions in reviews and show per-dimension reputation"
```

---

### Task 10: The narrator and CLI wiring

**Files:**
- Create: `src/aiswarm/prompts/narrator_system.md`, `src/aiswarm/prompts/narrative_request.md`
- Modify: `src/aiswarm/postrun/replies.py`, `src/aiswarm/postrun/cycle.py`, `src/aiswarm/cli.py`
- Test: `tests/test_postrun.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `account` (Task 2); `StopDecision`; `ReportSettings`, `LibrarySettings`, `make_embedder` (Task 1); `PostRun` embedder/quorum (Tasks 5, 8).
- Produces: `NarrativeReply(narrative ≤ 6000)`; `PostRun(..., narrator: ModelClient | None = None)`; `STEPS == ("journal", "review", "responses", "update", "norms_critique", "norms_vote", "library", "narrative")`; `report/narrative.md`; CLI passes narrator, embedder, `library_top_k`, and `quorum_slack`; `aiswarm show` prints `account.md` and `narrative.md` when present.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postrun.py`:

```python


async def test_the_narrator_writes_a_user_only_narrative(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)                    # no account.md, like a pre-2b run
    narrator = FakeModel(responder(postrun_reply))
    clients = {a: FakeModel(responder(postrun_reply)) for a in "abc"}
    post = PostRun(conn=conn, store=store, runs_dir=tmp_path / "runs", clients=clients, narrator=narrator)
    assert (await post.run(run_id)).status == "done"
    text = (tmp_path / "runs" / run_id / "report" / "narrative.md").read_text()
    assert text.startswith("# Narrative\n") and "It went the way the account says." in text
    request = narrator.calls[0][1][0]["content"]
    assert request.startswith("# Post-run: narrative") and "# Account" in request and "COMPETING-POSITIONS" in request
    for client in clients.values():
        assert all("It went the way" not in m["content"] for _, ms, _ in client.calls for m in ms)


async def test_no_narrator_means_no_narrative(tmp_path, community, make_run, postrun_reply):
    store, conn = community
    run_id = finished_run(conn, tmp_path, make_run)
    await cycle(store, conn, tmp_path, {a: FakeModel(responder(postrun_reply)) for a in "abc"}).run(run_id)
    assert not (tmp_path / "runs" / run_id / "report" / "narrative.md").exists()
```

Append to `tests/test_cli.py`:

```python


def test_narrator_and_show(project):
    tmp_path, config = project
    config.write_text(config.read_text().replace('# narrator = "claude"', 'narrator = "local"'))
    make_agents(config, "a")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    shown = invoke("show", run_id, "-c", config)
    assert "# Account" in shown.output and "# Narrative" in shown.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_postrun.py tests/test_cli.py -q -k "narrat"`
Expected: FAIL — `unexpected keyword argument 'narrator'`; no narrative.

- [ ] **Step 3: Write the prompts (verbatim)**

Create `src/aiswarm/prompts/narrator_system.md`:

```
You write concise accounts of how a small community of AI agents worked through a goal together, for the researcher who studies them. Use only the facts you are given. Reply with JSON only.
```

Create `src/aiswarm/prompts/narrative_request.md`:

```
# Post-run: narrative

## Account (the run's key moments, assembled by code)

$account

## Outcome

$answer

## Minority report

$minority

Write a concise prose account of how this run unfolded: the key moments, how positions shifted, and how it ended. Not a transcript ($narrative_max characters at most).

Reply with JSON only: {"narrative": "..."}
```

- [ ] **Step 4: Implement the step**

In `src/aiswarm/postrun/replies.py`, append:

```python


class NarrativeReply(_Reply):
    narrative: JournalText
```

In `src/aiswarm/postrun/cycle.py`:
- imports: `NarrativeReply` from replies; `from aiswarm.consensus import StopDecision`; `from aiswarm.report import account`;
- `STEPS = ("journal", "review", "responses", "update", "norms_critique", "norms_vote", "library", "narrative")`;
- `PostRun.__init__` gains keyword `narrator: ModelClient | None = None` stored as `self.narrator`;
- in `_calls`, before the final `raise`:

```python
        if step == "narrative":
            return [_Call(step, "narrator", "", lambda: self._narrative(ctx))] if self.narrator else []
```

- add:

```python
    async def _narrative(self, ctx: _Context) -> str | None:
        assert self.narrator is not None
        closed = next((e for e in reversed(ctx.entries) if e.type == "run_closed"), None)
        decision = StopDecision(closed.payload.get("reason", "budget"), closed.payload.get("call_id")) if closed \
            else StopDecision("budget")
        report = self.runs_dir / ctx.run_id / "report"
        minority = report / "minority.md"
        request = render(
            "narrative_request.md", account=account(ctx.goal, ctx.entries, decision, ctx.who),
            answer=ctx.answer.strip() or "(no report)",
            minority=minority.read_text().strip() if minority.exists() else "(none)", narrative_max=JOURNAL_MAX,
        )
        answer = await ask(self.narrator, render("narrator_system.md"), request, NarrativeReply)
        report.mkdir(parents=True, exist_ok=True)
        (report / "narrative.md").write_text(
            "# Narrative\n\n_Written by the narrator model from the account. No agent ever sees this._\n\n"
            + answer.reply.narrative.strip() + "\n"
        )
        return answer.reasoning
```

- [ ] **Step 5: Wire the CLI**

In `src/aiswarm/cli.py`:
- imports: `from aiswarm.models.factory import make_client, make_embedder`;
- add near `_clients`:

```python
_NARRATOR = "#narrator"  # never a valid agent id, so it can share the limited-clients dict


def _support(cfg: Config) -> tuple[Any, Any]:
    narrator = make_client(cfg.models[cfg.report.narrator], cfg.run.model_retries) if cfg.report.narrator else None
    embedder = make_embedder(cfg.models[cfg.library.embeddings]) if cfg.library.embeddings else None
    return narrator, embedder
```

- in `run`, after `clients = _clients(...)`, add `narrator, embedder = _support(cfg)`; in `go()` replace the `limited = ...` line and the `Runner(...)`/`PostRun(...)` constructions with:

```python
        limited = limit_clients({**clients, **({_NARRATOR: narrator} if narrator else {})}, cfg.run.max_concurrent_calls)
        narrator_client = limited.pop(_NARRATOR, None)
        runner = Runner(settings=cfg.run, conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                        sink=_print_event, embedder=embedder, library_top_k=cfg.library.top_k)
```

and

```python
            post = await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                                 narrator=narrator_client, embedder=embedder, quorum_slack=cfg.run.quorum_slack,
                                 sink=_print_event).run(run_id)
```

- in `postrun`, the same: `narrator, embedder = _support(cfg)` after `clients = ...`, and inside `go()`:

```python
        limited = limit_clients({**clients, **({_NARRATOR: narrator} if narrator else {})}, cfg.run.max_concurrent_calls)
        narrator_client = limited.pop(_NARRATOR, None)
        return await PostRun(conn=conn, store=store, runs_dir=cfg.runs_dir, clients=limited,
                             narrator=narrator_client, embedder=embedder, quorum_slack=cfg.run.quorum_slack,
                             sink=_print_event).run(run_id)
```

- in `show`, print the extra files when present:

```python
    for name in ("answer.md", "minority.md", "account.md", "narrative.md"):
        path = report / name
        if path.exists():
            typer.echo(path.read_text())
```

(replacing the two existing `typer.echo(...)` lines).

In `tests/test_cli.py`'s `project` fixture, also patch the embedder factory so no test touches the network:

```python
    monkeypatch.setattr(cli, "make_embedder", lambda backend: FakeEmbedder())
```

(import `FakeEmbedder` from `aiswarm.models.fake`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_postrun.py tests/test_cli.py -q`
Expected: PASS. Full suite — all pass.

- [ ] **Step 7: Commit**

```bash
git add src/aiswarm/prompts/narrator_system.md src/aiswarm/prompts/narrative_request.md src/aiswarm/postrun/replies.py src/aiswarm/postrun/cycle.py src/aiswarm/cli.py tests/test_postrun.py tests/test_cli.py
git commit -m "feat: add the narrator step and wire narrator, embeddings and library into the CLI"
```

---

### Task 11: Live check and spec status

**Files:**
- Modify: `docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md` (status line)

- [ ] **Step 1: Configure the lab**

In the scratchpad lab (`lab-local`; never commit its `aiswarm.toml`), add an embeddings backend and enable the library and a narrator:

```toml
[models.embed]
kind = "openai"
base_url = "http://localhost:8008/v1"
model = "mlx-community--all-MiniLM-L6-v2-4bit"
api_key = "<same key as the other oMLX entries>"

[report]
narrator = "qwen4b-fast"

[library]
embeddings = "embed"
top_k = 3
```

- [ ] **Step 2: Backfill the library from earlier runs**

```bash
for r in $(sqlite3 community/community.db "select id from runs where status='closed' order by created_at"); do
  /Users/andrew/Develop/90_PLAYGROUND/AISwarm/.venv/bin/aiswarm postrun "$r"
done
```

Expected: each earlier run's new steps (deliberation only if proposals exist, `library`, `narrative`) complete; `methods` holds every earlier proposal with an embedding; a `narrative.md` appears per run.

- [ ] **Step 3: One fresh run end to end**

```bash
/Users/andrew/Develop/90_PLAYGROUND/AISwarm/.venv/bin/aiswarm run "Plan how a small team should handle disagreements about priorities." --max-rounds 8
```

Expected: a `library_retrieved` event in the run's `events.jsonl`; the run and cycle finish with no `step_error`; any proposals are deliberated; `aiswarm show <run>` prints the account and narrative; `community/norms.md` exists if anything was adopted.

- [ ] **Step 4: Mark the spec and commit**

Change the addendum's status to `- **Status:** Approved; Plans 2a and 2b implemented`.

```bash
git add docs/superpowers/specs/2026-10-10-aiswarm-postrun-design.md
git commit -m "docs: mark Plan 2b implemented"
```
