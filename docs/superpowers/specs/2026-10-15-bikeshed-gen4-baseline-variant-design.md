# Generation 4, sub-project D2: the model-variant baseline

*2026-10-15. Extends sub-project D (`2026-10-15-bikeshed-gen4-baseline-run-design.md`, merged as `f22ef47`). Motivated by a probe of one agent's turn in generation 4's first run; its write-up stays outside this repo until it's reviewed.*

## Goal

A baseline holds the model fixed and removes the culture. A **variant** holds the culture out the same way and changes one thing: every agent runs on another backend. Run 1's room against its baseline gives what the culture adds; its baseline against a variant on `qwen3-14b` gives what reasoning adds, with the same weights, seed, settings and brief deal.

A variant is never read as a plain baseline. It's labelled as a variant in its directory name, in `baseline.toml` and everywhere the Observer prints it.

## The command

- `bikeshed baseline <run-id> --backend <name>` mirrors a closed run as D does, with every agent on backend `<name>`.
- `bikeshed baseline --resume <id>` continues a variant as it continues a baseline. The backend comes from `baseline.toml`.

**Refusals**, each through `_fail`, on top of D's:

- `<name>` isn't a backend in the config;
- `--backend` together with `--resume` (the variant's backend is already recorded);
- `<name>` is the backend every agent used in the source run: that's a plain baseline, so drop `--backend`.

## What changes from a baseline

- **Directory:** `lab/baselines/<run-id>-v<k>/`, with `k` counting up from 1 among that run's variants. Baselines keep `-b<k>`.
- **`baseline.toml`** gains `backend = "<name>"`. A file without it is a plain baseline.
- **Agents:** each one is created in the scratch store on `<name>` instead of its source backend. Temperaments, fresh introductions, seed, settings and brief rows are as in D. Introductions use `<name>` too.
- **Backend check:** the per-agent "same backend, same settings" check (`check_backends`) is skipped for agents, since the backend is the point of the variant. The Scorer check stays as it is: the Scorer must be the one the source run had, or rulings can't be compared.
- **Provenance:** recorded as for any run, so the scratch store's start row names `<name>` for every agent.

Nothing else differs: no norms, library, history, post-run or control, and nothing written under `lab/community/` or `lab/runs/`.

## Observer

`observer/baselines.py` reads `backend` from `baseline.toml` into a new `Baseline.backend: str | None` (None for a plain baseline).

- **"## Baseline"** in the source run's report gains a "Model" column: "as run" for a baseline, the backend name for a variant.
- **"## Baseline vs community"** labels each block "baseline" or "variant (`<name>`)", in the ending/rounds/answer lines and in the action-mix rows.

## Testing

TDD with `FakeModel` and temporary directories, as in D.

- `--backend` creates `<run-id>-v1`, writes `backend` to `baseline.toml`, and every agent in the scratch store has that backend; the provenance start row names it.
- Introductions are made through the variant backend's client.
- A second variant of the same run gets `-v2`; a baseline after it still gets `-b1`.
- A variant runs when a source backend has since changed or left the config; a plain baseline in the same state is still refused.
- The Scorer check still refuses a changed Scorer.
- Refusals: unknown backend; `--backend` with `--resume`; a backend that matches every source agent's.
- `--resume` of a paused variant keeps its backend.
- Isolation: the generation's `community/` and `runs/` are unchanged after a variant.
- Observer: a variant gets the "Model" column value and the "variant (`<name>`)" label; a plain baseline reads "as run" and "baseline"; a `baseline.toml` without `backend` (from D) is read as a plain baseline.

## First use

Once merged: `bikeshed baseline 20261015-173944-5f14` (the plain baseline) and `bikeshed baseline 20261015-173944-5f14 --backend qwen3-14b` (the variant), one after the other, outside any community run.
