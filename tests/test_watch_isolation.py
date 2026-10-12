import ast
import hashlib
from pathlib import Path

from observer_helpers import add_entry, add_run

import bikeshed
from bikeshed.config import Config
from bikeshed.gitrepo import GitRepo
from bikeshed.watch.source import open_watcher

SRC = Path(bikeshed.__file__).parent
AGENT_PATH = ["view.py", "agent.py", "runner.py", "board.py", "consensus.py", "actions.py", "introduce.py",
              "checkin.py", "journal.py", "library.py", "norms.py", "reviews.py", "profiles.py", "prompts/__init__.py",
              *[f"postrun/{p.name}" for p in (SRC / "postrun").glob("*.py")]]


def test_no_agent_path_module_imports_the_watcher():
    for rel in AGENT_PATH:
        tree = ast.parse((SRC / rel).read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(name.startswith("bikeshed.watch") for name in names), rel


def test_watching_leaves_the_community_untouched(tmp_path, community):
    store, conn = community
    add_run(conn, "r1")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"

    def fingerprint():
        db = cfg.community_dir / "community.db"
        return GitRepo(cfg.community_dir).head(), hashlib.sha256(db.read_bytes()).hexdigest()

    before = fingerprint()
    watcher = open_watcher(cfg, "r1")
    watcher.poll()
    watcher.poll()
    assert fingerprint() == before
    assert not cfg.runs_dir.exists()  # the watcher creates no files
