"""LLM judgments for the Observer: structured replies, an evidence rule, one correction, and a cache."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from aiswarm.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient, ModelError
from aiswarm.observer.store import Judgment, ObserverStore
from aiswarm.prompts import render


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


Check = Callable[[Any, dict[str, str]], str | None]


def _evidence(quotes: Callable[[Any], list[str]]) -> Check:
    def check(reply: Any, inputs: dict[str, str]) -> str | None:
        corpus = normalise("\n".join(inputs.values()))
        missing = [q for q in quotes(reply) if q and normalise(q) not in corpus]
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
    "self_image": Analysis("self_image", SelfImageReply, _evidence(
        lambda r: [c.evidence for c in r.changes] + [r.gap_evidence])),
    "benchmark": Analysis("benchmark", BenchmarkReply, _evidence(lambda r: list(r.evidence))),
    "codebook": Analysis("codebook", CodebookReply, _covers_labels),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Judge:
    def __init__(self, client: ModelClient, store: ObserverStore, model_alias: str):
        self.client = client
        self.store = store
        self.model_alias = model_alias

    async def run(self, analysis: Analysis, *, run_id: str, agent_id: str | None, inputs: dict[str, str]) -> Judgment:
        system = render(analysis.prompt_name)
        prompt_sha = _sha(system)
        key = _sha(json.dumps([analysis.name, run_id, agent_id, prompt_sha, self.model_alias, inputs],
                              sort_keys=True, ensure_ascii=False))
        if (hit := self.store.cached(key)) is not None:
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
