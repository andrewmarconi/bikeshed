"""The JSON replies each post-run step asks for."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from aiswarm.journal import JOURNAL_MAX
from aiswarm.profiles import PRIVATE_MAX

Text = Annotated[str, Field(min_length=1)]
JournalText = Annotated[str, Field(min_length=1, max_length=JOURNAL_MAX)]


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JournalReply(_Reply):
    journal: JournalText


class ReviewReply(_Reply):
    more_of: Text
    less_of: Text
    private_note: Text
    evidence: list[int] = Field(min_length=1)
    overall: int = Field(ge=1, le=5)
    overall_reason: Text


class ResponseItem(_Reply):
    review_id: int
    stance: Literal["accept", "dispute"]
    reason: Text


class ResponsesReply(_Reply):
    responses: list[ResponseItem]


class PublicReply(_Reply):
    name: str
    pronouns: str | None = None
    headline: str
    introduction: str


class UpdateReply(_Reply):
    journal: JournalText
    lessons: str = Field(max_length=PRIVATE_MAX)
    self_description: str | None = None
    temperament: str | None = None
    peer_notes: str | None = None
    public_profile: PublicReply | None = None
