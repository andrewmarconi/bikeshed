import itertools
import json
import re

import pytest
from typer.testing import CliRunner

import aiswarm.cli as cli
from aiswarm.models.fake import FakeEmbedder, FakeModel

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch, postrun_reply):
    counter = itertools.count(1)

    def respond(system, messages):
        first = messages[0]["content"]
        reply = postrun_reply(first)
        if reply:
            return reply
        if "introduce yourself" in first.lower():
            n = next(counter)
            return json.dumps({"name": f"Member {n}", "headline": f"Headline {n}", "introduction": f"Hello from {n}."})
        rnd = int(re.search(r"^# Round (\d+)", first, re.M).group(1))
        if rnd == 1:
            return json.dumps({"action": {"type": "propose", "answer_summary": "Blue", "method": "Gut"}})
        return json.dumps({"action": {"type": "pass"}})

    monkeypatch.setattr(cli, "make_client", lambda backend, retries: FakeModel(respond))
    monkeypatch.setattr(cli, "make_embedder", lambda backend: FakeEmbedder())

    async def fake_probe(backend):
        return {"models": [backend.model], "health": None, "error": None}

    monkeypatch.setattr(cli, "probe_server", fake_probe)
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


def make_agents(config, *ids):
    for agent_id in ids:
        result = invoke("agents", "new", agent_id, "--model", "local", "--temperament", f"T{agent_id}.", "-c", config)
        assert result.exit_code == 0, result.output


def run_id_from(output):
    return re.search(r"run (\S+) closed", output).group(1)


def test_run_triggers_the_postrun_cycle(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "2", "-c", config)
    assert result.exit_code == 0, result.output
    assert "— post-run —" in result.output and "post-run done (0 failed steps)" in result.output
    run_id = run_id_from(result.output)
    assert (tmp_path / "community" / "agents" / "a" / "journal" / f"{run_id}.md").exists()
    shown = invoke("agents", "show", "a", "-c", config)
    assert "reputation: no reviews yet" in shown.output
    assert "## Reviews received" in shown.output and "Self-review" in shown.output
    assert "## Latest journal" in shown.output and "After reviews." in shown.output


def test_no_postrun_skips_the_cycle_and_postrun_runs_it_later(project):
    tmp_path, config = project
    make_agents(config, "a")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    assert "post-run" not in result.output
    assert not (tmp_path / "community" / "agents" / "a" / "journal").exists()
    later = invoke("postrun", run_id, "-c", config)
    assert later.exit_code == 0, later.output
    assert "post-run done" in later.output
    assert (tmp_path / "community" / "agents" / "a" / "journal" / f"{run_id}.md").exists()
    again = invoke("postrun", run_id, "-c", config)
    assert again.exit_code == 0 and "post-run done (0 failed steps)" in again.output


def test_postrun_rejects_unknown_runs(project):
    tmp_path, config = project
    result = invoke("postrun", "nope", "-c", config)
    assert result.exit_code == 1 and "no run 'nope'" in result.output


def test_agents_checkin_renames_and_records_pronouns(project, monkeypatch):
    tmp_path, config = project
    make_agents(config, "a", "b")
    replies = {"a": {"name": "Alder", "pronouns": "they/them"}, "b": {"name": "Member 2", "pronouns": ""}}

    def respond(system, messages):
        agent_id = re.search(r"\((\w)\)", system).group(1)
        return json.dumps(replies[agent_id])

    monkeypatch.setattr(cli, "make_client", lambda backend, retries: FakeModel(respond))
    result = invoke("agents", "checkin", "-c", config)
    assert result.exit_code == 0, result.output
    assert "Alder (a) · they/them — formerly Member 1" in result.output
    assert "Member 2 (b) · pronouns not stated" in result.output
    only_a = invoke("agents", "checkin", "--agent", "a", "-c", config)
    assert only_a.exit_code == 0 and "(b)" not in only_a.output
    shown = invoke("agents", "show", "a", "-c", config)
    assert "# Alder (a) · they/them" in shown.output


def test_narrator_and_show(project):
    tmp_path, config = project
    config.write_text(config.read_text().replace('# narrator = "claude"', 'narrator = "local"'))
    make_agents(config, "a")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    shown = invoke("show", run_id, "-c", config)
    assert "# Account" in shown.output and "# Narrative" in shown.output


def test_runs_record_provenance_and_show_reports_changes(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    first = invoke("run", "Pick a colour", "--max-rounds", "4", "-c", config)
    first_id = re.search(r"run (\S+) closed", first.output).group(1)
    second = invoke("run", "Pick a colour", "--max-rounds", "3", "-c", config)
    second_id = re.search(r"run (\S+) closed", second.output).group(1)
    from aiswarm.db import connect
    conn = connect(tmp_path / "community" / "community.db")
    phases = [r["phase"] for r in conn.execute("SELECT phase FROM run_provenance WHERE run_id = ? ORDER BY id", (first_id,))]
    assert phases == ["start", "postrun"]
    assert "Harness changed" not in invoke("show", first_id, "-c", config).output
    shown = invoke("show", second_id, "-c", config).output
    assert f"since run {first_id}: config run.max_rounds" in shown
