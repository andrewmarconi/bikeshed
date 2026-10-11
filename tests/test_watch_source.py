from aiswarm.watch.source import EventTail


def test_reads_only_new_complete_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    tail = EventTail(path)
    assert tail.read() == []  # no file yet
    path.write_text('{"event": "a"}\n{"event": "b"}\n{"event": "c"')
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.read() == []
    with path.open("a") as f:
        f.write(', "x": 1}\n{"event": "d"}\n')
    assert [e["event"] for e in tail.read()] == ["c", "d"]
    assert tail.bad == 0


def test_broken_lines_are_skipped_and_counted(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event": "a"}\nnot json\n[1, 2]\n\n{"event": "b"}\n')
    tail = EventTail(path)
    assert [e["event"] for e in tail.read()] == ["a", "b"]
    assert tail.bad == 2
