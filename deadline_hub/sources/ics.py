"""Source: any school portal calendar feed (Moodle, Canvas, D2L, Blackboard export ICS)."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

from ..models import Deadline, extract_course_code

# Moodle/Canvas append these to event titles; strip them for a clean card title.
SUFFIX_RE = re.compile(
    r"\s*(is due|is due to be graded|closes|due|deadline|submission)\s*$", re.I)
# Events that are not deadlines.
SKIP_RE = re.compile(r"\b(opens|is available|starts)\s*$", re.I)


class IcsSource:
    def __init__(self, name: str, url: str | None = None, path: str | None = None,
                 timezone: str = "America/Vancouver", course_aliases: dict | None = None):
        if not (url or path):
            raise ValueError(f"source {name}: give a url or a path")
        self.name = name
        self.url = url
        self.path = path
        self.tz = ZoneInfo(timezone)
        self.course_aliases = {k.upper(): v for k, v in (course_aliases or {}).items()}

    def _raw(self) -> bytes:
        if self.path:
            return Path(self.path).expanduser().read_bytes()
        url = self.url.replace("webcal://", "https://", 1)
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    def _to_dt(self, value) -> datetime:
        if isinstance(value, datetime):
            return (value if value.tzinfo else value.replace(tzinfo=self.tz)).astimezone(self.tz)
        if isinstance(value, date):  # all-day: treat as due end of that day
            return datetime.combine(value, time(23, 59), self.tz)
        raise ValueError(f"unsupported date {value!r}")

    def _course(self, ev) -> str | None:
        cats = ev.get("CATEGORIES")
        cat_text = ""
        if cats is not None:
            items = cats if isinstance(cats, list) else [cats]
            cat_text = " ".join(str(c) for item in items for c in item.cats)
        for text in (cat_text, str(ev.get("SUMMARY", "")), str(ev.get("DESCRIPTION", ""))):
            for alias, code in self.course_aliases.items():
                if alias in text.upper():
                    return code
            code = extract_course_code(text)
            if code:
                return code
        return None

    def fetch(self) -> list[Deadline]:
        cal = Calendar.from_ical(self._raw())
        out = []
        for ev in cal.walk("VEVENT"):
            summary = str(ev.get("SUMMARY", "")).strip()
            if not summary or SKIP_RE.search(summary):
                continue
            # Due time: Moodle puts it in DTSTART (DTEND == DTSTART); Canvas uses DTSTART too.
            when = ev.get("DTSTART") or ev.get("DTEND") or ev.get("DUE")
            if when is None:
                continue
            uid = str(ev.get("UID") or f"{summary}|{when.dt}")
            title = SUFFIX_RE.sub("", summary).strip(" -:")
            out.append(Deadline(
                source=self.name,
                source_uid=uid,
                title=title,
                due=self._to_dt(when.dt),
                course=self._course(ev),
                url=str(ev.get("URL")) if ev.get("URL") else None,
                description=str(ev.get("DESCRIPTION", "")).strip()[:1500] or None,
                priority=10,
            ))
        return out
