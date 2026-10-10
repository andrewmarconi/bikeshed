import itertools
import json
import re

import pytest
from typer.testing import CliRunner

import aiswarm.cli as cli
from aiswarm.models.fake import FakeModel

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    counter = itertools.count(1)

    def respond(system, messages):
        first = messages[0]["content"]
        if "introduce yourself" in first.lower():
            n = next(counter)
            return json.dumps({"name": f"Member {n}", "headline": f"Headline {n}", "introduction": f"Hello from {n}."})
        rnd = int(re.search(r"^# Round (\d+)", first, re.M).group(1))
        if rnd == 1:
            return json.dumps({"action": {"type": "propose", "answer_summary": "Blue", "method": "Gut"}})
        return json.dumps({"action": {"type": "pass"}})

    monkeypatch.setattr(cli, "make_client", lambda backend, retries: FakeModel(respond))
    config = tmp_path / "aiswarm.toml"
    result = runner.invoke(cli.app, ["init", "-c", str(config)])
    assert result.exit_code == 0, result.output
    return tmp_path, config


def invoke(*args):
    return runner.invoke(cli.app, [str(a) for a in args])


def test_init_creates_config_community_and_db(project):
    tmp_path, config = project
    assert config.exists()
    assert (tmp_path / "community" / ".git").exists()
    assert (tmp_path / "community" / "community.db").exists()
    assert (tmp_path / "runs").is_dir()


def test_agents_new_introduces_and_lists(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "local", "--temperament", "Curious.", "-c", config)
    assert result.exit_code == 0, result.output
    assert "Member 1 (a) — Headline 1" in result.output
    listing = invoke("agents", "list", "-c", config)
    assert "a" in listing.output and "Member 1" in listing.output and "local" in listing.output
    shown = invoke("agents", "show", "a", "-c", config)
    assert "Curious." in shown.output and "Hello from 1." in shown.output


def test_agents_new_rejects_unknown_backend(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "nope", "--temperament", "x", "-c", config)
    assert result.exit_code != 0
    assert "unknown model backend" in result.output


def test_run_then_show(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    result = invoke("run", "Pick a colour", "--max-rounds", "4", "-c", config)
    assert result.exit_code == 0, result.output
    assert "closed: stagnation" in result.output
    run_id = re.search(r"run (\S+) closed", result.output).group(1)
    shown = invoke("show", run_id, "-c", config)
    assert "Competing positions" in shown.output and "No consensus was reached" in shown.output


def test_run_refuses_agents_without_public_profile(project):
    tmp_path, config = project
    from aiswarm.profiles import ProfileStore
    ProfileStore(tmp_path / "community").create("z", "local", "T.")
    result = invoke("run", "G", "-c", config)
    assert result.exit_code != 0
    assert "agents introduce z" in result.output


def test_missing_config_is_reported(tmp_path):
    result = invoke("agents", "list", "-c", tmp_path / "missing.toml")
    assert result.exit_code != 0
    assert "aiswarm init" in result.output


def test_doctor_warns_that_subscription_mode_is_not_fully_isolated(tmp_path, monkeypatch):
    config = tmp_path / "aiswarm.toml"
    config.write_text('[models.claude]\nkind = "claude_cli"\n')

    async def fake_leak_check(client):
        return True, 650

    monkeypatch.setattr(cli, "leak_check", fake_leak_check)
    result = invoke("doctor", "-c", config)
    assert result.exit_code == 0, result.output
    assert "ok   claude: input tokens 650" in result.output
    assert "not fully isolated" in result.output
    config.write_text('[models.claude]\nkind = "claude_cli"\nbare = true\n')
    assert "not fully isolated" not in invoke("doctor", "-c", config).output


def test_agents_new_help_names_the_models_table():
    result = runner.invoke(cli.app, ["agents", "new", "--help"], terminal_width=200)
    assert "[models]" in result.output
