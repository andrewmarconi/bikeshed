import json

from observer_helpers import add_entry, add_run
from rich.text import Text
from textual.widgets import RichLog, Static

from aiswarm.config import Config
from aiswarm.watch.app import WatchApp, WorkspacePopup
from aiswarm.watch.source import open_watcher


def setup(tmp_path, community, process=1):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running", goal_id="best-fruit", category="contested",
            process=process)
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango [ripe]", "method": "m"})
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    return conn, events, open_watcher(cfg, "r1")


def text(app, widget_id):
    content = app.query_one(f"#{widget_id}", Static).content
    return content.plain if isinstance(content, Text) else str(content)


def pane(app, widget_id):
    """A pane's visible text, with wrapped lines joined back up."""
    return " ".join(strip.text.strip() for strip in app.query_one(f"#{widget_id}", RichLog).lines)


def shown(app, widget_id):
    return app.query_one(f"#{widget_id}").display


def append(events, *items):
    with events.open("a") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


async def test_process_1_run_has_a_full_width_table_and_a_status_bar(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.query_one("#table", RichLog).border_title == "Table — Pick a fruit."
        assert not shown(app, "right")
        assert app.query_one("#table").region.width > 130
        assert text(app, "status").startswith("round 1/12 · rounds │ Alpha ★– idle · Bea ★– idle")
        assert "Mango [ripe]" in pane(app, "table")
        add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "Too sweet."})
        app.tick()
        await pilot.pause()
        assert "Too sweet." in pane(app, "table")


async def test_process_2_routes_entries_to_three_panes(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    add_entry(conn, "r1", "a", "think", {"text": "I doubt [it]."}, visible_to=["a"])
    add_entry(conn, "r1", "a", "private_message", {"to": "b", "text": "psst"}, visible_to=["a", "b"], to="b")
    append(events, {"event": "agent_step_started", "round": 1, "agent": "b"})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert shown(app, "right") and shown(app, "thinking") and shown(app, "private")
        assert not shown(app, "postrun")
        assert 60 <= app.query_one("#table").region.width <= 80
        assert "💭" in pane(app, "thinking") and "I doubt [it]." in pane(app, "thinking")
        assert "🔒" in pane(app, "private") and "→ Bea (b): psst" in pane(app, "private")
        assert "Mango [ripe]" in pane(app, "table") and "I doubt" not in pane(app, "table")
        assert "psst" not in pane(app, "table")
        assert text(app, "status").startswith("round 1/12 · react │") and "speaking…" in text(app, "status")


async def test_panes_wrap_and_rewrap_on_resize(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    points = " ".join(f"word{n}" for n in range(30))
    add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": points})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        log = app.query_one("#table", RichLog)
        width = log.scrollable_content_region.width
        assert all(strip.cell_length <= width for strip in log.lines)
        assert "word0" in pane(app, "table") and "word29" in pane(app, "table")
        narrow = len(log.lines)
        await pilot.resize_terminal(160, 30)
        await pilot.pause()
        assert len(log.lines) < narrow and "word29" in pane(app, "table")


async def test_follow_toggle_and_quit(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f")
        assert "follow: off" in text(app, "footer") and "w workspace" in text(app, "footer")
        await pilot.press("f")
        assert "follow: on" in text(app, "footer")
        await pilot.press("q")
    assert app.return_code == 0 or app.return_code is None


async def test_post_run_grid_replaces_private_messages(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert shown(app, "postrun") and shown(app, "thinking") and not shown(app, "private")
        assert "post-run" in text(app, "status")


async def test_process_1_post_run_opens_a_right_column_with_the_grid(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert shown(app, "right") and shown(app, "postrun")
        assert not shown(app, "thinking") and not shown(app, "private")


async def test_w_pop_up_shows_workspace_and_grid_and_keeps_updating(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community, process=2)
    add_entry(conn, "r1", "a", "write_file", {"path": "rules.md", "version": 1, "note": "n"})
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, WorkspacePopup)
        popup = lambda: str(app.screen.query_one("#popup-text", Static).content)  # noqa: E731
        assert "rules.md v1" in popup() and "Post-run" in popup()
        add_entry(conn, "r1", "b", "write_file", {"path": "notes.md", "version": 1, "note": "n"})
        add_entry(conn, "r1", "a", "think", {"text": "while the pop-up is open"}, visible_to=["a"])
        append(events, {"event": "run_closed", "reason": "budget", "call_id": None},
               {"event": "postrun_started", "run_id": "r1"})
        app.tick()
        await pilot.pause()
        assert "notes.md v1" in popup()
        await pilot.press("w")
        await pilot.pause()
        assert not isinstance(app.screen, WorkspacePopup)
        assert "while the pop-up is open" in pane(app, "thinking") and shown(app, "postrun")
