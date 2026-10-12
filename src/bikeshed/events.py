"""Live event stream for a run: one JSON object per line, for read-only consumers."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bikeshed.db import now_iso


class EventLog:
    def __init__(self, path: Path, sink: Callable[[dict[str, Any]], None] | None = None):
        self.path = path
        self._sink = sink
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, name: str, /, **data: Any) -> dict[str, Any]:
        event = {"ts": now_iso(), "event": name, **data}
        with self.path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self._sink:
            self._sink(event)
        return event


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
