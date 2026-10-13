import subprocess
import tomllib

import pytest

from bikeshed.goals import (
    DEFAULT_GOALS, GOALS_FILE, Benchmark, Goal, GoalError, GoalFile, add_goal, load_goals, render_goals, seed_goals,
)
from bikeshed.profiles import ProfileStore


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / "community")


def test_default_goals_are_open_and_tagged():
    assert DEFAULT_GOALS.benchmark == Benchmark(
        text="Pick one pizza topping combination the whole group will share.", category="underspecified", every=5)
    assert DEFAULT_GOALS.goals[0] == Goal(id="best-fruit", text="Come to an agreement on the best fruit.",
                                          category="unsettled")
    assert len(DEFAULT_GOALS.goals) == 12
    assert {g.category for g in DEFAULT_GOALS.goals} == {"unsettled", "underspecified"}


def test_render_round_trips_the_defaults():
    assert GoalFile.model_validate(tomllib.loads(render_goals(DEFAULT_GOALS))) == DEFAULT_GOALS


def test_render_round_trips_quotes_and_unicode():
    gf = GoalFile(benchmark=DEFAULT_GOALS.benchmark,
                  goals=[Goal(id="pastry", text='Is "pain au chocolat" better than a croissant? 🥐', category="unsettled")])
    assert GoalFile.model_validate(tomllib.loads(render_goals(gf))) == gf


@pytest.mark.parametrize("content, problem", [
    ("[benchmark\n", "goals.toml"),
    ('[benchmark]\ntext = "x"\ncategory = "silly"\n', "category"),
    ('[benchmark]\ntext = "x"\ncategory = "unsettled"\n[[goals]]\nid = "a"\ntext = "y"\ncategory = "unsettled"\n'
     '[[goals]]\nid = "a"\ntext = "z"\ncategory = "unsettled"\n', "duplicate goal id"),
    ('[benchmark]\ntext = "x"\ncategory = "unsettled"\n[[goals]]\nid = "benchmark"\ntext = "y"\ncategory = "unsettled"\n',
     "reserved"),
])
def test_load_goals_reports_problems(tmp_path, content, problem):
    path = tmp_path / GOALS_FILE
    path.write_text(content)
    with pytest.raises(GoalError, match=problem):
        load_goals(path)


def test_load_goals_reports_a_missing_file(tmp_path):
    with pytest.raises(GoalError, match="bikeshed init"):
        load_goals(tmp_path / GOALS_FILE)


def test_seed_goals_writes_only_when_missing(store):
    assert seed_goals(store) is True
    assert load_goals(store.root / GOALS_FILE) == DEFAULT_GOALS
    (store.root / GOALS_FILE).write_text(render_goals(GoalFile(benchmark=DEFAULT_GOALS.benchmark)))
    assert seed_goals(store) is False
    assert load_goals(store.root / GOALS_FILE).goals == []


def test_add_goal_derives_unique_ids_and_commits(store):
    seed_goals(store)
    first = add_goal(store, "Rank the best sandwiches!", "unsettled")
    second = add_goal(store, "Rank the best sandwiches!", "unsettled")
    assert (first.id, second.id) == ("rank-the-best-sandwiches", "rank-the-best-sandwiches-2")
    assert load_goals(store.root / GOALS_FILE).goals[-1] == second
    log = subprocess.run(["git", "log", "-1", "--format=%an|%s"], cwd=store.root, capture_output=True, text=True)
    assert log.stdout.strip() == "bikeshed|add goal rank-the-best-sandwiches-2"


def test_add_goal_rejects_bad_input(store):
    seed_goals(store)
    with pytest.raises(GoalError, match="category"):
        add_goal(store, "Fine text.", "serious")
    with pytest.raises(GoalError, match="duplicate goal id"):
        add_goal(store, "Again.", "unsettled", goal_id="best-fruit")
    with pytest.raises(GoalError, match="single line"):
        add_goal(store, "two\nlines", "unsettled")


from bikeshed.db import connect  # noqa: E402
from bikeshed.goals import BENCHMARK_ID, benchmark_due, next_goal, runs_since_benchmark, used_goals  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "c.db")


