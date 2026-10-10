import subprocess
import tomllib

import pytest

from aiswarm.goals import (
    DEFAULT_GOALS, GOALS_FILE, Benchmark, Goal, GoalError, GoalFile, add_goal, load_goals, render_goals, seed_goals,
)
from aiswarm.profiles import ProfileStore


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / "community")


def test_default_goals_are_frivolous_and_tagged():
    assert DEFAULT_GOALS.benchmark == Benchmark(
        text="Pick one pizza topping combination the whole group will share.", category="trade-off", every=5)
    assert DEFAULT_GOALS.goals[0] == Goal(id="best-fruit", text="Come to an agreement on the best fruit.",
                                          category="contested")
    assert len(DEFAULT_GOALS.goals) == 10
    assert {g.category for g in DEFAULT_GOALS.goals} == {"neutral", "trade-off", "contested"}


def test_render_round_trips_the_defaults():
    assert GoalFile.model_validate(tomllib.loads(render_goals(DEFAULT_GOALS))) == DEFAULT_GOALS


def test_render_round_trips_quotes_and_unicode():
    gf = GoalFile(benchmark=DEFAULT_GOALS.benchmark,
                  goals=[Goal(id="pastry", text='Is "pain au chocolat" better than a croissant? 🥐', category="contested")])
    assert GoalFile.model_validate(tomllib.loads(render_goals(gf))) == gf


@pytest.mark.parametrize("content, problem", [
    ("[benchmark\n", "goals.toml"),
    ('[benchmark]\ntext = "x"\ncategory = "silly"\n', "category"),
    ('[benchmark]\ntext = "x"\ncategory = "neutral"\n[[goals]]\nid = "a"\ntext = "y"\ncategory = "neutral"\n'
     '[[goals]]\nid = "a"\ntext = "z"\ncategory = "neutral"\n', "duplicate goal id"),
    ('[benchmark]\ntext = "x"\ncategory = "neutral"\n[[goals]]\nid = "benchmark"\ntext = "y"\ncategory = "neutral"\n',
     "reserved"),
])
def test_load_goals_reports_problems(tmp_path, content, problem):
    path = tmp_path / GOALS_FILE
    path.write_text(content)
    with pytest.raises(GoalError, match=problem):
        load_goals(path)


def test_load_goals_reports_a_missing_file(tmp_path):
    with pytest.raises(GoalError, match="aiswarm init"):
        load_goals(tmp_path / GOALS_FILE)


def test_seed_goals_writes_only_when_missing(store):
    assert seed_goals(store) is True
    assert load_goals(store.root / GOALS_FILE) == DEFAULT_GOALS
    (store.root / GOALS_FILE).write_text(render_goals(GoalFile(benchmark=DEFAULT_GOALS.benchmark)))
    assert seed_goals(store) is False
    assert load_goals(store.root / GOALS_FILE).goals == []


def test_add_goal_derives_unique_ids_and_commits(store):
    seed_goals(store)
    first = add_goal(store, "Rank the best sandwiches!", "contested")
    second = add_goal(store, "Rank the best sandwiches!", "neutral")
    assert (first.id, second.id) == ("rank-the-best-sandwiches", "rank-the-best-sandwiches-2")
    assert load_goals(store.root / GOALS_FILE).goals[-1] == second
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=store.root, capture_output=True, text=True)
    assert log.stdout.strip() == "aiswarm|add goal rank-the-best-sandwiches-2"


def test_add_goal_rejects_bad_input(store):
    seed_goals(store)
    with pytest.raises(GoalError, match="category"):
        add_goal(store, "Fine text.", "serious")
    with pytest.raises(GoalError, match="duplicate goal id"):
        add_goal(store, "Again.", "neutral", goal_id="best-fruit")
    with pytest.raises(GoalError, match="single line"):
        add_goal(store, "two\nlines", "neutral")
