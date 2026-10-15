import json

import pytest

from bikeshed.baseline import (
    BaselineError, check_backends, new_directory, prepare, read_info, read_source, record_run, settings_for,
)
from bikeshed.briefs import Brief, RunBriefs, save
from bikeshed.config import Config
from bikeshed.db import connect


FULL_RUN = {"process": 3, "max_rounds": 12, "close_rule": "critique", "view_entries": 15, "max_writes": 3,
            "quorum_slack": 1, "stagnation_rounds": 2, "max_reads": 3}


def _provenance(conn, run_id, phase, run, model="CHANGE-ME", spec=None, scorer=None):
    config = {"run": {**FULL_RUN, **run}}
    if scorer is not None:
        config |= {"scorer": {"judge": scorer[0]}, "models": {scorer[0]: scorer[1]}}
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES (?, ?, 'abc', 0, ?, '{}', ?, '{}', 1, 'f', 't')",
        (run_id, phase, json.dumps(config),
         json.dumps({a: {"backend": "local", "model": model, **(spec or {})} for a in "abc"})))


def _closed(conn, run_id="r1", seed=77, status="closed"):
    conn.execute("INSERT INTO runs (id, goal, status, ended_reason, agent_ids, created_at, seed, goal_id, goal_category, "
                 "is_benchmark, process) VALUES (?, 'Pick a pizza.', ?, 'consensus', ?, 't', ?, 'benchmark', "
                 "'underspecified', 1, 3)", (run_id, status, json.dumps(["a", "b", "c"]), seed))


def _cfg(tmp_path):
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "base_url": "http://x/v1", "model": "CHANGE-ME"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.runs_dir = tmp_path / "runs"
    return cfg


def test_read_source_takes_goal_seed_backends_and_brief_deal(community):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {"process": 3, "max_rounds": 12})
    deal = RunBriefs(Brief(0, None, "Shared.", ()), (Brief(1, "b", "Private.", ()),))
    save(conn, "r1", deal)
    src = read_source(conn, store, "r1")
    assert (src.goal, src.goal_id, src.is_benchmark, src.seed) == ("Pick a pizza.", "benchmark", True, 77)
    assert src.agent_ids == ("a", "b", "c") and src.backends == {"a": "local", "b": "local", "c": "local"}
    assert src.briefs == deal


def test_settings_come_from_the_last_start_or_resume(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {"process": 3, "max_rounds": 12, "view_entries": 18, "max_concurrent_calls": 9})
    _provenance(conn, "r1", "resume", {"process": 3, "max_rounds": 6, "view_entries": 18, "max_concurrent_calls": 9})
    _provenance(conn, "r1", "postrun", {"process": 3, "max_rounds": 99})
    settings = settings_for(read_source(conn, store, "r1"), _cfg(tmp_path))
    assert (settings.process, settings.max_rounds, settings.view_entries) == (3, 6, 18)
    assert settings.max_concurrent_calls == _cfg(tmp_path).run.max_concurrent_calls  # the machine's, not the run's


def test_temperaments_are_first_versions(community):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    store.write_private("a", "temperament.md", "Changed later.")
    assert read_source(conn, store, "r1").temperaments["a"].strip() == "Temperament of a."


@pytest.mark.parametrize("problem, message", [("missing", "no run"), ("open", "not closed"),
                                              ("no-seed", "no recorded seed"), ("no-provenance", "no provenance")])
def test_read_source_refuses_what_it_cannot_mirror(community, problem, message):
    store, conn = community
    if problem != "missing":
        _closed(conn, seed=None if problem == "no-seed" else 77, status="paused" if problem == "open" else "closed")
    if problem not in ("missing", "no-provenance"):
        _provenance(conn, "r1", "start", {})
    with pytest.raises(BaselineError, match=message):
        read_source(conn, store, "r1")


def test_check_backends_refuses_a_missing_or_changed_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    src = read_source(conn, store, "r1")
    cfg = _cfg(tmp_path)
    check_backends(src, cfg)  # unchanged: fine
    cfg.models["local"].model = "Other-Model"
    with pytest.raises(BaselineError, match="Other-Model"):
        check_backends(src, cfg)
    del cfg.models["local"]
    with pytest.raises(BaselineError, match="no longer in the config"):
        check_backends(src, cfg)


