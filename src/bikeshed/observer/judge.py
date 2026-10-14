"""LLM judgments for the Observer: structured replies, an evidence rule, one correction, and a cache."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from bikeshed.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from bikeshed.models.base import Message, ModelClient, ModelError
from bikeshed.observer.store import Judgment, ObserverStore
from bikeshed.prompts import render


class Theme(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    polarity: Literal["expressed", "rejected", "ambivalent"]
    evidence: str = Field(min_length=1)
    source: str


class ThemesReply(BaseModel):
    themes: list[Theme] = Field(min_length=1, max_length=8)  # the prompt asks for 3-8; sparse input may give fewer


class ColleagueFinding(BaseModel):
    colleague: str
    verdict: Literal["consistent", "softened", "harsher", "contradictory"]
    journal_quote: str = Field(min_length=1)
    review_quote: str = Field(min_length=1)


class PrivatePublicReply(BaseModel):
    findings: list[ColleagueFinding]


class ThinkActPoint(BaseModel):
    relation: Literal["agrees", "diverges"]
    about: str = Field(min_length=1, max_length=120)
    think_quote: str = Field(min_length=1)
    action_quote: str = Field(min_length=1)


class ThinkActReply(BaseModel):
    points: list[ThinkActPoint] = Field(max_length=12)


class Change(BaseModel):
    what: str
    evidence: str = Field(min_length=1)


class SelfImageReply(BaseModel):
    changes: list[Change]
    private_public_gap: Literal["none", "some", "large"]
    gap_evidence: str = ""


class BenchmarkReply(BaseModel):
    trend: Literal["first", "converging", "diverging", "new direction"]
    optimises_for: str
    evidence: list[str]


class CodeAssignment(BaseModel):
    label: str
    code: str = Field(min_length=1, max_length=60)
    description: str


class CodebookReply(BaseModel):
    assignments: list[CodeAssignment]


def normalise(text: str) -> str:
    return " ".join(text.split())


ELLIPSIS = re.compile(r"\.\.\.|…")


def quoted_in(quote: str, text: str) -> bool:
    """Whether `quote` is verbatim in `text`. A quote abridged with an ellipsis passes when every piece appears
    verbatim, in order."""
    pieces = [p for p in (normalise(piece) for piece in ELLIPSIS.split(quote)) if p]
    corpus, start = normalise(text), 0
    for piece in pieces:
        if (found := corpus.find(piece, start)) < 0:
            return False
        start = found + len(piece)
    return bool(pieces)


Check = Callable[[Any, dict[str, str]], str | None]


def _evidence(quotes: Callable[[Any], list[str]]) -> Check:
    def check(reply: Any, inputs: dict[str, str]) -> str | None:
        corpus = "\n".join(inputs.values())
        missing = [q for q in quotes(reply) if q and not quoted_in(q, corpus)]
        if missing:
            return "these quotes do not appear verbatim in the input: " + "; ".join(repr(q) for q in missing[:5])
        return None
    return check


def _covers_labels(reply: Any, inputs: dict[str, str]) -> str | None:
    wanted = [line for line in inputs["new labels"].splitlines() if line.strip()]
    given = [a.label for a in reply.assignments]
    if sorted(given) != sorted(wanted):
        return f"assign every new label exactly once, spelled as given: {', '.join(wanted)}"
    return None


def _think_and_act(reply: Any, inputs: dict[str, str]) -> str | None:
    """Each side is quoted from its own sections; a think note passed off as an action fails."""
    thought = inputs["think notes"]
    done = inputs["public actions"] + "\n" + inputs["private messages sent"]
    missing = [p.think_quote for p in reply.points if not quoted_in(p.think_quote, thought)]
    missing += [p.action_quote for p in reply.points if not quoted_in(p.action_quote, done)]
    if missing:
        return ("each think_quote must be copied from the think notes and each action_quote from the public actions "
                "or private messages sent; these are not found verbatim there: " + "; ".join(repr(q) for q in missing[:5]))
    return None


class FactFinding(BaseModel):
    fact: str
    disclosed: bool
    quote: str = ""  # the member's own words conveying the fact
    relay_quote: str = ""  # another member's words passing it on, if any


class DisclosureReply(BaseModel):
    facts: list[FactFinding]


class Fabricated(BaseModel):
    where: Literal["opening proposals", "final synthesis"]
    quote: str = Field(min_length=1)
    what: str = Field(min_length=1, max_length=160)


class FabricationReply(BaseModel):
    items: list[Fabricated] = Field(max_length=30)


def _disclosure(reply: Any, inputs: dict[str, str]) -> str | None:
    wanted = [line.split(":", 1)[0] for line in inputs["private facts"].splitlines() if line.strip()]
    if sorted(f.fact for f in reply.facts) != sorted(wanted):
        return f"give exactly one finding for each fact id: {', '.join(wanted)}"
    own, others = inputs["this member's entries"], inputs["other members' entries"]
    bad = [f.quote for f in reply.facts if f.disclosed and not quoted_in(f.quote, own)]
    bad += [f.relay_quote for f in reply.facts if f.relay_quote and not quoted_in(f.relay_quote, others)]
    if bad:
        return ("each quote must come from this member's entries and each relay_quote from other members' entries; "
                "not found verbatim: " + "; ".join(repr(q) for q in bad[:5]))
    return None


def _fabrication(reply: Any, inputs: dict[str, str]) -> str | None:
    bad = [i.quote for i in reply.items if not quoted_in(i.quote, inputs[i.where])]
    if bad:
        return "each quote must come from the section named in where; not found verbatim: " + \
            "; ".join(repr(q) for q in bad[:5])
    return None


@dataclass(frozen=True)
class Analysis:
    name: str
    reply: type[BaseModel]
    check: Check

    @property
    def prompt_name(self) -> str:
        return f"observer/{self.name}.md"


ANALYSES: dict[str, Analysis] = {
    "themes": Analysis("themes", ThemesReply, _evidence(lambda r: [t.evidence for t in r.themes])),
    "private_public": Analysis("private_public", PrivatePublicReply, _evidence(
        lambda r: [q for f in r.findings for q in (f.journal_quote, f.review_quote)])),
    "think_vs_act": Analysis("think_vs_act", ThinkActReply, _think_and_act),
    "self_image": Analysis("self_image", SelfImageReply, _evidence(
        lambda r: [c.evidence for c in r.changes] + [r.gap_evidence])),
    "benchmark": Analysis("benchmark", BenchmarkReply, _evidence(lambda r: list(r.evidence))),
    "codebook": Analysis("codebook", CodebookReply, _covers_labels),
    "disclosure": Analysis("disclosure", DisclosureReply, _disclosure),
    "fabrication": Analysis("fabrication", FabricationReply, _fabrication),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Judge:
    def __init__(self, client: ModelClient, store: ObserverStore, model_alias: str, *, retry_failed: bool = True):
        self.client = client
        self.store = store
        self.model_alias = model_alias
        self.retry_failed = retry_failed  # False: a stored failure stands, like a cached judgment

    def key(self, analysis: Analysis, *, run_id: str, agent_id: str | None, inputs: dict[str, str]) -> str:
        """The cache key: the same analysis, inputs, prompt and model give the same judgment."""
        return _sha(json.dumps([analysis.name, run_id, agent_id, _sha(render(analysis.prompt_name)), self.model_alias,
                                inputs], sort_keys=True, ensure_ascii=False))

    def cached(self, analysis: Analysis, *, run_id: str, agent_id: str | None,
               inputs: dict[str, str]) -> Judgment | None:
        return self.store.cached(self.key(analysis, run_id=run_id, agent_id=agent_id, inputs=inputs),
                                 include_failed=not self.retry_failed)

    def is_cached(self, analysis: Analysis, *, run_id: str, agent_id: str | None, inputs: dict[str, str]) -> bool:
        return self.cached(analysis, run_id=run_id, agent_id=agent_id, inputs=inputs) is not None

    async def run(self, analysis: Analysis, *, run_id: str, agent_id: str | None, inputs: dict[str, str]) -> Judgment:
        system = render(analysis.prompt_name)
        prompt_sha = _sha(system)
        key = self.key(analysis, run_id=run_id, agent_id=agent_id, inputs=inputs)
        if (hit := self.store.cached(key, include_failed=not self.retry_failed)) is not None:
            return hit

        def done(status: str, result: dict[str, Any] | None, error: str | None, reported: str | None) -> Judgment:
            judgment = Judgment(analysis.name, run_id, agent_id, status, result, error, reported, prompt_sha)
            self.store.save(key, judgment, self.model_alias)
            return judgment

        user = "\n\n".join(f"## {name}\n\n{text.strip() or '(none)'}" for name, text in inputs.items())
        messages: list[Message] = [{"role": "user", "content": user + "\n\nReply with JSON only."}]
        schema = simplify_schema(analysis.reply.model_json_schema())
        reported: str | None = None
        problem = "no reply"
        for _ in range(2):
            try:
                result = await self.client.chat(system, messages, schema)
            except ModelError as exc:
                return done("failed", None, f"model error: {exc}", reported)
            reported = result.model or reported
            try:
                reply = analysis.reply.model_validate(extract_json(result.text))
                failure = analysis.check(reply, inputs)
                if failure is None:
                    return done("ok", reply.model_dump(mode="json"), None, reported)
                problem = failure
            except ValidationError as exc:
                problem = summarise_validation(exc)
            except ActionError as exc:
                problem = str(exc)
            messages += [{"role": "assistant", "content": result.text},
                         {"role": "user", "content": correction_request(problem)}]
        return done("failed", None, problem, reported)
