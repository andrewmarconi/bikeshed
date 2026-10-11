"""An agent's first introduction: it writes its own public profile."""

from pydantic import BaseModel, ConfigDict, ValidationError

from aiswarm.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from aiswarm.models.base import Message, ModelClient
from aiswarm.profiles import HEADLINE_MAX, INTRO_MAX, NAME_MAX, PRONOUNS_MAX, ProfileError, ProfileStore, PublicProfile
from aiswarm.prompts import render


class _IntroReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    pronouns: str = ""
    headline: str
    introduction: str


INTRO_SCHEMA = simplify_schema(_IntroReply.model_json_schema())


async def introduce(store: ProfileStore, agent_id: str, client: ModelClient) -> PublicProfile:
    system = render("introduction_system.md", temperament=store.private(agent_id)["temperament.md"].strip())
    messages: list[Message] = [{"role": "user", "content": render(
        "introduction_request.md", name_max=NAME_MAX, pronouns_max=PRONOUNS_MAX, headline_max=HEADLINE_MAX,
        intro_max=INTRO_MAX,
    )}]
    for attempt in range(2):
        result = await client.chat(system, messages, INTRO_SCHEMA)
        try:
            reply = _IntroReply.model_validate(extract_json(result.text))
            profile = store.set_public(agent_id, reply.name, reply.headline, reply.introduction, pronouns=reply.pronouns)
            if store.record(agent_id).control and not store.has_seed_public(agent_id):
                store.save_seed_public(agent_id)  # the seed is the first introduction only
            return profile
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except (ProfileError, ActionError) as exc:
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": correction_request(problem)},
        ]
    raise ProfileError(f"{agent_id} did not produce a valid introduction")
