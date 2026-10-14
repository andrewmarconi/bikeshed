import itertools
import json
import re

import pytest
from typer.testing import CliRunner

import bikeshed.cli as cli
from bikeshed.models.fake import FakeEmbedder, FakeModel

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch, postrun_reply):
    counter = itertools.count(1)

    def respond(system, messages):
        if system.startswith("You are the Scorer"):
            return json.dumps({"accepted": True, "feedback": ""})
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
    config = tmp_path / "bikeshed.toml"
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
    from bikeshed.profiles import ProfileStore
    ProfileStore(tmp_path / "community").create("z", "local", "T.")
    result = invoke("run", "G", "-c", config)
    assert result.exit_code != 0
    assert "agents introduce z" in result.output


def test_missing_config_is_reported(tmp_path):
    result = invoke("agents", "list", "-c", tmp_path / "missing.toml")
    assert result.exit_code != 0
    assert "bikeshed init" in result.output


def test_doctor_warns_that_subscription_mode_is_not_fully_isolated(tmp_path, monkeypatch):
    config = tmp_path / "bikeshed.toml"
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
    from bikeshed.db import connect
    conn = connect(tmp_path / "community" / "community.db")
    phases = [r["phase"] for r in conn.execute("SELECT phase FROM run_provenance WHERE run_id = ? ORDER BY id", (first_id,))]
    assert phases == ["start", "postrun"]
    assert "Harness changed" not in invoke("show", first_id, "-c", config).output
    shown = invoke("show", second_id, "-c", config).output
    assert f"since run {first_id}: config run.max_rounds" in shown


def test_agents_new_control_creates_a_seeded_control_agent(project):
    tmp_path, config = project
    result = invoke("agents", "new", "e", "--model", "local", "--temperament", "T.", "--control", "-c", config)
    assert result.exit_code == 0, result.output
    assert "control = true" in (tmp_path / "community" / "agents" / "e" / "agent.toml").read_text()
    assert (tmp_path / "community" / "agents" / "e" / "seed" / "public.toml").exists()
    assert "control" in invoke("agents", "list", "-c", config).output


def test_init_seeds_the_goal_queue(project):
    tmp_path, config = project
    assert "best-fruit" in (tmp_path / "community" / "goals.toml").read_text()


def test_run_next_takes_the_benchmark_then_the_queue(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    first = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert first.exit_code == 0, first.output
    assert "goal (benchmark, underspecified): Pick one pizza topping combination" in first.output
    second = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert "goal (best-fruit, unsettled): Come to an agreement on the best fruit." in second.output
    listing = invoke("goals", "list", "-c", config).output
    assert "best-fruit\tunsettled\tused by" in listing and "next: hot-dog-sandwich" in listing
    assert "due after 3 more runs" in listing


def test_run_rejects_a_goal_together_with_next(project):
    tmp_path, config = project
    result = invoke("run", "Pick a colour", "--next", "-c", config)
    assert result.exit_code != 0 and "not both" in result.output


def test_goals_add(project):
    tmp_path, config = project
    result = invoke("goals", "add", "Rank the best sandwiches.", "--category", "unsettled", "-c", config)
    assert result.exit_code == 0, result.output
    assert "added rank-the-best-sandwiches (unsettled)" in result.output
    bad = invoke("goals", "add", "Fine.", "--category", "serious", "-c", config)
    assert bad.exit_code != 0 and "category" in bad.output


def test_goals_list_reports_a_broken_file(project):
    tmp_path, config = project
    (tmp_path / "community" / "goals.toml").write_text("[benchmark\n")
    result = invoke("goals", "list", "-c", config)
    assert result.exit_code != 0 and "goals.toml" in result.output and "Traceback" not in result.output


def test_agents_timeline(project):
    tmp_path, config = project
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    result = invoke("agents", "timeline", "member 1", "-c", config)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Timeline: Member 1 (a)")
    missing = invoke("agents", "timeline", "nobody", "-c", config)
    assert missing.exit_code != 0 and "no agent" in missing.output
    bad_since = invoke("agents", "timeline", "a", "--since", "nope", "-c", config)
    assert bad_since.exit_code != 0 and "no run" in bad_since.output


def test_agents_introduce_says_what_it_is_waiting_for(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config)
    assert "asking a (local) to introduce itself" in result.output


def test_agents_set_model(project):
    tmp_path, config = project
    config.write_text(config.read_text() + '\n[models.other]\nkind = "openai"\nbase_url = "http://x/v1"\nmodel = "m"\n')
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    unknown = invoke("agents", "set-model", "a", "nope", "-c", config)
    assert unknown.exit_code != 0 and "unknown model backend" in unknown.output
    result = invoke("agents", "set-model", "a", "other", "-c", config)
    assert result.exit_code == 0, result.output
    assert "a now uses other (was local)" in result.output
    assert "other" in invoke("agents", "list", "-c", config).output


def test_agents_set_model_refuses_during_an_unclosed_run(project):
    from bikeshed.db import connect

    tmp_path, config = project
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES "
                 "('r1', 'g', 'paused', '[\"a\"]', '2026-10-10T00:00:00+00:00')")
    conn.commit()
    result = invoke("agents", "set-model", "a", "local", "-c", config)
    assert result.exit_code != 0
    assert "r1" in result.output


