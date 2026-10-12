"""Per-run shared workspace: versioned files in a git repo, writes serialised by the Runner."""

import sqlite3
import unicodedata
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
    """Validate an agent-supplied path. Agent output is hostile filesystem input, and macOS
    filesystems are case-insensitive and normalisation-insensitive, so git metadata is
    rejected in any case or Unicode form."""
    stripped = unicodedata.normalize("NFC", path.strip())
    invalid = InvalidPath(f"Invalid workspace path: {path!r}")
    if not stripped or len(stripped) > MAX_PATH or "\\" in stripped or "\x00" in stripped:
        raise invalid
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in stripped):
        raise invalid
    pure = PurePosixPath(stripped)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise invalid
    if any(part.casefold().startswith(".git") for part in pure.parts):
        raise invalid
    parts = pure.parts
    while len(parts) > 1 and parts[0].casefold() == "workspace":  # paths are already inside the workspace
        parts = parts[1:]
    return str(PurePosixPath(*parts))


class Workspace:
    def __init__(self, conn: sqlite3.Connection, run_id: str, root: Path):
        self._conn = conn
        self.run_id = run_id
        self.root = root
        self._repo = GitRepo.init(root)
        if self._repo.head() is None:
            self._repo.commit([], "workspace created", author="system")

    def _canonical(self, rel: str) -> str:
        """The first-written spelling of a path; case variants alias the same file on disk."""
        key = rel.casefold()
        for path, _ in self.list_files():
            if path.casefold() == key:
                return path
        return rel

    def canonical_path(self, path: str) -> str:
        return self._canonical(normalise_path(path))

    def current_version(self, path: str) -> int:
        rel = self._canonical(normalise_path(path))
        row = self._conn.execute(
            "SELECT MAX(version) FROM files WHERE run_id = ? AND path = ?", (self.run_id, rel)
        ).fetchone()
        return row[0] or 0

    def list_files(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT path, MAX(version) FROM files WHERE run_id = ? GROUP BY path ORDER BY path", (self.run_id,)
        )
        return [(row[0], row[1]) for row in rows]

    def read(self, path: str) -> tuple[str, int]:
        rel = self._canonical(normalise_path(path))
        version = self.current_version(rel)
        if version == 0:
            raise FileNotFoundError(rel)
        return (self.root / rel).read_text(), version

    def write(self, path: str, base_version: int, content: str, author: str, note: str) -> int:
        rel = self._canonical(normalise_path(path))
        current = self.current_version(rel)
        if current and base_version != current:  # a new file has nothing to overwrite, so any base creates it
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
