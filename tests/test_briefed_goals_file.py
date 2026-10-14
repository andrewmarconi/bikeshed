import tomllib
from pathlib import Path

from bikeshed.goals import GoalFile

FILE = Path(__file__).parent.parent / "docs" / "goals" / "briefed-goals.toml"


def test_the_starter_set_is_a_valid_queue_for_a_five_member_lab():
    data = tomllib.loads(FILE.read_text())
    assert "benchmark" not in data  # the lab keeps its own
    gf = GoalFile.model_validate({"benchmark": {"text": "B", "category": "underspecified"}, **data})
    assert [g.category for g in gf.goals] == ["underspecified", "underspecified", "distributed", "distributed",
                                              "contested", "contested"]
    assert all(g.shared is not None for g in gf.goals)
    assert [len(g.private) for g in gf.goals[:2]] == [0, 0]
    assert all(1 <= len(g.private) <= 5 for g in gf.goals[2:])
    for goal in gf.goals[4:]:  # contested: conflicts live in text, never in private constraints
        assert all(not p.constraints for p in goal.private) or goal.id == "office-thermostat"
