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