@pytest.mark.parametrize("extra", [["Some goal"], ["--next"]])
def test_run_resume_rejects_a_goal_or_next(project, extra):
    tmp_path, config = project
    result = invoke("run", "--resume", "r1", *extra, "-c", config)
    assert result.exit_code != 0
    assert "--resume" in result.output and "not both" in result.output


def test_observe_without_a_judge(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    assert invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0
    result = invoke("observe", "-c", config)
    assert result.exit_code == 0, result.output
    assert "metrics only" in result.output
    assert (tmp_path / "observer" / "summary.md").exists()


def test_observe_unknown_run_fails_cleanly(project):
    tmp_path, config = project
    result = invoke("observe", "--run", "nope", "--no-judge", "-c", config)
    assert result.exit_code != 0 and "no closed run" in result.output


def test_export_after_a_real_run(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    assert invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0
    result = invoke("export", "--out", tmp_path / "out", "-c", config)
    assert result.exit_code == 0, result.output
    (run_dir,) = (tmp_path / "out" / "runs").iterdir()
    assert "Pick a colour" in (run_dir / "transcript.md").read_text()
    assert (run_dir / "events.jsonl").exists()
    assert "Pick a colour" in (tmp_path / "out" / "runs.csv").read_text()


def test_export_unknown_run_fails_cleanly(project):
    tmp_path, config = project
    result = invoke("export", "--run", "nope", "--out", tmp_path / "out", "-c", config)
    assert result.exit_code != 0 and "unknown run" in result.output


def test_watch_errors_cleanly(project):
    tmp_path, config = project
    result = invoke("watch", "-c", config)
    assert result.exit_code != 0 and "no runs yet" in result.output
    unknown = invoke("watch", "nope", "-c", config)
    assert unknown.exit_code != 0 and "no run 'nope'" in unknown.output


def test_watch_opens_the_latest_run(project, monkeypatch):
    tmp_path, config = project
    make_agents(config, "a")
    assert invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config).exit_code == 0
    opened = []
    monkeypatch.setattr(cli.WatchApp, "run", lambda self: opened.append(self.watcher.state.run_id))
    result = invoke("watch", "-c", config)
    assert result.exit_code == 0, result.output
    assert len(opened) == 1


def test_run_with_process_2_records_it_and_resume_keeps_it(project):
    import sqlite3

    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--process", "2", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == 2
    provenance = json.loads(conn.execute("SELECT config FROM run_provenance WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert provenance["run"]["process"] == 2
    rejected = invoke("run", "--resume", run_id, "--process", "1", "-c", config)
    assert rejected.exit_code != 0 and "keeps its process" in rejected.output
    assert invoke("run", "G", "--process", "3", "-c", config).exit_code != 0


def test_abandon_closes_a_dead_run_and_keeps_its_record(project):
    from bikeshed.db import connect

    tmp_path, config = project
    make_agents(config, "a", "b")
    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES "
                 "('r1', 'Pick a colour', 'running', '[\"a\", \"b\"]', '2026-10-10T00:00:00+00:00')")
    conn.execute("INSERT INTO entries (run_id, round, author, type, payload, created_at) VALUES "
                 "('r1', 3, 'a', 'propose', '{\"answer_summary\": \"Blue\", \"method\": \"m\"}', 't')")
    conn.commit()
    declined = runner.invoke(cli.app, ["abandon", "r1", "-c", str(config)], input="n\n")
    assert declined.exit_code != 0
    assert conn.execute("SELECT status FROM runs WHERE id = 'r1'").fetchone()[0] == "running"
    result = invoke("abandon", "r1", "--yes", "-c", config)
    assert result.exit_code == 0, result.output
    assert "r1 closed: abandoned" in result.output
    assert tuple(conn.execute("SELECT status, ended_reason FROM runs WHERE id = 'r1'").fetchone()) == \
        ("closed", "abandoned")
    rows = conn.execute("SELECT round, author, type, payload FROM entries WHERE run_id = 'r1' ORDER BY id").fetchall()
    assert [r["type"] for r in rows] == ["propose", "run_closed"]  # nothing is deleted
    assert rows[1]["round"] == 3 and json.loads(rows[1]["payload"]) == {"reason": "abandoned", "call_id": None}
    last = json.loads((tmp_path / "runs" / "r1" / "events.jsonl").read_text().splitlines()[-1])
    assert (last["event"], last["reason"]) == ("run_closed", "abandoned")
    assert "abandoned" in (tmp_path / "runs" / "r1" / "report" / "answer.md").read_text()
    again = invoke("abandon", "r1", "--yes", "-c", config)
    assert again.exit_code != 0 and "already closed" in again.output
    assert invoke("abandon", "nope", "--yes", "-c", config).exit_code != 0


def test_run_refuses_without_a_scorer(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    config.write_text(config.read_text().replace('\njudge = "claude"\n', "\n"))  # the [observer] line is commented
    result = invoke("run", "Pick a colour", "--no-postrun", "-c", config)
    assert result.exit_code != 0 and "[scorer]" in result.output


def test_runs_record_the_scorer_in_provenance(project):
    import sqlite3

    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    cfg_json, prompts = conn.execute("SELECT config, prompts FROM run_provenance WHERE phase = 'start'").fetchone()
    assert json.loads(cfg_json)["scorer"]["judge"] == "claude" and "claude" in json.loads(cfg_json)["models"]
    assert "scorer.md" in json.loads(prompts)


def test_run_next_stores_a_queue_goals_briefs_and_show_prints_the_reveal(project, monkeypatch):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    original = cli.make_client

    def with_briefed_scorer(backend, retries):
        base = original(backend, retries)

        def respond(system, messages):
            if system.startswith("You are the Scorer"):
                return json.dumps({"answers_goal": True, "feedback": "", "conflicts": []})
            return base._script(system, messages)

        return FakeModel(respond)

    monkeypatch.setattr(cli, "make_client", with_briefed_scorer)
    (tmp_path / "community" / "goals.toml").write_text(
        '[benchmark]\ntext = "Pick a topping."\ncategory = "underspecified"\nevery = 99\n\n'
        '[[goals]]\nid = "briefed"\ntext = "Plan a picnic."\ncategory = "distributed"\n'
        'shared = "Shared facts."\nshared_constraints = ["Use the list."]\n\n'
        '[[goals.private]]\ntext = "Private fact one."\nconstraints = ["Rule one."]\n')
    assert invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0  # benchmark
    result = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    from bikeshed.db import connect
    conn = connect(tmp_path / "community" / "community.db")
    phases = [r["phase"] for r in conn.execute("SELECT phase FROM run_provenance WHERE run_id = ? ORDER BY id", (first_id,))]
    assert phases == ["start", "postrun"]
    assert "Harness changed" not in invoke("show", first_id, "-c", config).output
    shown = invoke("show", second_id, "-c", config).output
    assert f"since run {first_id}: config run.max_rounds" in shown


def test_agents_new_control_creates_a_seeded_control_agent(project):
    tmp_path, config = project
    result = invoke("agents", "new", "e", "--model", "local", "--temperament", "T.", "--control", "-c", config)
    assert result.exit_code == 0, result.output
    assert "control = true" in (tmp_path / "community" / "agents" / "e" / "agent.toml").read_text()
    assert (tmp_path / "community" / "agents" / "e" / "seed" / "public.toml").exists()
    assert "control" in invoke("agents", "list", "-c", config).output


def test_init_seeds_the_goal_queue(project):
    tmp_path, config = project
    assert "best-fruit" in (tmp_path / "community" / "goals.toml").read_text()


def test_run_next_takes_the_benchmark_then_the_queue(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    first = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert first.exit_code == 0, first.output
    assert "goal (benchmark, underspecified): Pick one pizza topping combination" in first.output
    second = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert "goal (best-fruit, unsettled): Come to an agreement on the best fruit." in second.output
    listing = invoke("goals", "list", "-c", config).output
    assert "best-fruit\tunsettled\tused by" in listing and "next: hot-dog-sandwich" in listing
    assert "due after 3 more runs" in listing


def test_run_rejects_a_goal_together_with_next(project):
    tmp_path, config = project
    result = invoke("run", "Pick a colour", "--next", "-c", config)
    assert result.exit_code != 0 and "not both" in result.output


def test_goals_add(project):
    tmp_path, config = project
    result = invoke("goals", "add", "Rank the best sandwiches.", "--category", "unsettled", "-c", config)
    assert result.exit_code == 0, result.output
    assert "added rank-the-best-sandwiches (unsettled)" in result.output
    bad = invoke("goals", "add", "Fine.", "--category", "serious", "-c", config)
    assert bad.exit_code != 0 and "category" in bad.output


def test_goals_list_reports_a_broken_file(project):
    tmp_path, config = project
    (tmp_path / "community" / "goals.toml").write_text("[benchmark\n")
    result = invoke("goals", "list", "-c", config)
    assert result.exit_code != 0 and "goals.toml" in result.output and "Traceback" not in result.output


def test_agents_timeline(project):
    tmp_path, config = project
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    result = invoke("agents", "timeline", "member 1", "-c", config)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Timeline: Member 1 (a)")
    missing = invoke("agents", "timeline", "nobody", "-c", config)
    assert missing.exit_code != 0 and "no agent" in missing.output
    bad_since = invoke("agents", "timeline", "a", "--since", "nope", "-c", config)
    assert bad_since.exit_code != 0 and "no run" in bad_since.output


def test_agents_introduce_says_what_it_is_waiting_for(project):
    tmp_path, config = project
    result = invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config)
    assert "asking a (local) to introduce itself" in result.output


def test_agents_set_model(project):
    tmp_path, config = project
    config.write_text(config.read_text() + '\n[models.other]\nkind = "openai"\nbase_url = "http://x/v1"\nmodel = "m"\n')
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    unknown = invoke("agents", "set-model", "a", "nope", "-c", config)
    assert unknown.exit_code != 0 and "unknown model backend" in unknown.output
    result = invoke("agents", "set-model", "a", "other", "-c", config)
    assert result.exit_code == 0, result.output
    assert "a now uses other (was local)" in result.output
    assert "other" in invoke("agents", "list", "-c", config).output


def test_agents_set_model_refuses_during_an_unclosed_run(project):
    from bikeshed.db import connect

    tmp_path, config = project
    assert invoke("agents", "new", "a", "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES "
                 "('r1', 'g', 'paused', '[\"a\"]', '2026-10-10T00:00:00+00:00')")
    conn.commit()
    result = invoke("agents", "set-model", "a", "local", "-c", config)
    assert result.exit_code != 0
    assert "r1" in result.output


@pytest.mark.parametrize("extra", [["Some goal"], ["--next"]])
def test_run_resume_rejects_a_goal_or_next(project, extra):
    tmp_path, config = project
    result = invoke("run", "--resume", "r1", *extra, "-c", config)
    assert result.exit_code != 0
    assert "--resume" in result.output and "not both" in result.output


def test_observe_without_a_judge(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    assert invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0
    result = invoke("observe", "-c", config)
    assert result.exit_code == 0, result.output
    assert "metrics only" in result.output
    assert (tmp_path / "observer" / "summary.md").exists()


def test_observe_unknown_run_fails_cleanly(project):
    tmp_path, config = project
    result = invoke("observe", "--run", "nope", "--no-judge", "-c", config)
    assert result.exit_code != 0 and "no closed run" in result.output


def test_export_after_a_real_run(project):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    assert invoke("run", "Pick a colour", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0
    result = invoke("export", "--out", tmp_path / "out", "-c", config)
    assert result.exit_code == 0, result.output
    (run_dir,) = (tmp_path / "out" / "runs").iterdir()
    assert "Pick a colour" in (run_dir / "transcript.md").read_text()
    assert (run_dir / "events.jsonl").exists()
    assert "Pick a colour" in (tmp_path / "out" / "runs.csv").read_text()


def test_export_unknown_run_fails_cleanly(project):
    tmp_path, config = project
    result = invoke("export", "--run", "nope", "--out", tmp_path / "out", "-c", config)
    assert result.exit_code != 0 and "unknown run" in result.output


def test_watch_errors_cleanly(project):
    tmp_path, config = project
    result = invoke("watch", "-c", config)
    assert result.exit_code != 0 and "no runs yet" in result.output
    unknown = invoke("watch", "nope", "-c", config)
    assert unknown.exit_code != 0 and "no run 'nope'" in unknown.output


def test_watch_opens_the_latest_run(project, monkeypatch):
    tmp_path, config = project
    make_agents(config, "a")
    assert invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config).exit_code == 0
    opened = []
    monkeypatch.setattr(cli.WatchApp, "run", lambda self: opened.append(self.watcher.state.run_id))
    result = invoke("watch", "-c", config)
    assert result.exit_code == 0, result.output
    assert len(opened) == 1


def test_run_with_process_2_records_it_and_resume_keeps_it(project):
    import sqlite3

    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--process", "2", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = run_id_from(result.output)
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    assert conn.execute("SELECT process FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == 2
    provenance = json.loads(conn.execute("SELECT config FROM run_provenance WHERE run_id = ?", (run_id,)).fetchone()[0])
    assert provenance["run"]["process"] == 2
    rejected = invoke("run", "--resume", run_id, "--process", "1", "-c", config)
    assert rejected.exit_code != 0 and "keeps its process" in rejected.output
    assert invoke("run", "G", "--process", "3", "-c", config).exit_code != 0


def test_abandon_closes_a_dead_run_and_keeps_its_record(project):
    from bikeshed.db import connect

    tmp_path, config = project
    make_agents(config, "a", "b")
    conn = connect(tmp_path / "community" / "community.db")
    conn.execute("INSERT INTO runs (id, goal, status, agent_ids, created_at) VALUES "
                 "('r1', 'Pick a colour', 'running', '[\"a\", \"b\"]', '2026-10-10T00:00:00+00:00')")
    conn.execute("INSERT INTO entries (run_id, round, author, type, payload, created_at) VALUES "
                 "('r1', 3, 'a', 'propose', '{\"answer_summary\": \"Blue\", \"method\": \"m\"}', 't')")
    conn.commit()
    declined = runner.invoke(cli.app, ["abandon", "r1", "-c", str(config)], input="n\n")
    assert declined.exit_code != 0
    assert conn.execute("SELECT status FROM runs WHERE id = 'r1'").fetchone()[0] == "running"
    result = invoke("abandon", "r1", "--yes", "-c", config)
    assert result.exit_code == 0, result.output
    assert "r1 closed: abandoned" in result.output
    assert tuple(conn.execute("SELECT status, ended_reason FROM runs WHERE id = 'r1'").fetchone()) == \
        ("closed", "abandoned")
    rows = conn.execute("SELECT round, author, type, payload FROM entries WHERE run_id = 'r1' ORDER BY id").fetchall()
    assert [r["type"] for r in rows] == ["propose", "run_closed"]  # nothing is deleted
    assert rows[1]["round"] == 3 and json.loads(rows[1]["payload"]) == {"reason": "abandoned", "call_id": None}
    last = json.loads((tmp_path / "runs" / "r1" / "events.jsonl").read_text().splitlines()[-1])
    assert (last["event"], last["reason"]) == ("run_closed", "abandoned")
    assert "abandoned" in (tmp_path / "runs" / "r1" / "report" / "answer.md").read_text()
    again = invoke("abandon", "r1", "--yes", "-c", config)
    assert again.exit_code != 0 and "already closed" in again.output
    assert invoke("abandon", "nope", "--yes", "-c", config).exit_code != 0


def test_run_refuses_without_a_scorer(project):
    tmp_path, config = project
    make_agents(config, "a", "b")
    config.write_text(config.read_text().replace('\njudge = "claude"\n', "\n"))  # the [observer] line is commented
    result = invoke("run", "Pick a colour", "--no-postrun", "-c", config)
    assert result.exit_code != 0 and "[scorer]" in result.output


def test_runs_record_the_scorer_in_provenance(project):
    import sqlite3

    tmp_path, config = project
    make_agents(config, "a", "b")
    result = invoke("run", "Pick a colour", "--max-rounds", "1", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    conn = sqlite3.connect(tmp_path / "community" / "community.db")
    cfg_json, prompts = conn.execute("SELECT config, prompts FROM run_provenance WHERE phase = 'start'").fetchone()
    assert json.loads(cfg_json)["scorer"]["judge"] == "claude" and "claude" in json.loads(cfg_json)["models"]
    assert "scorer.md" in json.loads(prompts)


def test_run_next_stores_a_queue_goals_briefs_and_show_prints_the_reveal(project, monkeypatch):
    tmp_path, config = project
    for agent_id in ("a", "b"):
        assert invoke("agents", "new", agent_id, "--model", "local", "--temperament", "T.", "-c", config).exit_code == 0
    original = cli.make_client

    def with_briefed_scorer(backend, retries):
        base = original(backend, retries)

        def respond(system, messages):
            if system.startswith("You are the Scorer"):
                return json.dumps({"answers_goal": True, "feedback": "", "conflicts": []})
            return base._script(system, messages)

        return FakeModel(respond)

    monkeypatch.setattr(cli, "make_client", with_briefed_scorer)
    (tmp_path / "community" / "goals.toml").write_text(
        '[benchmark]\ntext = "Pick a topping."\ncategory = "underspecified"\nevery = 99\n\n'
        '[[goals]]\nid = "briefed"\ntext = "Plan a picnic."\ncategory = "distributed"\n'
        'shared = "Shared facts."\nshared_constraints = ["Use the list."]\n\n'
        '[[goals.private]]\ntext = "Private fact one."\nconstraints = ["Rule one."]\n')
    assert invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config).exit_code == 0  # benchmark
    result = invoke("run", "--next", "--max-rounds", "2", "--no-postrun", "-c", config)
    assert result.exit_code == 0, result.output
    run_id = next(l.split()[-1] for l in result.output.splitlines() if "run" in l and "r" in l.split()[-1:][0]) \
        if False else None
    from bikeshed.db import connect
    conn = connect(tmp_path / "community" / "community.db")
    run_id = conn.execute("SELECT id FROM runs WHERE goal_id = 'briefed'").fetchone()[0]
    rows = conn.execute("SELECT slot, text FROM run_briefs WHERE run_id = ? ORDER BY slot", (run_id,)).fetchall()
    assert [tuple(r) for r in rows] == [(0, "Shared facts."), (1, "Private fact one.")]
    briefs_md = (tmp_path / "runs" / run_id / "report" / "briefs.md").read_text()
    assert "Private fact one." in briefs_md
    shown = invoke("show", run_id, "-c", config)
    assert shown.exit_code == 0 and briefs_md.strip() in shown.output
