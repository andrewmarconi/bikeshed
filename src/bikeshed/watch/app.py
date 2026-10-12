"""The Textual view. Thin: every tick it polls the Watcher and renders RunState through render.py."""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import RichLog, Static

from bikeshed.watch import render
from bikeshed.watch.source import Watcher

PANES = ("table", "thinking", "private")


class Feed(RichLog):
    """A wrapping pane. RichLog wraps at the width a line was written at, so it keeps the lines and rewraps."""

    def __init__(self, title: str, **kwargs):
        super().__init__(wrap=True, min_width=20, **kwargs)  # lines are Rich Text: nothing is parsed as markup
        self.border_title = Text(title)  # a str would be parsed as markup; goals may contain brackets
        self.items: list[Text] = []
        self.wrapped_at = 0

    def add(self, item: Text, scroll_end: bool) -> None:
        self.items.append(item)
        self.write(item, scroll_end=scroll_end)

    def on_resize(self, event: Resize) -> None:
        width = self.scrollable_content_region.width
        if self._size_known and width != self.wrapped_at and self.items:
            at_end = self.is_vertical_scroll_end
            self.clear()
            for item in self.items:
                self.write(item, scroll_end=at_end)
        self.wrapped_at = width
        super().on_resize(event)


class WorkspacePopup(ModalScreen):
    """Workspace files and the post-run grid. The app refreshes it every tick while it is open."""

    DEFAULT_CSS = """
    WorkspacePopup { align: center middle; }
    #popup { width: 80%; height: auto; max-height: 80%; border: round $primary; background: $panel; padding: 0 1; }
    """
    BINDINGS = [("w", "close", "Close"), ("escape", "close", "Close"), ("q", "app.quit", "Quit")]

    def __init__(self, content: str):
        super().__init__()
        self.initial = content

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="popup"):
            yield Static(self.initial, id="popup-text", markup=False)

    def show(self, content: str) -> None:
        self.query_one("#popup-text", Static).update(content)

    def action_close(self) -> None:
        self.dismiss()


class WatchApp(App):
    CSS = """
    #body { height: 1fr; }
    #table { width: 1fr; border: round $primary; }
    #right { width: 1fr; }
    #thinking, #private { height: 1fr; border: round $primary; }
    #postrun { height: 1fr; border: round $primary; padding: 0 1; }
    #status { height: auto; padding: 0 1; background: $boost; }
    #footer { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("f", "toggle_follow", "Follow"), ("w", "workspace", "Workspace")]

    def __init__(self, watcher: Watcher, interval: float = 1.0):
        super().__init__()
        self.watcher = watcher
        self.interval = interval
        self.following = True
        self.written = 0
        self.timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Feed(f"Table — {self.watcher.state.goal}", id="table")
            with Vertical(id="right"):
                yield Feed("Thinking", id="thinking")
                yield Feed("Private messages", id="private")
                yield Static(id="postrun", markup=False)
        yield Static(id="status")
        yield Static(id="footer", markup=False)

    def on_mount(self) -> None:
        # Held, not queried each tick: while the pop-up is open, queries would search the pop-up's screen.
        self.feeds = {name: self.query_one(f"#{name}", Feed) for name in PANES}
        self.right = self.query_one("#right", Vertical)
        self.grid = self.query_one("#postrun", Static)
        self.grid.border_title = "Post-run"
        self.status_bar = self.query_one("#status", Static)
        self.footer_line = self.query_one("#footer", Static)
        self.tick()
        self.timer = self.set_interval(self.interval, self.tick)

    def tick(self) -> None:
        self.watcher.poll()
        s = self.watcher.state
        colours = render.agent_colours(s.cards)
        new = s.feed[self.written:]
        if new:
            # scrolled up: don't drag the reader down
            ends = {name: self.written == 0 or feed.is_vertical_scroll_end for name, feed in self.feeds.items()}
            for line in new:
                self.feeds[line.pane].add(render.styled(line, colours), scroll_end=self.following and ends[line.pane])
            self.written = len(s.feed)
        post = render.show_postrun(s)
        two = s.process == 2
        self.right.display = two or post
        self.feeds["thinking"].display = two
        self.feeds["private"].display = two and not post
        self.grid.display = post
        self.grid.update(render.postrun(s))
        self.status_bar.update(render.status(s, colours))
        self._footer()
        if isinstance(self.screen, WorkspacePopup):
            self.screen.show(render.popup(s))
        if s.finished and self.timer is not None:
            self.timer.stop()

    def _footer(self) -> None:
        self.footer_line.update(render.footer(self.watcher.state, self.following))

    def action_toggle_follow(self) -> None:
        self.following = not self.following
        if self.following:
            for feed in self.feeds.values():
                feed.scroll_end(animate=False)
        self._footer()

    def action_workspace(self) -> None:
        self.push_screen(WorkspacePopup(render.popup(self.watcher.state)))
