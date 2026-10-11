import json

from observer_helpers import add_entry, add_run
from textual.widgets import Log, Static

from aiswarm.config import Config
from aiswarm.watch.app import WatchApp
from aiswarm.watch.source import open_watcher


def setup(tmp_path, community):
    store, conn = community
    add_run(conn, "r1", agents=("a", "b"), status="running", goal_id="best-fruit", category="contested")
    add_entry(conn, "r1", "a", "propose", {"answer_summary": "Mango [ripe]", "method": "m"})
    cfg = Config.model_validate({"models": {"local": {"kind": "openai", "model": "m"}}})
    cfg.community_dir, cfg.runs_dir = tmp_path / "community", tmp_path / "runs"
    events = cfg.runs_dir / "r1" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"event": "run_started", "agents": ["a", "b"]}) + "\n"
                      + json.dumps({"event": "round_started", "round": 1}) + "\n")
    return conn, events, open_watcher(cfg, "r1")


def text(app, widget_id):
    return str(app.query_one(f"#{widget_id}", Static).content)


async def test_renders_a_run_and_follows_new_entries(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "best-fruit (contested) · run r1 · round 1/12 · rounds" in text(app, "header")
        assert "Alpha (a)" in text(app, "cards")
        log = app.query_one("#feed", Log)
        assert any("Mango [ripe]" in line for line in log.lines)
        add_entry(conn, "r1", "b", "critique", {"target": 1, "about": "answer", "points": "Too sweet."})
        app.tick()
        await pilot.pause()
        assert any("Too sweet." in line for line in log.lines)
        assert app.query_one("#calls", Static).display and not app.query_one("#postrun", Static).display


async def test_follow_toggle_and_quit(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("f")
        assert "follow: off" in text(app, "footer")
        await pilot.press("f")
        assert "follow: on" in text(app, "footer")
        await pilot.press("q")
    assert app.return_code == 0 or app.return_code is None


async def test_post_run_grid_replaces_calls_after_the_rounds(tmp_path, community):
    conn, events, watcher = setup(tmp_path, community)
    app = WatchApp(watcher, interval=3600)
    async with app.run_test(size=(140, 40)) as pilot:
        with events.open("a") as f:
            f.write(json.dumps({"event": "run_closed", "reason": "budget", "call_id": None}) + "\n")
            f.write(json.dumps({"event": "postrun_started", "run_id": "r1"}) + "\n")
        app.tick()
        await pilot.pause()
        assert app.query_one("#postrun", Static).display
        assert not app.query_one("#calls", Static).display and not app.query_one("#files", Static).display
        assert "post-run" in text(app, "header")
