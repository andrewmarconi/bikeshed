import json
import subprocess

import pytest

from aiswarm.introduce import introduce
from aiswarm.models.fake import FakeModel
from aiswarm.profiles import PRIVATE_MAX, ProfileError, ProfileStore


@pytest.fixture
def store(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("a", "local", "Curious and blunt.")
    s.create("b", "claude", "Patient.")
    return s


def test_create_writes_private_files_and_commits(store):
    assert store.ids() == ["a", "b"]
    private = store.private("a")
    assert private["temperament.md"].strip() == "Curious and blunt."
    assert private["self.md"] == private["lessons.md"] == private["peer-notes.md"] == ""
    assert store.record("a").model == "local"
    assert store.record("a").control is False
    log = subprocess.run(["git", "log", "--format=%s"], cwd=store.root, capture_output=True, text=True).stdout
    assert "create agent a" in log
    assert "community.db" in (store.root / ".gitignore").read_text()


@pytest.mark.parametrize("agent_id", ["A", "1x", "has space", "x" * 25, ""])
def test_create_rejects_bad_ids(store, agent_id):
    with pytest.raises(ProfileError):
        store.create(agent_id, "local", "t")


def test_create_rejects_duplicates_and_empty_temperament(store):
    with pytest.raises(ProfileError):
        store.create("a", "local", "again")
    with pytest.raises(ProfileError):
        store.create("c", "local", "   ")


def test_set_public_happy_path(store):
    profile = store.set_public("a", "Wren", "Asks the awkward question.", "I like to start from first principles.")
    assert store.public("a") == profile
    assert profile.former_names == []


@pytest.mark.parametrize("name,headline,intro", [
    ("", "h", "i"),
    ("x" * 41, "h", "i"),
    ("Wren", "x" * 255, "i"),
    ("Wren", "two\nlines", "i"),
    ("Wren", "h", ""),
    ("Wren", "h", "x" * 601),
    ("bad\tname", "h", "i"),
])
def test_set_public_enforces_limits(store, name, headline, intro):
    with pytest.raises(ProfileError):
        store.set_public("a", name, headline, intro)
    assert store.public("a") is None


def test_names_are_unique_case_insensitively(store):
    store.set_public("a", "Wren", "h", "i")
    with pytest.raises(ProfileError, match="taken"):
        store.set_public("b", "wREN", "h", "i")


def test_rename_keeps_public_history(store):
    store.set_public("a", "Wren", "h", "i")
    store.set_public("a", "Kestrel", "h2", "i2")
    assert store.public("a").former_names == ["Wren"]
    store.set_public("a", "Kestrel", "h3", "i3")
    assert store.public("a").former_names == ["Wren"]


def test_unicode_name_round_trips(store):
    store.set_public("a", "Zoë 🌿", "Ça va", "Héllo")
    assert store.public("a").name == "Zoë 🌿"


def test_write_private_validates_and_commits(store):
    store.write_private("a", "lessons.md", "Start small.")
    assert store.private("a")["lessons.md"] == "Start small."
    with pytest.raises(ProfileError):
        store.write_private("a", "lessons.md", "x" * (PRIVATE_MAX + 1))
    with pytest.raises(ProfileError):
        store.write_private("a", "journal.md", "nope")
    assert store.private("a")["lessons.md"] == "Start small."


def intro(name, headline="A headline.", introduction="Hello, community."):
    return json.dumps({"name": name, "headline": headline, "introduction": introduction})


async def test_introduce_sets_profile(store):
    model = FakeModel([intro("Wren")])
    profile = await introduce(store, "a", model)
    assert profile.name == "Wren"
    system, messages, schema = model.calls[0]
    assert "Curious and blunt." in system
    assert "introduce yourself" in messages[0]["content"].lower()
    assert schema["required"] == ["name", "headline", "introduction"]


async def test_introduce_retries_once_with_the_error(store):
    model = FakeModel([intro("Wren", headline="x" * 300), intro("Wren")])
    assert (await introduce(store, "a", model)).name == "Wren"
    assert "could not be accepted" in model.calls[1][1][-1]["content"]


async def test_introduce_gives_up_after_two_invalid_replies(store):
    with pytest.raises(ProfileError):
        await introduce(store, "a", FakeModel(["garbage", "more garbage"]))
    assert store.public("a") is None
