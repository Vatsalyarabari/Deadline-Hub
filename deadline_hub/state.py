"""Remembers what has been synced where, so re-runs never create duplicates."""
from __future__ import annotations

import json
from pathlib import Path


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"items": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        self.data.setdefault("items", {})

    def get(self, deadline_id: str) -> dict | None:
        return self.data["items"].get(deadline_id)

    def remote_id(self, deadline_id: str, destination: str) -> str | None:
        item = self.get(deadline_id)
        return item.get("remote", {}).get(destination) if item else None

    def record(self, deadline, destination: str, remote_id: str | None):
        item = self.data["items"].setdefault(deadline.id, {"remote": {}})
        item["fingerprint"] = deadline.fingerprint()
        item["title"] = deadline.display_title
        item["due"] = deadline.due.isoformat()
        if remote_id is not None:
            item["remote"][destination] = remote_id

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)
