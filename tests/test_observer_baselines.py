from observer_helpers import add_entry, add_run

from bikeshed.db import connect
from bikeshed.observer.baselines import find
from bikeshed.profiles import ProfileStore


def make_baseline(home, name, source, *, closed=True, recorded=True):
    directory = home / name
    store = ProfileStore(directory / "community")
    for agent_id, label in [("a", "Ash"), ("b", "Birch")]:
        store.create(agent_id, "fake", f"Temperament of {agent_id}.")
        store.set_public(agent_id, label, f"{label} headline", f"I am {label}.")
    conn = connect(directory / "community" / "community.db")
    add_run(conn, "x1", agents=("a", "b"), status="closed" if closed else "running", ended="consensus")
    add_entry(conn, "x1", "a", "propose", {"answer_summary": "Margherita", "method": "m"})
    call = add_entry(conn, "x1", "a", "call_consensus", {"synthesis": "Margherita for all.", "refs": [], "credits": []},
                     round=2)
    add_entry(conn, "x1", "system", "run_closed", {"reason": "consensus", "call_id": call}, round=2)
    (directory / "runs").mkdir()
    info = f'source_run = "{source}"\ncreated_at = "t"\n' + ('baseline_run = "x1"\n' if recorded else "")
    (directory / "baseline.toml").write_text(info)
    return directory


def test_find_reads_a_closed_baseline(tmp_path):
    make_baseline(tmp_path / "baselines", "r1-b1", "r1")
    (b,) = find(tmp_path / "baselines")
    assert (b.id, b.source_run, b.run.ended_reason, b.calls) == ("r1-b1", "r1", "consensus", 1)
    assert b.answer.consensus and b.answer.text == "Margherita for all."
    assert b.mix["a"]["propose"] == 1


def test_find_skips_open_unrecorded_and_missing(tmp_path):
    assert find(tmp_path / "baselines") == []
    make_baseline(tmp_path / "baselines", "r1-b1", "r1", closed=False)
    make_baseline(tmp_path / "baselines", "r1-b2", "r1", recorded=False)
    assert find(tmp_path / "baselines") == []
