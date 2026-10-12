import json

from bikeshed.checkin import checkin
from bikeshed.models.fake import FakeModel


async def test_checkin_can_rename_and_records_pronouns(community):
    store, conn = community
    model = FakeModel([json.dumps({"name": "Alder", "pronouns": "he/him"})])
    profile = await checkin(store, "a", model)
    assert (profile.name, profile.pronouns, profile.former_names) == ("Alder", "he/him", ["Alpha"])
    assert profile.headline == "Alpha headline" and profile.introduction == "I am Alpha."
    system, messages, _ = model.calls[0]
    assert "Temperament of a." in system
    request = messages[0]["content"]
    assert "- name: Alpha" in request and "- pronouns: (not stated)" in request
    assert "You may change your name" in request
    assert "What pronouns would you like others to use for you?" in request


async def test_checkin_keeping_the_name_does_not_record_a_rename(community):
    store, conn = community
    profile = await checkin(store, "a", FakeModel([json.dumps({"name": "Alpha", "pronouns": ""})]))
    assert (profile.name, profile.pronouns, profile.former_names) == ("Alpha", "", [])


async def test_checkin_retries_once_when_the_name_is_taken(community):
    store, conn = community
    model = FakeModel([json.dumps({"name": "bea", "pronouns": "x"}), json.dumps({"name": "Ash", "pronouns": "x"})])
    assert (await checkin(store, "a", model)).name == "Ash"
    assert "is taken" in model.calls[1][1][-1]["content"]
