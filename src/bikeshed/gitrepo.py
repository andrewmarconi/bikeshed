"""Minimal git wrapper. Commits are isolated from the user's signing and hook config."""

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_ISOLATION = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def _git_env() -> dict[str, str]:
    """Ignore the user's global/system git config and any inherited GIT_* variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    at: datetime
    author: str
    subject: str
    patch: str


class GitRepo:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def init(cls, path: Path) -> "GitRepo":
        path.mkdir(parents=True, exist_ok=True)
        repo = cls(path)
        if not (path / ".git").exists():
            repo._git("init", "-q", "--template=", "-b", "main")
        return repo

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *_ISOLATION, *args], cwd=self.path, capture_output=True, text=True, env=_git_env()
        )
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout.strip()

    def commit(self, paths: list[str], message: str, author: str) -> str:
        email = f"{author}@bikeshed.local"
        if paths:
            self._git("add", "-A", "-f", "--", *paths)
        self._git(
            "-c", f"user.name={author}", "-c", f"user.email={email}",
            "commit", "-q", "--allow-empty", "-m", message, "--author", f"{author} <{email}>",
        )
        return self._git("rev-parse", "HEAD")

    def head(self) -> str | None:
        try:
            return self._git("rev-parse", "--verify", "-q", "HEAD")
        except GitError:
            return None

    def root_commit(self) -> str:
        return self._git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]

    def reset_hard(self, commit: str) -> None:
        self._git("reset", "-q", "--hard", commit)
        self._git("clean", "-q", "-ffdx")

    def tracks(self, path: str) -> bool:
        """Whether `path` (relative to this directory) is tracked by the enclosing repository."""
        try:
            self._git("ls-files", "--error-unmatch", "--", path)
            return True
        except GitError:
            return False

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain"))

    def show_at(self, path: str, before: datetime | None = None) -> str | None:
        """The file's content at the last commit touching it (at or before `before`); None if it did not exist."""
        if self.head() is None:
            return None
        args = ["log", "-1", "--format=%H"]
        if before is not None:
            args.append(f"--before={before.isoformat()}")
        commit = self._git(*args, "--", path)
        if not commit:
            return None
        try:
            return self._git("show", f"{commit}:{path}")
        except GitError:
            return None  # deleted in that commit

    def first_version(self, path: str) -> str | None:
        if self.head() is None:
            return None
        commits = self._git("log", "--reverse", "--format=%H", "--", path).split()
        if not commits:
            return None
        try:
            return self._git("show", f"{commits[0]}:{path}")
        except GitError:
            return None

    def log(self, path: str, exclude: Sequence[str] = ()) -> list[Commit]:
        """Commits touching `path` (minus `exclude`), oldest first, each with its patch."""
        if self.head() is None:
            return []
        out = self._git(
            "log", "--reverse", "-p", "--format=%x1e%aI%x1f%an%x1f%s", "--", path,
            *(f":(exclude){p}" for p in exclude),
        )
        commits: list[Commit] = []
        for record in out.split("\x1e"):
            if not record.strip():
                continue
            header, _, patch = record.partition("\n")
            at, author, subject = header.split("\x1f", 2)
            commits.append(Commit(datetime.fromisoformat(at), author, subject, patch.strip("\n")))
        return commits
