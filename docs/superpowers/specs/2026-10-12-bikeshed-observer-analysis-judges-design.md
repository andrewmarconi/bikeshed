# Observer: a judge per analysis

Amends `2026-10-10-aiswarm-observer-design.md` (§3 config, §6 cache).

## Why

On the first five lab runs every `think_vs_act` judgment failed with the same error from `claude -p`: Sonnet's safeguards flagged the request (`[reasoning_extraction]`). The prompt hands the judge another model's "private think notes … written to itself before each of its turns" and asks how they compare with what the agent did. In Bikeshed those notes are ordinary Process 2 board entries, but the request reads like an attempt to extract hidden reasoning, and the refusal repeats on every retry. The other analyses on the same runs judged normally.

## Change

`[observer.judges]` maps an analysis name to a backend that judges that analysis instead of `[observer] judge`:

```toml
[observer]
judge = "claude-judge"

[observer.judges]
think_vs_act = "qwen35b"
```

- Keys are analysis names (`themes`, `private_public`, `think_vs_act`, `self_image`, `benchmark`, `codebook`). An unknown name fails `observe` with the list of known ones. Values must name a backend in `[models]`, checked when the config loads.
- `[observer.judges]` needs `[observer] judge`: it overrides the default; it does not replace it.
- `--no-judge` turns off every judge, overrides included.
- Each judge has its own concurrency limit, so a slow local judge does not hold up the default one.
- The cache key already includes the judge's model alias, so moving an analysis to another judge re-judges it, and moving it back reuses nothing stale.
- When a report's judgments come from more than one model, the footer names the analyses each model judged, e.g. `_Judged by Qwen3.6-35B-A3B-4bit (think_vs_act), claude-sonnet-… (benchmark, codebook, …)_`. A report judged by one model reads as before.

Judgments from different models are not directly comparable. The footer makes the split visible; comparing across a switch is the reader's job, as it already is when the default judge changes.

## Tests

- Config: an override loads; an unknown backend or a missing default judge fails validation.
- Observe: the overridden analysis goes only to its own client and the rest only to the default; an unknown analysis name and a missing client both raise `ValueError`; a mixed report names the analyses per model.
