import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from bikeshed.config import DEFAULT_CONFIG, ClaudeCliBackend, OpenAIBackend, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bikeshed.toml"
    path.write_text(text)
    return path


def test_loads_backends_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, '''
[models.local]
kind = "openai"
model = "qwen3-8b"

[models.claude]
kind = "claude_cli"
'''))
    assert isinstance(cfg.models["local"], OpenAIBackend)
    assert cfg.models["local"].base_url == "http://localhost:8000/v1"
    assert isinstance(cfg.models["claude"], ClaudeCliBackend)
    assert cfg.models["claude"].model == "haiku"
    assert cfg.run.max_rounds == 12
    assert cfg.run.quorum_slack == 1
    assert cfg.run.stagnation_rounds == 2
    assert cfg.community_dir == (tmp_path / "community").resolve()
    assert cfg.runs_dir == (tmp_path / "runs").resolve()
    assert cfg.db_path == cfg.community_dir / "community.db"


def test_rejects_unknown_backend_kind(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.x]\nkind = "nope"\nmodel = "m"\n'))


def test_run_settings_override(tmp_path):
    cfg = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n[run]\nmax_rounds = 3\n'))
    assert cfg.run.max_rounds == 3


def test_default_config_is_valid(tmp_path):
    assert tomllib.loads(DEFAULT_CONFIG)
    cfg = load_config(write(tmp_path, DEFAULT_CONFIG))
    assert set(cfg.models) == {"local", "claude"}


def test_postrun_settings_defaults(tmp_path):
    cfg = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n'))
    assert cfg.run.max_concurrent_calls == 4
    assert cfg.run.reputation_half_life_runs == 5.0


def test_max_concurrent_calls_must_be_positive(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n[run]\nmax_concurrent_calls = 0\n'))


def test_report_and_library_settings(tmp_path):
    cfg = load_config(write(tmp_path, '''
[models.local]
kind = "openai"
model = "m"
[models.embed]
kind = "openai"
model = "e"
[report]
narrator = "local"
[library]
embeddings = "embed"
top_k = 5
'''))
    assert (cfg.report.narrator, cfg.library.embeddings, cfg.library.top_k) == ("local", "embed", 5)
    bare = load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n'))
    assert (bare.report.narrator, bare.library.embeddings, bare.library.top_k) == (None, None, 3)


@pytest.mark.parametrize("extra", [
    '[report]\nnarrator = "nope"\n',
    '[library]\nembeddings = "nope"\n',
    '[models.c]\nkind = "claude_cli"\n[library]\nembeddings = "c"\n',
])
def test_report_and_library_must_name_suitable_backends(tmp_path, extra):
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, '[models.l]\nkind = "openai"\nmodel = "m"\n' + extra))


def test_observer_settings_default_and_resolve(tmp_path):
    cfg = load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n'))
    assert cfg.observer.judge is None
    assert cfg.observer.dir == (tmp_path / "observer").resolve()
    cfg = load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n[observer]\njudge = "claude"\ndir = "obs"\n'))
    assert cfg.observer.judge == "claude" and cfg.observer.dir == (tmp_path / "obs").resolve()


def test_observer_judge_must_name_a_backend(tmp_path):
    with pytest.raises(ValidationError, match="observer.judge"):
        load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n[observer]\njudge = "nope"\n'))


def test_observer_judges_override_one_analysis(tmp_path):
    cfg = load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n[models.l]\nkind = "openai"\nmodel = "m"\n'
                                      '[observer]\njudge = "claude"\n[observer.judges]\nthink_vs_act = "l"\n'))
    assert cfg.observer.judges == {"think_vs_act": "l"}


@pytest.mark.parametrize("extra", [
    '[observer]\njudge = "claude"\n[observer.judges]\nthink_vs_act = "nope"\n',
    '[observer.judges]\nthink_vs_act = "claude"\n',
])
def test_observer_judges_need_a_known_backend_and_a_default_judge(tmp_path, extra):
    with pytest.raises(ValidationError, match="observer.judges"):
        load_config(write(tmp_path, '[models.claude]\nkind = "claude_cli"\n' + extra))


def test_scorer_judge_must_name_a_backend():
    import pytest

    from bikeshed.config import Config

    with pytest.raises(ValueError, match="scorer.judge"):
        Config.model_validate({"models": {}, "scorer": {"judge": "nope"}})
    assert Config.model_validate({"models": {}}).scorer.judge is None


def test_max_writes_defaults_to_three_and_cannot_be_negative():
    from pydantic import ValidationError

    from bikeshed.config import RunSettings

    assert RunSettings().max_writes == 3
    with pytest.raises(ValidationError):
        RunSettings(max_writes=-1)