def test_prepare_builds_a_scratch_room_of_seed_agents(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    home = tmp_path / "baselines"
    first = new_directory(home, "r1")
    assert first.name == "r1-b1"
    scratch = prepare(first, read_source(conn, store, "r1"))
    assert new_directory(home, "r1").name == "r1-b2"
    assert scratch.ids() == ["a", "b", "c"]
    assert scratch.private("a")["temperament.md"].strip() == "Temperament of a."
    assert scratch.public("a") is None and not scratch.record("a").control
    assert scratch.record("a").model == "local"
    assert connect(first / "community" / "community.db").execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert (first / "runs").is_dir()
    assert read_info(first)["source_run"] == "r1" and "baseline_run" not in read_info(first)
    record_run(first, "x123")
    assert read_info(first)["baseline_run"] == "x123"


def test_process_comes_from_the_run_row(community, tmp_path):
    store, conn = community
    _closed(conn)                                  # runs.process = 3
    _provenance(conn, "r1", "start", {"process": 2})
    assert settings_for(read_source(conn, store, "r1"), _cfg(tmp_path)).process == 3


def test_a_run_whose_provenance_lacks_a_setting_is_refused(community):
    store, conn = community
    _closed(conn)
    conn.execute(
        "INSERT INTO run_provenance (run_id, phase, code_commit, code_dirty, config, prompts, models, servers, seed, "
        "fingerprint, captured_at) VALUES ('r1', 'start', 'abc', 0, ?, '{}', ?, '{}', 1, 'f', 't')",
        (json.dumps({"run": {"max_rounds": 12}}), json.dumps({a: {"backend": "local", "model": "CHANGE-ME"} for a in "abc"})))
    with pytest.raises(BaselineError, match="close_rule"):
        read_source(conn, store, "r1")


def test_check_backends_refuses_a_changed_backend_setting(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {}, spec={"thinking": False, "context_window": 32768, "timeout_s": 600})
    cfg = _cfg(tmp_path)
    cfg.models["local"].thinking, cfg.models["local"].context_window = False, 32768
    cfg.models["local"].timeout_s = 30                      # a machine setting: never refused
    check_backends(read_source(conn, store, "r1"), cfg)
    cfg.models["local"].thinking = True
    with pytest.raises(BaselineError, match="thinking"):
        check_backends(read_source(conn, store, "r1"), cfg)


def test_check_backends_refuses_a_different_scorer(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {}, scorer=("judge", {"kind": "openai", "base_url": "http://x/v1", "model": "J"}))
    cfg = _cfg(tmp_path)
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "J"})
    cfg.scorer.judge = "judge"
    check_backends(read_source(conn, store, "r1"), cfg)
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "Other-Judge"})
    with pytest.raises(BaselineError, match="Scorer"):
        check_backends(read_source(conn, store, "r1"), cfg)
    cfg.scorer.judge = "local"
    with pytest.raises(BaselineError, match="Scorer"):
        check_backends(read_source(conn, store, "r1"), cfg)


def _think(cfg):
    cfg.models["think"] = cfg.models["local"].model_copy(update={"thinking": True})
    return cfg


def test_variant_check_skips_agent_backends_but_keeps_the_scorer(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {}, scorer=("judge", {"kind": "openai", "base_url": "http://x/v1", "model": "J"}))
    cfg = _think(_cfg(tmp_path))
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "J"})
    cfg.scorer.judge = "judge"
    cfg.models["local"].model = "Other-Model"                  # the source backend changed since
    src = read_source(conn, store, "r1")
    with pytest.raises(BaselineError, match="Other-Model"):
        check_backends(src, cfg)                               # a plain baseline is refused
    check_backends(src, cfg, "think")                          # a variant isn't
    cfg.models["judge"] = cfg.models["local"].model_copy(update={"model": "Other-Judge"})
    with pytest.raises(BaselineError, match="Scorer"):
        check_backends(src, cfg, "think")


def test_variant_check_refuses_a_missing_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    with pytest.raises(BaselineError, match="'nope' isn't in the config"):
        check_backends(read_source(conn, store, "r1"), _cfg(tmp_path), "nope")


def test_variant_check_refuses_the_backend_every_agent_used(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})                       # a, b and c all on "local"
    with pytest.raises(BaselineError, match="plain baseline, so drop --backend"):
        check_backends(read_source(conn, store, "r1"), _cfg(tmp_path), "local")


def test_variant_check_allows_a_partly_matching_source(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    src = read_source(conn, store, "r1")
    src.backends["a"] = "think"                                # a ran on think; b and c on local
    check_backends(src, _think(_cfg(tmp_path)), "think")


def test_new_directory_counts_variants_apart_from_baselines(tmp_path):
    home = tmp_path / "baselines"
    assert new_directory(home, "r1", variant=True).name == "r1-v1"
    (home / "r1-v1").mkdir(parents=True)
    assert new_directory(home, "r1", variant=True).name == "r1-v2"
    assert new_directory(home, "r1").name == "r1-b1"


def test_prepare_variant_records_its_backend(community, tmp_path):
    store, conn = community
    _closed(conn)
    _provenance(conn, "r1", "start", {})
    directory = new_directory(tmp_path / "baselines", "r1", variant=True)
    scratch = prepare(directory, read_source(conn, store, "r1"), "think")
    assert {scratch.record(a).model for a in ("a", "b", "c")} == {"think"}
    assert read_info(directory)["backend"] == "think"
    record_run(directory, "x123")                              # must keep the backend it found
    info = read_info(directory)
    assert (info["backend"], info["baseline_run"], info["source_run"]) == ("think", "x123", "r1")
    plain = prepare(new_directory(tmp_path / "baselines", "r1"), read_source(conn, store, "r1"))
    assert plain.record("a").model == "local" and "backend" not in read_info(tmp_path / "baselines" / "r1-b1")
