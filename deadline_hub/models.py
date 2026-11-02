"""The one internal format every source produces and every destination consumes."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime

# Matches course codes like "CMPT 401", "BUSI321", "MATH-123"
COURSE_CODE_RE = re.compile(r"\b([A-Z]{3,4})[\s\-]?(\d{3}[A-Z]?)\b")

TYPE_KEYWORDS = [
    ("exam", ["final exam", "midterm", "exam", "test"]),
    ("quiz", ["quiz"]),
    ("lab", ["lab"]),
    ("project", ["project", "presentation"]),
    ("assignment", ["assignment", "homework", "hw", "problem set", "case study", "essay", "paper", "reading"]),
]


def extract_course_code(text: str | None) -> str | None:
    if not text:
        return None
    m = COURSE_CODE_RE.search(text.upper())
    return f"{m.group(1)} {m.group(2)}" if m else None


def classify(title: str) -> str:
    t = title.lower()
    for kind, words in TYPE_KEYWORDS:
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return kind
    return "other"


@dataclass
class Deadline:
    source: str            # e.g. "moodle-ics"
    source_uid: str        # the source's own ID for this item (stable across fetches)
    title: str
    due: datetime          # timezone-aware
    course: str | None = None
    kind: str = "other"
    url: str | None = None
    description: str | None = None
    priority: int = 0      # when sources disagree, the higher one wins (official dates = 10)
    all_day: bool = False  # no time was listed: remind the day before instead of hours before

    # Filled in by the engine
    id: str = field(default="", init=False)

    def __post_init__(self):
        self.id = hashlib.sha1(f"{self.source}:{self.source_uid}".encode()).hexdigest()[:16]
        if self.kind == "other":
            self.kind = classify(self.title)

    def fingerprint(self) -> str:
        """Changes whenever something a destination should update changes."""
        raw = f"{self.title}|{self.due.isoformat()}|{self.course}|{self.url}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def display_title(self) -> str:
        if self.course and self.course.replace(" ", "") not in self.title.replace(" ", "").upper():
            return f"{self.title} ({self.course})"
        return self.title

    def to_dict(self) -> dict:
        d = asdict(self)
        d["due"] = self.due.isoformat()
        return d
