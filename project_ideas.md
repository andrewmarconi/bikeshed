# Project ideas

Ideas worth exploring, not yet specs. Each one needs a spec in `docs/superpowers/specs/` before it is built.

## A decision model as the Scorer

*Added 2026-10-13.*

The Scorer makes one bounded judgment: does a passing call's synthesis give the concrete answer the goal asks for? A decision model, which reads text and returns typed, calibrated answers instead of generating text, fits that judgment better than a chat model. It would also record a calibrated p(accept) for every ruling, which Sonnet does not give us.

### Jev (TypeSafe AI)

- Released 2026-09-15. Questions come in three types: Choice (up to 255 options, a probability per option), Score (2–10 ordered levels) and Noul (p(yes)).
- Hosted only, with no published weights. `POST https://api.typesafe.ai/v1/systemone` with a Bearer key; model `jev-1.13.0` (`jev-latest`). Also listed on OpenRouter as `typesafe/jev-1.13`.
- $0.042 per million input tokens, output free. 64k context per request; 32k for the state plus the longest question. Roughly 70–500 ms per request.
- Docs: <https://docs.typesafe.ai/models>; judge use: <https://www.braintrust.dev/articles/what-is-jev>

### Open-weight alternatives

None runs on oMLX as a Jev-style model. oMLX serves only llm, vlm, embedding and reranker models, and does not return logprobs from chat completions.

- **Laya** (`convaiinnovations/laya`, Apache-2.0): ModernBERT encoder decision models; `laya-serve` speaks Jev's `/v1/systemone`. Runs on PyTorch, not MLX. 512–1,024 tokens per question, near chance zero-shot, ships over-confident (per Laya's own site). <https://laya.studio/compare/jev-alternatives>
- **JevK5, Julia 1, TinyJev, OpenJev Verdict**: small Apache-licensed encoder/classifier models; benchmarks self-reported; each needs its own server.
- **Simple Jev** (Featherless AI) and **open-alternative-jev**: wrap an open LLM and read the next-token probabilities of the allowed answers. Need Transformers or vLLM for raw logits. <https://github.com/ikermoel/open-alternative-jev>
- **Qwen3-Reranker on oMLX** (`/v1/rerank`): the one Jev-like route oMLX serves. It scores "does the Document meet the requirements? yes/no" from the logits in one pass, which amounts to a Noul. The rerank API takes no custom instruction (oMLX uses its fixed web-search instruction), so the criterion would go in the query text: off-label, accuracy unknown.

### Problems to solve

- **Feedback.** A rejection must carry feedback (`scorer.py`, `Ruling`), and the agents read it (`scorer_feedback.md`, the post-run journal). A decision model writes no text. Options: a Choice over the rejection cases `prompts/scorer.md` lists, each mapped to a fixed sentence (must stay values-neutral, principle 3); or a hybrid where a text model writes feedback only on rejections.
- **Counting and arithmetic.** Jev's docs call it weak at both, and several goals hinge on them ("three songs", "$20", "a single basket").
- **Prompt injection.** The synthesis is written by the agents, and Jev "does not treat state as hostile by default".
- **Accuracy evidence is thin.** One third-party benchmark had Jev at 62.6% against Claude Haiku 4.5 at 81.3% on a single-question task.
- **Integration.** Neither Jev nor a reranker is OpenAI chat, so either needs a new backend kind or a Scorer-specific client, plus an accept threshold that provenance records.
- **Experimental record.** Changing the Scorer changes the agents' declared environment. Do it between runs only, note it in the run log, and have the Observer mark it.

### First step: shadow replay

Replay the past Sonnet rulings (8 as of 2026-10-13: 4 accepted, 4 rejected) offline against Jev and/or a Qwen3-Reranker on oMLX, comparing verdicts and probabilities with Sonnet's. Nothing reaches a run or an agent. Switch only if it agrees with Sonnet and handles the counting goals.