def add_run(conn, run_id, goal_id=None, benchmark=False):
    conn.execute(
        "INSERT INTO runs (id, goal, status, agent_ids, created_at, goal_id, is_benchmark) "
        "VALUES (?, 'g', 'closed', '[]', 't', ?, ?)", (run_id, goal_id, int(benchmark)))


def test_benchmark_is_due_when_never_run_even_after_ad_hoc_runs(conn):
    for n in range(5):
        add_run(conn, f"r{n}")
    assert runs_since_benchmark(conn) is None
    assert next_goal(conn, DEFAULT_GOALS).is_benchmark


def test_benchmark_recurs_every_n_runs_counting_ad_hoc_runs(conn):
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    assert not benchmark_due(conn, 5)
    add_run(conn, "r1", "best-fruit")
    add_run(conn, "r2")                      # ad hoc
    add_run(conn, "r3", "hot-dog-sandwich")
    assert runs_since_benchmark(conn) == 3 and not benchmark_due(conn, 5)
    add_run(conn, "r4", "greatest-board-game")
    assert benchmark_due(conn, 5)
    assert next_goal(conn, DEFAULT_GOALS) == next_goal(conn, DEFAULT_GOALS)  # pure: selecting does not consume
    assert next_goal(conn, DEFAULT_GOALS).id == BENCHMARK_ID


def test_next_goal_takes_the_first_unused_queue_goal(conn):
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    add_run(conn, "r1", "best-fruit")
    selected = next_goal(conn, DEFAULT_GOALS)
    assert (selected.id, selected.category, selected.is_benchmark) == ("hot-dog-sandwich", "unsettled", False)
    assert used_goals(conn) == {"best-fruit": "r1"}


def test_next_goal_fails_when_the_queue_is_used_up(conn):
    gf = GoalFile(benchmark=Benchmark(text="B.", category="unsettled", every=100),
                  goals=[Goal(id="only", text="Only.", category="unsettled")])
    add_run(conn, "b1", BENCHMARK_ID, benchmark=True)
    add_run(conn, "r1", "only")
    with pytest.raises(GoalError, match="goals add"):
        next_goal(conn, gf)


def test_add_goal_rejects_control_characters_and_leaves_the_file_loadable(store):
    seed_goals(store)
    before = (store.root / GOALS_FILE).read_text()
    for text in ("del\x7fchar", "carriage\rreturn", "bell\x07"):
        with pytest.raises(GoalError, match="control characters"):
            add_goal(store, text, "unsettled")
    assert (store.root / GOALS_FILE).read_text() == before
    assert load_goals(store.root / GOALS_FILE) == DEFAULT_GOALS


@pytest.mark.parametrize("content, key", [
    ('[benchmark]\ntext = "x"\ncategory = "unsettled"\nevry = 3\n', "evry"),
    ('[benchmark]\ntext = "x"\ncategory = "unsettled"\n[[goals]]\nid = "a"\ntext = "y"\ncatgory = "unsettled"\n',
     "catgory"),
    ('[benchmark]\ntext = "x"\ncategory = "unsettled"\n[[goal]]\nid = "a"\ntext = "y"\ncategory = "unsettled"\n', "goal"),
])
def test_load_goals_rejects_unknown_keys(tmp_path, content, key):
    path = tmp_path / GOALS_FILE
    path.write_text(content)
    with pytest.raises(GoalError, match=key):
        load_goals(path)


def test_load_goals_duplicate_message_has_no_empty_location(tmp_path):
    path = tmp_path / GOALS_FILE
    path.write_text('[benchmark]\ntext = "x"\ncategory = "unsettled"\n[[goals]]\nid = "a"\ntext = "y"\n'
                    'category = "unsettled"\n[[goals]]\nid = "a"\ntext = "z"\ncategory = "unsettled"\n')
    with pytest.raises(GoalError) as info:
        load_goals(path)
    assert ": :" not in str(info.value)


def test_load_goals_reports_an_unreadable_file(tmp_path):
    path = tmp_path / GOALS_FILE
    path.write_bytes(b'[benchmark]\ntext = "\xff\xfe"\n')
    with pytest.raises(GoalError, match="UTF-8"):
        load_goals(path)


def test_load_goals_reports_a_directory(tmp_path):
    (tmp_path / GOALS_FILE).mkdir()
    with pytest.raises(GoalError, match=GOALS_FILE):
        load_goals(tmp_path / GOALS_FILE)
