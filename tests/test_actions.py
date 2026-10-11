import json

import pytest

from aiswarm.actions import (
    ActionError, CallConsensus, Critique, ListFiles, Pass, Propose, extract_json, parse_action,
    simplify_schema, step_schema, target_refs,
)


def test_parses_enveloped_action():
    action = parse_action('{"action": {"type": "propose", "answer_summary": "A", "method": "M"}}')
    assert isinstance(action, Propose) and action.method == "M"


def test_accepts_bare_action_object():
    assert isinstance(parse_action('{"type": "pass"}'), Pass)


def test_strips_code_fence():
    assert isinstance(parse_action('```json\n{"action": {"type": "pass"}}\n```'), Pass)


def test_extracts_json_after_prose():
    assert isinstance(parse_action('Sure! {"action": {"type": "list_files"}} Hope that helps.'), ListFiles)


def test_extract_json_without_object_raises():
    with pytest.raises(ActionError):
        extract_json("no json here")


@pytest.mark.parametrize("text", [
    '{"action": {"type": "dance"}}',
    '{"action": {"type": "propose", "answer_summary": "A"}}',
    '{"action": {"type": "pass", "extra": 1}}',
    '{"action": {"type": "critique", "target": 1, "about": "answer", "points": ""}}',
    '{"action": {"type": "call_consensus", "synthesis": "S", "refs": []}}',
    '{"action": {"type": "write_file", "path": "a", "base_version": -1, "content": "", "note": "n"}}',
])
def test_rejects_invalid_actions(text):
    with pytest.raises(ActionError):
        parse_action(text)


def test_error_message_names_the_field():
    with pytest.raises(ActionError, match="method"):
        parse_action('{"action": {"type": "propose", "answer_summary": "A"}}')


def test_call_consensus_accepts_empty_credits():
    action = parse_action('{"action": {"type": "call_consensus", "synthesis": "S", "refs": [2], "credits": []}}')
    assert isinstance(action, CallConsensus) and action.credits == []


def test_target_refs():
    assert target_refs(Critique(type="critique", target=5, about="answer", points="p")) == [5]
    assert target_refs(CallConsensus(type="call_consensus", synthesis="s", refs=[2, 3], credits=[])) == [2, 3]
    assert target_refs(Propose(type="propose", answer_summary="a", method="m")) == []


def test_step_schema_is_simplified_for_strict_backends():
    schema = step_schema()
    text = json.dumps(schema)
    for word in ('"oneOf"', '"discriminator"', '"const"', '"minLength"', '"title"'):
        assert word not in text
    assert schema["required"] == ["action"]
    assert "anyOf" in json.dumps(schema["properties"]["action"])


def test_simplify_schema_keeps_property_names():
    schema = {"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert simplify_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}


def test_ignores_json_inside_think_block():
    text = '<think>maybe {"type": "propose", "answer_summary": "A", "method": "M"}? no.</think>{"action": {"type": "pass"}}'
    assert isinstance(parse_action(text), Pass)


def test_prose_with_braces_before_a_fenced_action():
    text = 'Here is my {action} wrapper:\n```json\n{"action": {"type": "pass"}}\n```\nDone.'
    assert isinstance(parse_action(text), Pass)


def test_prose_with_braces_before_plain_json():
    assert isinstance(parse_action('I considered {options}. {"action": {"type": "list_files"}}'), ListFiles)


def test_write_file_content_is_capped():
    big = json.dumps({"action": {"type": "write_file", "path": "a.md", "base_version": 0, "content": "x" * 20_001, "note": "n"}})
    with pytest.raises(ActionError):
        parse_action(big)


def test_summary_omits_empty_location():
    from pydantic import BaseModel, ValidationError, model_validator

    from aiswarm.actions import summarise_validation

    class M(BaseModel):
        @model_validator(mode="after")
        def _fail(self):
            raise ValueError("duplicate goal id 'x'")

    with pytest.raises(ValidationError) as info:
        M()
    assert summarise_validation(info.value) == "Value error, duplicate goal id 'x'"


def test_summary_reports_length_of_too_long_text():
    from pydantic import BaseModel, Field, ValidationError

    from aiswarm.actions import summarise_validation

    class M(BaseModel):
        lessons: str = Field(max_length=10)

    with pytest.raises(ValidationError) as info:
        M(lessons="x" * 4312)
    assert summarise_validation(info.value).endswith("(yours was 4,312 characters)")


def test_summary_reports_length_for_custom_character_limits():
    from pydantic import ValidationError

    from aiswarm.actions import summarise_validation
    from aiswarm.profiles import PublicProfile

    with pytest.raises(ValidationError) as info:
        PublicProfile(name="N", headline="H", introduction="y" * 700)
    assert "(yours was 700 characters)" in summarise_validation(info.value)


def test_summary_leaves_other_errors_alone():
    from pydantic import BaseModel, ValidationError

    from aiswarm.actions import summarise_validation

    class M(BaseModel):
        n: int

    with pytest.raises(ValidationError) as info:
        M(n="abc")
    assert "yours was" not in summarise_validation(info.value)


def test_correction_request_has_a_single_full_stop():
    from aiswarm.actions import correction_request

    text = correction_request("No JSON object found in the reply.")
    assert text == "That could not be accepted: No JSON object found in the reply. Reply again with JSON only."
    assert ".." not in correction_request("plain problem")
