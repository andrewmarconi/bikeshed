import ast
import hashlib
from pathlib import Path

from observer_helpers import add_entry, add_run

import aiswarm
from aiswarm.gitrepo import GitRepo
from aiswarm.provenance import prompt_snapshot

SRC = Path(aiswarm.__file__).parent
AGENT_PATH = ["view.py", "agent.py", "runner.py", "board.py", "consensus.py", "actions.py", "introduce.py",
              "checkin.py", "journal.py", "library.py", "norms.py", "reviews.py", "profiles.py", "prompts/__init__.py",
              *[f"postrun/{p.name}" for p in (SRC / "postrun").glob("*.py")]]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_agent_path_module_imports_the_observer():
    for rel in AGENT_PATH:
        assert not any(name.startswith("aiswarm.observer") for name in imports(SRC / rel)), rel


def test_judge_prompts_are_not_part_of_agent_prompt_provenance():
    assert (SRC / "prompts" / "observer" / "themes.md").exists()
    assert not any("observer" in name or name in {"themes.md", "codebook.md"} for name in prompt_snapshot())


def _fingerprint(community_dir: Path) -> tuple[str | None, str]:
    digest = hashlib.sha256()
    for name in ("community.db", "community.db-wal"):  # not -shm: readers may legitimately touch it
        path = community_dir / name
        if path.exists():
            digest.update(name.encode() + path.read_bytes())
    return GitRepo(community_dir).head(), digest.hexdigest()


async def test_observe_leaves_the_community_untouched(tmp_path, community):
    from aiswarm.config import Config
    from aiswarm.observer.observe import observe

    store, conn = community
    add_run(conn, "r1", created="2099-01-01T00:00:00+00:00")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "x", "method": "m"})
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cfg = Config.model_validate({"models": {"claude": {"kind": "claude_cli"}}})
    cfg.community_dir = tmp_path / "community"
    cfg.observer.dir = tmp_path / "observer"
    before = _fingerprint(cfg.community_dir)
    await observe(cfg, judge_note="--no-judge")
    assert _fingerprint(cfg.community_dir) == before
    assert not any("observer" in p.parts for p in cfg.community_dir.rglob("*") if ".git" not in p.parts)
