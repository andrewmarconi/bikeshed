"""Prompt templates. Text lives in prompts/*.md; placeholders use $name."""

from importlib.resources import files
from string import Template


def render(name: str, **values: object) -> str:
    text = files("bikeshed.prompts").joinpath(name).read_text()
    return Template(text).substitute(values)
