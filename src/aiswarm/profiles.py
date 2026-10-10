"""Persistent agent profiles: private working files and a public self-presentation."""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from aiswarm.actions import summarise_validation
from aiswarm.db import now_iso
from aiswarm.gitrepo import GitRepo

AGENT_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
NAME_MAX = 40
PRONOUNS_MAX = 40
HEADLINE_MAX = 254
INTRO_MAX = 600
PRIVATE_FILES = ("temperament.md", "self.md", "lessons.md", "peer-notes.md")
PRIVATE_MAX = 4000
_GITIGNORE = "community.db\ncommunity.db-*\n"


class ProfileError(ValueError):
    pass


class PublicProfile(BaseModel):
    name: str
    pronouns: str = ""
    headline: str
    introduction: str
    former_names: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= NAME_MAX or not value.isprintable():
            raise ValueError(f"name must be 1-{NAME_MAX} printable characters")
        return value

    @field_validator("pronouns")
    @classmethod
    def _pronouns(cls, value: str) -> str:
        value = value.strip()
        if len(value) > PRONOUNS_MAX or not value.isprintable():
            raise ValueError(f"pronouns must be a single line of at most {PRONOUNS_MAX} characters")
        return value

    @field_validator("headline")
    @classmethod
    def _headline(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= HEADLINE_MAX or not value.isprintable():
            raise ValueError(f"headline must be a single line of 1-{HEADLINE_MAX} characters")
        return value

    @field_validator("introduction")
    @classmethod
    def _introduction(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= INTRO_MAX:
            raise ValueError(f"introduction must be 1-{INTRO_MAX} characters")
        return value


class AgentRecord(BaseModel):
    id: str
    model: str
    control: bool = False
    created_at: str


def _toml(data: dict[str, Any]) -> str:
    def value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(value(item) for item in v) + "]"
        return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings

    return "".join(f"{key} = {value(v)}\n" for key, v in data.items())


def _check_private(filename: str, content: str) -> None:
    if filename not in PRIVATE_FILES:
        raise ProfileError(f"{filename} is not a private profile file")
    if len(content) > PRIVATE_MAX:
        raise ProfileError(f"{filename} exceeds {PRIVATE_MAX} characters")
    if filename == "temperament.md" and not content.strip():
        raise ProfileError("temperament must not be empty")


class ProfileStore:
    def __init__(self, community_dir: Path):
        self.root = community_dir
        self.agents_dir = community_dir / "agents"
        self._repo = GitRepo.init(community_dir)
        gitignore = community_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE)
            self._repo.commit([".gitignore"], "ignore database", author="aiswarm")

    def _dir(self, agent_id: str) -> Path:
        return self.agents_dir / agent_id

    def ids(self) -> list[str]:
        if not self.agents_dir.exists():
            return []
        return sorted(p.name for p in self.agents_dir.iterdir() if (p / "agent.toml").exists())

    def create(self, agent_id: str, model: str, temperament: str, control: bool = False) -> AgentRecord:
        if not AGENT_ID.match(agent_id):
            raise ProfileError("agent id must be lowercase letters, digits or '-', starting with a letter, max 24")
        if agent_id in self.ids():
            raise ProfileError(f"agent {agent_id!r} already exists")
        _check_private("temperament.md", temperament)
        directory = self._dir(agent_id)
        directory.mkdir(parents=True)
        record = AgentRecord(id=agent_id, model=model, control=control, created_at=now_iso())
        (directory / "agent.toml").write_text(_toml(record.model_dump()))
        (directory / "temperament.md").write_text(temperament.strip() + "\n")
        for filename in PRIVATE_FILES[1:]:
            (directory / filename).write_text("")
        self._repo.commit([f"agents/{agent_id}"], f"create agent {agent_id}", author="aiswarm")
        return record

    def record(self, agent_id: str) -> AgentRecord:
        return AgentRecord.model_validate(tomllib.loads((self._dir(agent_id) / "agent.toml").read_text()))

    def private(self, agent_id: str) -> dict[str, str]:
        return {name: (self._dir(agent_id) / name).read_text() for name in PRIVATE_FILES}

    def write_private(self, agent_id: str, filename: str, content: str) -> None:
        _check_private(filename, content)
        (self._dir(agent_id) / filename).write_text(content)
        self._repo.commit([f"agents/{agent_id}/{filename}"], f"{agent_id} updated {filename}", author=agent_id)

    def write_journal_file(self, agent_id: str, run_id: str, content: str) -> None:
        path = self._dir(agent_id) / "journal" / f"{run_id}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)
        self._repo.commit([f"agents/{agent_id}/journal/{run_id}.md"], f"{agent_id} journal for {run_id}", author=agent_id)

    def journals(self, agent_id: str, limit: int) -> list[tuple[str, str]]:
        """The latest `limit` journal files as (run_id, text), oldest first. Run ids sort by time."""
        directory = self._dir(agent_id) / "journal"
        if not directory.exists() or limit <= 0:
            return []
        paths = sorted(directory.glob("*.md"), key=lambda p: p.stem)[-limit:]
        return [(path.stem, path.read_text()) for path in paths]

    def write_community_file(self, filename: str, content: str, message: str) -> None:
        (self.root / filename).write_text(content)
        self._repo.commit([filename], message, author="community")

    def public(self, agent_id: str) -> PublicProfile | None:
        path = self._dir(agent_id) / "public.toml"
        if not path.exists():
            return None
        return PublicProfile.model_validate(tomllib.loads(path.read_text()))

    def set_public(
        self, agent_id: str, name: str, headline: str, introduction: str, pronouns: str | None = None
    ) -> PublicProfile:
        """Validate and save a public profile. `pronouns=None` keeps the current pronouns."""
        current = self.public(agent_id)
        former = list(current.former_names) if current else []
        if pronouns is None:
            pronouns = current.pronouns if current else ""
        try:
            candidate = PublicProfile(name=name, pronouns=pronouns, headline=headline, introduction=introduction,
                                      former_names=former)
        except ValidationError as exc:
            raise ProfileError(summarise_validation(exc)) from exc
        for other in self.ids():
            other_profile = self.public(other) if other != agent_id else None
            if other_profile and other_profile.name.casefold() == candidate.name.casefold():
                raise ProfileError(f"the name {candidate.name!r} is taken")
        if current and current.name != candidate.name:
            candidate.former_names.append(current.name)
        (self._dir(agent_id) / "public.toml").write_text(_toml(candidate.model_dump()))
        self._repo.commit([f"agents/{agent_id}/public.toml"], f"{agent_id} updated public profile", author=agent_id)
        return candidate
