from aiswarm.board import SYSTEM, Board
from aiswarm.db import connect
from aiswarm.events import EventLog, read_events


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
    assert [e["type"] for e in events] == ["round_started", "action_posted"]
    assert events[1]["agent"] == "a" and "ts" in events[1]
    assert seen == events
