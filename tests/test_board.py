import sqlite3

from aiswarm.board import SYSTEM, Board, public, visible
from aiswarm.db import connect
from aiswarm.events import EventLog, read_events


def test_private_entries_are_visible_only_to_their_parties(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1", ("a", "b", "c"))
    board = Board(conn, "r1")
    goal = board.append(0, SYSTEM, "goal", {"goal": "G"})
    message = board.append(2, "a", "private_message", {"to": "b", "text": "psst"}, to="b", visible_to=["a", "b"])
    note = board.append(2, "a", "think", {"text": "hmm"}, visible_to=["a"])
    assert board.get(message.id).visible_to == ("a", "b") and board.get(message.id).to == "b"
    assert board.get(goal.id).visible_to is None
    assert [e.id for e in board.entries_for("a")] == [goal.id, message.id, note.id]
    assert [e.id for e in board.entries_for("b")] == [goal.id, message.id]
    assert [e.id for e in board.entries_for("c")] == [goal.id]
    assert [e.id for e in visible(board.entries(), "c")] == [goal.id]
    assert [e.id for e in public(board.entries())] == [goal.id]


def test_runs_record_their_process(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1")
    assert conn.execute("SELECT process FROM runs WHERE id = 'r1'").fetchone()[0] == 1


def test_entries_load_from_a_database_without_the_visibility_column(tmp_path):
    """watch and observe read without migrating; an older database must still load, as all public."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, run_id TEXT, round INTEGER, author TEXT, type TEXT, "
                 "to_ TEXT, refs TEXT, payload TEXT, created_at TEXT)")
    conn.execute("INSERT INTO entries VALUES (1, 'r1', 0, 'system', 'goal', 'all', '[]', '{\"goal\": \"G\"}', 't')")
    [entry] = Board(conn, "r1").entries()
    assert entry.visible_to is None and entry.payload == {"goal": "G"}


def test_append_and_list_in_order(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn)
    board = Board(conn, "r1")
    goal = board.append(0, SYSTEM, "goal", {"goal": "G"})
    prop = board.append(1, "a", "propose", {"answer_summary": "A", "method": "M"})
    crit = board.append(2, "b", "critique", {"target": prop.id, "about": "method", "points": "p"}, refs=[prop.id])
    assert [e.id for e in board.entries()] == [goal.id, prop.id, crit.id]
    assert board.get(crit.id).refs == [prop.id]
    assert board.get(crit.id).payload["points"] == "p"
    assert board.get(crit.id).to == "all"


def test_get_is_scoped_to_run(tmp_path, make_run):
    conn = connect(tmp_path / "c.db")
    make_run(conn, "r1")
    make_run(conn, "r2")
    other = Board(conn, "r2").append(0, SYSTEM, "goal", {"goal": "x"})
    assert Board(conn, "r1").get(other.id) is None
    assert Board(conn, "r1").entries() == []


def test_event_log_appends_jsonl_and_calls_sink(tmp_path):
    seen = []
    log = EventLog(tmp_path / "run" / "events.jsonl", sink=seen.append)
    log.emit("round_started", round=1)
    log.emit("action_posted", agent="a", type="propose")
    events = read_events(tmp_path / "run" / "events.jsonl")
    assert [e["event"] for e in events] == ["round_started", "action_posted"]
    assert events[1]["agent"] == "a" and events[1]["type"] == "propose" and "ts" in events[1]
    assert seen == events
