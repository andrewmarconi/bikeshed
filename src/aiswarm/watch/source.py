"""Read-only sources for the watcher: the run's events file and the community store."""

import json
from pathlib import Path
from typing import Any


class EventTail:
    """Reads new complete lines of an events.jsonl file; a partial last line waits for the next read."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.bad = 0

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        chunk = data[: end + 1]
        self.offset += len(chunk)
        events = []
        for line in chunk.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.bad += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                self.bad += 1
        return events
