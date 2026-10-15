"""The JSON replies each post-run step asks for."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bikeshed.journal import JOURNAL_MAX
from bikeshed.norms import DIMENSION_NAME_MAX, NORM_MAX
from bikeshed.profiles import PRIVATE_MAX

Text = Annotated[str, Field(min_length=1)]
JournalText = Annotated[str, Field(min_length=1, max_length=JOURNAL_MAX)]
_KEEP_WORDS = frozenset({"", "null", "none"})


def _word(value: str) -> str:
    return value.strip().strip("\"'").strip().casefold()


def is_keep(value: object) -> bool:
    """A field the model means to leave as it is: missing, blank, or null/none written as a string."""
    return value is None or (isinstance(value, str) and _word(value) in _KEEP_WORDS)


def keeps_pronouns(value: object) -> bool:
    """Pronouns differ: an empty value means "rather not say", so only a missing value or "null" keeps them."""
    return value is None or (isinstance(value, str) and _word(value) == "null")


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JournalReply(_Reply):
    journal: JournalText


class DimensionRating(_Reply):
    dimension: Text
    rating: int = Field(ge=1, le=5)
    reason: Text


class ReviewReply(_Reply):
    more_of: Text
    less_of: Text
    private_note: Text
    evidence: list[int] = Field(min_length=1)
    overall: int = Field(ge=1, le=5)
    overall_reason: Text
    dimension_ratings: list[DimensionRating] = Field(default_factory=list)


class ResponseItem(_Reply):
    review_id: int
    stance: Literal["accept", "dispute"]
    reason: Text


class ResponsesReply(_Reply):
    responses: list[ResponseItem]


class PublicReply(_Reply):
    name: str | None = None
    pronouns: str | None = None
    headline: str | None = None
    introduction: str | None = None


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


class UpdateReply(_Reply):
    journal: JournalText
    lessons: str = Field(max_length=PRIVATE_MAX)
    self_description: str | None = None
    temperament: str | None = None
    peer_notes: str | None = None
    public_profile: PublicReply | None = None
    proposal: ProposalReply | None = None


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


class NarrativeReply(_Reply):
    narrative: JournalText
