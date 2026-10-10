"""Per-run shared workspace: versioned files in a git repo, writes serialised by the Runner."""

import sqlite3
from pathlib import Path, PurePosixPath

from aiswarm.db import now_iso
from aiswarm.gitrepo import GitRepo

MAX_PATH = 200


class InvalidPath(ValueError):
    pass


class WriteConflict(Exception):
    def __init__(self, path: str, current_version: int):
        super().__init__(f"{path} is now at version {current_version}")
        self.path = path
        self.current_version = current_version


def normalise_path(path: str) -> str:
    stripped = path.strip()
    invalid = InvalidPath(f"Invalid workspace path: {path!r}")
    if not stripped or len(stripped) > MAX_PATH or "\\" in stripped or "\x00" in stripped:
        raise invalid
    pure = PurePosixPath(stripped)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or pure.parts[0] == ".git":
        raise invalid
    return str(pure)


class Workspace:
    def __init__(self, conn: sqlite3.Connection, run_id: str, root: Path):
        self._conn = conn
        self.run_id = run_id
        self.root = root
        self._repo = GitRepo.init(root)
        if self._repo.head() is None:
            self._repo.commit([], "workspace created", author="system")

    def current_version(self, path: str) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) FROM files WHERE run_id = ? AND path = ?", (self.run_id, path)
        ).fetchone()
        return row[0] or 0

    def list_files(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT path, MAX(version) FROM files WHERE run_id = ? GROUP BY path ORDER BY path", (self.run_id,)
        )
        return [(row[0], row[1]) for row in rows]

    def read(self, path: str) -> tuple[str, int]:
        rel = normalise_path(path)
        version = self.current_version(rel)
        if version == 0:
            raise FileNotFoundError(rel)
        return (self.root / rel).read_text(), version

    def write(self, path: str, base_version: int, content: str, author: str, note: str) -> int:
        rel = normalise_path(path)
        current = self.current_version(rel)
        if base_version != current:
            raise WriteConflict(rel, current)
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        version = current + 1
        sha = self._repo.commit([rel], f"{note}\n\n{rel} v{version}", author=author)
        self._conn.execute(
            "INSERT INTO files (run_id, path, version, author, git_commit, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, rel, version, author, sha, now_iso()),
        )
        return version

    def head(self) -> str:
        head = self._repo.head()
        assert head is not None
        return head

    def initial_commit(self) -> str:
        return self._repo.root_commit()

    def reset_to(self, commit: str) -> None:
        self._repo.reset_hard(commit)
