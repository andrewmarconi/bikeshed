"""The Textual view. Thin: every tick it polls the Watcher and renders RunState through render.py."""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import RichLog, Static

from aiswarm.watch import render
from aiswarm.watch.source import Watcher


class Feed(RichLog):
    """A wrapping board feed. RichLog wraps at the width a line was written at, so it keeps the text and rewraps."""

    def __init__(self, **kwargs):
        super().__init__(wrap=True, min_width=20, **kwargs)  # markup stays off: entries are literal text
        self.texts: list[str] = []
        self.wrapped_at = 0

    def add(self, text: str, scroll_end: bool) -> None:
        self.texts.append(text)
        self.write(text, scroll_end=scroll_end)

    def on_resize(self, event: Resize) -> None:
        width = self.scrollable_content_region.width
        if self._size_known and width != self.wrapped_at and self.texts:
            at_end = self.is_vertical_scroll_end
            self.clear()
            for text in self.texts:
                self.write(text, scroll_end=at_end)
        self.wrapped_at = width
        super().on_resize(event)


class WatchApp(App):
    CSS = """
    #header { height: auto; padding: 0 1; background: $boost; }
    #body { height: 1fr; }
    #feed { width: 2fr; border: round $primary; }
    #side { width: 1fr; }
    #side Static { border: round $primary; padding: 0 1; height: auto; }
    #footer { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("f", "toggle_follow", "Follow")]

    def __init__(self, watcher: Watcher, interval: float = 1.0):
        super().__init__()
        self.watcher = watcher
        self.interval = interval
        self.following = True
        self.written = 0
        self.timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="header", markup=False)
        with Horizontal(id="body"):
            yield Feed(id="feed")
            with VerticalScroll(id="side"):  # many agents in a small terminal must not hide the lower panels
                yield Static(id="cards", markup=False)
                yield Static(id="calls", markup=False)
                yield Static(id="files", markup=False)
                yield Static(id="postrun", markup=False)
        yield Static(id="footer", markup=False)

    def on_mount(self) -> None:
        self.tick()
        self.timer = self.set_interval(self.interval, self.tick)

    def tick(self) -> None:
        self.watcher.poll()
        s = self.watcher.state
        log = self.query_one("#feed", Feed)
        new = s.feed[self.written:]
        if new:
            at_end = self.written == 0 or log.is_vertical_scroll_end  # scrolled up: don't drag the reader down
            for line in new:
                log.add(line.text, scroll_end=self.following and at_end)
            self.written = len(s.feed)
        self.query_one("#header", Static).update(render.header(s))
        self.query_one("#cards", Static).update(render.cards(s))
        post = render.show_postrun(s)
        for widget_id, content in (("calls", render.calls(s)), ("files", render.files(s))):
            widget = self.query_one(f"#{widget_id}", Static)
            widget.update(content)
            widget.display = not post
        grid = self.query_one("#postrun", Static)
        grid.update(render.postrun(s))
        grid.display = post
        self._footer()
        if s.finished and self.timer is not None:
            self.timer.stop()

    def _footer(self) -> None:
        self.query_one("#footer", Static).update(render.footer(self.watcher.state, self.following))

    def action_toggle_follow(self) -> None:
        self.following = not self.following
        if self.following:
            self.query_one("#feed", RichLog).scroll_end(animate=False)
        self._footer()
