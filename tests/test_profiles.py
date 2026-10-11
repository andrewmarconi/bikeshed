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


@pytest.mark.parametrize("name,headline", [("Wren (a)", "h"), ("Wren", "The Wren (A) - Wren (a)")])
def test_set_public_rejects_the_agents_own_id_marker(store, name, headline):
    with pytest.raises(ProfileError, match=r"your id \(a\) is shown next to your name automatically; leave it out"):
        store.set_public("a", name, headline, "i")
    assert store.public("a") is None
    assert store.set_public("a", "Wren (b)", "Mentions (b)", "I am Wren (a).").name == "Wren (b)"


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


def test_pronouns_are_optional_and_kept_unless_given(store):
    assert store.set_public("a", "Wren", "h", "i").pronouns == ""
    assert store.set_public("a", "Wren", "h", "i", pronouns=" they/them ").pronouns == "they/them"
    assert store.set_public("a", "Wren", "h2", "i").pronouns == "they/them"          # None keeps the current value
    assert store.set_public("a", "Wren", "h2", "i", pronouns="").pronouns == ""      # empty clears it
    assert store.public("a").pronouns == ""


@pytest.mark.parametrize("pronouns", ["x" * 41, "she\nher"])
def test_pronouns_are_validated(store, pronouns):
    with pytest.raises(ProfileError):
        store.set_public("a", "Wren", "h", "i", pronouns=pronouns)


async def test_introduce_asks_for_pronouns(store):
    reply = json.dumps({"name": "Wren", "pronouns": "it/its", "headline": "h", "introduction": "i"})
    model = FakeModel([reply])
    assert (await introduce(store, "a", model)).pronouns == "it/its"
    assert "What pronouns would you like others to use for you?" in model.calls[0][1][0]["content"]


@pytest.fixture
def control_store(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("a", "local", "Curious and blunt.")
    s.set_public("a", "Ash", "Ash headline", "I am Ash.")
    s.create("e", "local", "Seed temperament.", control=True)
    s.set_public("e", "Eve", "Eve headline", "I am Eve.")
    s.save_seed_public("e")
    return s


def test_control_agents_keep_a_seed(control_store):
    seed = control_store.root / "agents" / "e" / "seed"
    assert (seed / "temperament.md").read_text() == "Seed temperament.\n"
    assert 'name = "Eve"' in (seed / "public.toml").read_text()
    assert control_store.has_seed_public("e") and not control_store.has_seed_public("a")
    assert not (control_store.root / "agents" / "a" / "seed").exists()


def test_reset_restores_the_seed_and_empties_notes(control_store):
    control_store.write_private("e", "temperament.md", "Changed.")
    control_store.write_private("e", "lessons.md", "Learned things.")
    control_store.set_public("e", "Evelyn", "New headline", "New intro.")
    assert control_store.reset_to_seed("e") is True
    private = control_store.private("e")
    assert private["temperament.md"] == "Seed temperament.\n"
    assert private["self.md"] == private["lessons.md"] == private["peer-notes.md"] == ""
    public = control_store.public("e")
    assert (public.name, public.headline, public.former_names) == ("Eve", "Eve headline", [])
    log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=control_store.root, capture_output=True, text=True)
    assert log.stdout.strip() == "reset control agent e to seed"


def test_reset_keeps_the_current_name_when_the_seed_name_is_taken(control_store):
    control_store.set_public("e", "Evelyn", "Eve headline", "I am Eve.")
    control_store.set_public("a", "Eve", "Ash headline", "I am Ash.")
    assert control_store.reset_to_seed("e") is False
    assert control_store.public("e").name == "Evelyn"


def test_reset_without_a_seed_profile_fails(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    with pytest.raises(ProfileError, match="no seed profile"):
        s.reset_to_seed("e")


async def test_introduce_saves_the_seed_for_control_agents(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    s.create("f", "local", "Seed.")
    reply = json.dumps({"name": "Eve", "headline": "H", "introduction": "Hi."})
    await introduce(s, "e", FakeModel([reply]))
    await introduce(s, "f", FakeModel([reply.replace("Eve", "Fay")]))
    assert s.has_seed_public("e") and not s.has_seed_public("f")


async def test_reintroducing_a_control_agent_keeps_its_first_seed(tmp_path):
    s = ProfileStore(tmp_path / "community")
    s.create("e", "local", "Seed.", control=True)
    await introduce(s, "e", FakeModel([json.dumps({"name": "Eve", "headline": "H", "introduction": "Hi."})]))
    await introduce(s, "e", FakeModel([json.dumps({"name": "Eden", "headline": "H2", "introduction": "Hello."})]))
    assert 'name = "Eve"' in (s.root / "agents" / "e" / "seed" / "public.toml").read_text()


def test_set_model_updates_the_record_and_commits(store):
    store.set_model("a", "small")
    assert store.record("a").model == "small"
    assert any(c.subject == "set agent a model to small" for c in store.history("a"))


def test_set_model_rejects_an_unknown_agent(store):
    with pytest.raises(ProfileError, match="no agent"):
        store.set_model("ghost", "small")


def test_file_at_and_first_version(store):
    import time
    from datetime import UTC, datetime, timedelta

    before_edit = datetime.now(UTC) + timedelta(seconds=1)
    time.sleep(2)
    store.write_private("a", "self.md", "Later self.")
    assert store.file_at("a", "self.md", before_edit) == ""
    assert store.file_at("a", "self.md", None) == "Later self."
    assert store.first_version("a", "temperament.md") == "Curious and blunt."
    assert store.file_at("a", "nope.md", None) is None
    assert store.first_version("a", "nope.md") is None
