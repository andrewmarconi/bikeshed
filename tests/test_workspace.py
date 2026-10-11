import subprocess

import pytest

from aiswarm.db import connect
from aiswarm.workspace import InvalidPath, Workspace, WriteConflict, normalise_path


@pytest.fixture
def ws(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    return Workspace(conn, "r1", tmp_path / "ws")


def test_new_workspace_has_an_initial_commit(ws):
    assert ws.head() == ws.initial_commit()
    assert ws.list_files() == []


def test_write_new_file_then_read(ws):
    assert ws.write("draft.md", 0, "hello", author="a", note="start draft") == 1
    assert ws.read("draft.md") == ("hello", 1)
    assert ws.list_files() == [("draft.md", 1)]


def test_successive_versions_and_git_author(ws):
    ws.write("draft.md", 0, "v1", author="a", note="n1")
    assert ws.write("draft.md", 1, "v2", author="b", note="tighten") == 2
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=ws.root, capture_output=True, text=True)
    assert log.stdout.strip() == "b|tighten"


def test_stale_base_version_conflicts(ws):
    ws.write("draft.md", 0, "v1", author="a", note="n")
    with pytest.raises(WriteConflict) as info:
        ws.write("draft.md", 0, "mine", author="b", note="n")
    assert info.value.current_version == 1
    assert ws.read("draft.md") == ("v1", 1)


def test_any_base_version_creates_a_file_that_does_not_exist_yet(ws):
    assert ws.write("goals.json", 5, "new", author="a", note="n") == 1
    assert ws.read("goals.json") == ("new", 1)
    with pytest.raises(WriteConflict) as info:  # once it exists, a wrong base still conflicts
        ws.write("goals.json", 5, "mine", author="b", note="n")
    assert info.value.current_version == 1


def test_read_missing_file(ws):
    with pytest.raises(FileNotFoundError):
        ws.read("nope.md")


@pytest.mark.parametrize("bad", ["../escape.md", "/etc/passwd", ".git/config", "", "   ", ".", "a\\b.md", "x" * 201])
def test_invalid_paths_are_rejected(ws, bad):
    with pytest.raises(InvalidPath):
        ws.write(bad, 0, "x", author="a", note="n")
    assert ws.list_files() == []


def test_normalise_path():
    assert normalise_path("notes/./a.md") == "notes/a.md"
    assert normalise_path("agents/b/scratch.md") == "agents/b/scratch.md"


def test_reset_to_restores_files(ws):
    ws.write("a.md", 0, "one", author="a", note="n")
    checkpoint = ws.head()
    ws.write("a.md", 1, "two", author="a", note="n")
    (ws.root / "stray.md").write_text("junk")
    ws.reset_to(checkpoint)
    assert (ws.root / "a.md").read_text() == "one"
    assert not (ws.root / "stray.md").exists()


@pytest.mark.parametrize("bad", [".GIT/config", ".Git/x", "sub/.git/x", ".gitignore", ".gitattributes", "a‌.md".replace("a", ".git")])
def test_git_metadata_paths_are_rejected_in_any_case(ws, bad):
    with pytest.raises(InvalidPath):
        ws.write(bad, 0, "x", author="a", note="n")
    assert (ws.root / ".git" / "config").read_text().count("fsmonitor") == 0


def test_case_and_unicode_variants_share_one_version_history(ws):
    ws.write("draft.md", 0, "from a", author="a", note="n")
    with pytest.raises(WriteConflict):
        ws.write("Draft.md", 0, "from b", author="b", note="n")
    assert ws.write("DRAFT.md", 1, "from b", author="b", note="n") == 2
    assert ws.list_files() == [("draft.md", 2)]
    ws.write("café.md", 0, "nfd", author="a", note="n")
    assert ws.current_version("café.md") == 1


def test_ignored_paths_are_still_committed(ws, monkeypatch, tmp_path):
    excludes = tmp_path / "excludes"
    excludes.write_text("*.log\n")
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[core]\n\texcludesFile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    assert ws.write("run.log", 0, "x", author="a", note="n") == 1
