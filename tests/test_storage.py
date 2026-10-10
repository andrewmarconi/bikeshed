import subprocess

import pytest

from aiswarm.db import MIGRATIONS, connect, transaction
from aiswarm.gitrepo import GitRepo


def git_log(path, fmt):
    return subprocess.run(["git", "log", "-1", f"--format={fmt}"], cwd=path, capture_output=True, text=True).stdout.strip()


def test_head_is_none_before_first_commit(tmp_path):
    assert GitRepo.init(tmp_path / "r").head() is None


def test_commit_records_author_and_returns_sha(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a.txt").write_text("x")
    sha = repo.commit(["a.txt"], "add a", author="wren")
    assert len(sha) == 40
    assert repo.head() == sha
    assert git_log(repo.path, "%an <%ae>|%s") == "wren <wren@aiswarm.local>|add a"


def test_init_is_idempotent(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("1")
    sha = repo.commit(["a"], "m", author="x")
    assert GitRepo.init(tmp_path / "r").head() == sha


def test_commit_ignores_hostile_global_git_config(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[commit]\n\tgpgsign = true\n[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("1")
    assert repo.commit(["a"], "m", author="x")


def test_reset_hard_restores_and_cleans(tmp_path):
    repo = GitRepo.init(tmp_path / "r")
    (repo.path / "a").write_text("v1")
    first = repo.commit(["a"], "v1", author="x")
    (repo.path / "a").write_text("v2")
    repo.commit(["a"], "v2", author="x")
    (repo.path / "stray").write_text("untracked")
    repo.reset_hard(first)
    assert (repo.path / "a").read_text() == "v1"
    assert not (repo.path / "stray").exists()
    assert repo.root_commit() == first


def test_connect_migrates_fresh_db(tmp_path):
    conn = connect(tmp_path / "c.db")
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"runs", "rounds", "entries", "files", "failures", "traces"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_connect_is_idempotent(tmp_path):
    connect(tmp_path / "c.db").close()
    conn = connect(tmp_path / "c.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_transaction_rolls_back_on_error(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    with pytest.raises(RuntimeError):
        with transaction(conn):
            make_run(conn)
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_transaction_commits(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    with transaction(conn):
        make_run(conn)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
