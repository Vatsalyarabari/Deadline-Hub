"""Shared by every school connector: turn course texts (files, pages, syllabus) into deadlines."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, Iterable

from .. import extract
from ..models import Deadline


def deadlines_from_texts(texts: Iterable[tuple], *, source: str, course_code: str,
                         course_key: str, start: date | None, tz, default_time: time,
                         slash_order: str = "md", weights: dict | None = None) -> tuple[list[Deadline], int]:
    """texts: (label, url, text) or (label, url, text, priority). Announcements use a higher
    priority than files because they're newer. Grade weights found along the way go into `weights`."""
    ref = start or datetime.now(tz).date()
    out: list[Deadline] = []
    n = 0
    for t in texts:
        label, url, text = t[:3]
        prio = t[3] if len(t) > 3 else 0
        n += 1
        if weights is not None:
            for k, v in extract.find_weights(text).items():
                weights.setdefault(k, v)
        for f in extract.find_deadlines(text, ref=ref, term_start=start, slash_order=slash_order):
            due = datetime.combine(f.when, f.at or default_time, tz)
            ident = f.key or f"{f.title.lower()}|{f.when.isoformat()}"
            notes = [f"Found in {label}", f'"{f.context}"']
            if not f.at:
                notes.append("No time listed; reminder set for the evening before.")
            if f.approximate:
                notes.append("Only the week was listed; date is that week's Friday.")
            out.append(Deadline(
                source=f"{source}-files",
                source_uid=f"{course_key}|{'ann|' if prio else ''}{ident}", title=f.title,
                due=due, course=course_code, url=url, description="\n".join(notes),
                priority=prio, all_day=f.at is None))
    return out, n


class FileCache:
    """Downloads a course file once and again only when its 'version' (modified time) changes."""

    def __init__(self, root: Path, file_types: list[str], max_bytes: float):
        self.root = Path(root)
        self.file_types = [t.lower() for t in file_types]
        self.max_bytes = max_bytes

    def wanted(self, name: str, size: int | None) -> bool:
        return Path(name).suffix.lower() in self.file_types and (size or 0) <= self.max_bytes

    def get(self, course_key, item_key, name: str, version, download: Callable[[Path], None]) -> Path:
        safe = re.sub(r"[^\w.\- ]", "_", name)
        dest = self.root / str(course_key) / f"{item_key}_{safe}"
        meta = dest.with_suffix(dest.suffix + ".json")
        if dest.exists() and meta.exists() and json.loads(meta.read_text()).get("version") == version:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        download(dest)
        meta.write_text(json.dumps({"version": version}))
        return dest
