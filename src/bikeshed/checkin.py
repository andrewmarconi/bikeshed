"""A profile check-in: an existing agent may change its name and states its pronouns."""

from pydantic import BaseModel, ConfigDict, ValidationError

from bikeshed.actions import ActionError, correction_request, extract_json, simplify_schema, summarise_validation
from bikeshed.models.base import Message, ModelClient
from bikeshed.profiles import NAME_MAX, PRONOUNS_MAX, ProfileError, ProfileStore, PublicProfile
from bikeshed.prompts import render


class _CheckinReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    pronouns: str


CHECKIN_SCHEMA = simplify_schema(_CheckinReply.model_json_schema())


async def checkin(store: ProfileStore, agent_id: str, client: ModelClient) -> PublicProfile:
    current = store.public(agent_id)
    if current is None:
        raise ProfileError(f"{agent_id} has no public profile yet; introduce it first")
    system = render("checkin_system.md", display=f"{current.name} ({agent_id})",
                    temperament=store.private(agent_id)["temperament.md"].strip())
    messages: list[Message] = [{"role": "user", "content": render(
        "checkin_request.md", current_name=current.name, pronouns=current.pronouns or "(not stated)",
        headline=current.headline, introduction=current.introduction, name_max=NAME_MAX, pronouns_max=PRONOUNS_MAX,
    )}]
    for _ in range(2):
        result = await client.chat(system, messages, CHECKIN_SCHEMA)
        try:
            reply = _CheckinReply.model_validate(extract_json(result.text))
            return store.set_public(agent_id, reply.name, current.headline, current.introduction,
                                    pronouns=reply.pronouns)
        except ValidationError as exc:
            problem = summarise_validation(exc)
        except (ProfileError, ActionError) as exc:
            problem = str(exc)
        messages += [
            {"role": "assistant", "content": result.text},
            {"role": "user", "content": correction_request(problem)},
        ]
    raise ProfileError(f"{agent_id} did not produce a valid check-in")
