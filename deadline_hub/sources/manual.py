"""Source: deadlines the student typed in themselves (quick-add)."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..models import Deadline


class ManualSource:
    def __init__(self, name: str, items: list[dict], timezone: str = "America/Vancouver", **_):
        self.name = name
        self.items = items or []
        self.tz = ZoneInfo(timezone)

    def fetch(self) -> list[Deadline]:
        out = []
        for it in self.items:
            day = date.fromisoformat(it["date"])
            at = time.fromisoformat(it["time"]) if it.get("time") else time(23, 59)
            out.append(Deadline(source=self.name, source_uid=it["id"], title=it["title"],
                                due=datetime.combine(day, at, self.tz), course=it.get("course"),
                                description="Added by you", priority=30, all_day=not it.get("time")))
        return out
