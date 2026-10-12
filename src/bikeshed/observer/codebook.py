"""Open coding: theme labels in the judge's own words, merged into a codebook that grows with the community."""

from collections import Counter

from bikeshed.observer.judge import ANALYSES, Judge
from bikeshed.observer.store import Judgment, ObserverStore


async def update_codebook(judge: Judge, store: ObserverStore, run_id: str, labels: list[str]) -> Judgment | None:
    new = sorted({label for label in labels if store.code_for(label) is None})
    if not new:
        return None
    listing = "\n".join(f"- {name}: {description}" for name, description in store.codes()) or "(empty)"
    judgment = await judge.run(ANALYSES["codebook"], run_id=run_id, agent_id=None,
                               inputs={"codebook": listing, "new labels": "\n".join(new)})
    if judgment.status == "ok" and judgment.result:
        for a in judgment.result["assignments"]:
            store.assign(a["label"], a["code"], a["description"])
    return judgment


def theme_counts(store: ObserverStore, judgments: list[Judgment]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for j in judgments:
        if j.analysis != "themes" or j.status != "ok" or not j.result or j.agent_id is None:
            continue
        for theme in j.result["themes"]:
            counts.setdefault(j.agent_id, Counter())[store.code_for(theme["label"]) or theme["label"]] += 1
    return counts
