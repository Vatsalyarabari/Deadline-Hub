"""Find deadlines in course files with plain pattern matching. No AI, nothing leaves your computer.

Handles things like:
    "Midterm 2 – Friday, Nov 13 at 10:00 AM"
    "HW #5 due 2026-11-03"
    "Quiz 3 (Oct 2) and Quiz 4 (Oct 16)"
    "Week 9 Friday: Lab 6 due"          (needs the term start date)
    "13 November   Final project"
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------- text from files

class _Strip(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(raw: str) -> str:
    p = _Strip()
    p.feed(raw or "")
    return html.unescape("".join(p.out))


def text_from_file(path: str | Path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        lines = [p.text for p in d.paragraphs]
        for table in d.tables:  # schedules usually live in tables: keep each row on one line
            for row in table.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(lines)
    if ext == ".pptx":
        from pptx import Presentation
        lines = []
        for slide in Presentation(str(path)).slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    lines.extend(p.text for p in shape.text_frame.paragraphs)
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        lines.append(" | ".join(c.text for c in row.cells))
        return "\n".join(lines)
    if ext in (".html", ".htm"):
        return html_to_text(path.read_text(errors="ignore"))
    if ext in (".txt", ".md", ".csv"):
        return path.read_text(errors="ignore")
    return ""


# ---------------------------------------------------------------- patterns

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DAYNAMES = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s*"
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

DATE_RES = [
    # Nov 13 / Friday, November 13th, 2026
    re.compile(rf"(?:{_DAYNAMES})?\b{_MON}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>20\d\d))?", re.I),
    # 13 Nov / 13th of November 2026
    re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\b(?:,?\s+(?P<year>20\d\d))?", re.I),
    # 2026-11-13
    re.compile(r"\b(?P<year>20\d\d)-(?P<mnum>\d{1,2})-(?P<day>\d{1,2})\b"),
    # 11/13 or 11/13/2026 (month first; set slash_order: dm in config for 13/11)
    re.compile(r"(?<![\d/.])(?P<a>\d{1,2})/(?P<b>\d{1,2})(?:/(?P<year>(?:20)?\d\d))?(?![\d/])"),
]
WEEK_RE = re.compile(rf"\bweek\s*(?P<n>\d{{1,2}})\b(?:[^a-z\d]{{0,6}}(?P<wd>{_DAYNAMES}))?", re.I)
WEEKDAY_ONLY_RE = re.compile(rf"\b(?P<wd>{_DAYNAMES})", re.I)
TIME_RE = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])|\b(?P<word>noon|midnight)\b", re.I)

ITEM_RE = re.compile(
    r"\b(?P<kind>final\s+exam(?:ination)?|final\s+project|mid-?term(?:\s+exam)?|exam(?:ination)?|test|quiz(?:zes)?"
    r"|assignment|homework|hw|lab(?:oratory)?|project|presentation|essay|paper|case\s+study|problem\s+set"
    r"|proposal|reflection\s+paper|portfolio)\b"
    r"(?:\s*(?:#|no\.?|number)?\s*(?P<num>\d{1,2}[a-z]?)\b(?![:.]\d|\s*%|\s*(?:am|pm|a\.m|p\.m)\b))?",
    re.I)
OPENS_RE = re.compile(r"^\W{0,3}(opens?|released?|available|starts?|begins?|assigned|handed\s+out)\b", re.I)
DUE_RE = re.compile(r"\b(due|deadline|submit|submission|hand\s*in|closes?)\b", re.I)
SKIP_RE = re.compile(
    r"\b(no\s+class(es)?|holiday|reading\s+(break|week)|thanksgiving|remembrance|last\s+day\s+to"
    r"|withdraw|add/drop|drop\s+deadline|office\s+hours?|published|copyright|revised|updated)\b", re.I)

CANON = {
    "hw": "HW", "homework": "HW", "midterm exam": "Midterm", "mid-term": "Midterm", "midterm": "Midterm",
    "mid-term exam": "Midterm", "examination": "Exam", "final examination": "Final Exam",
    "laboratory": "Lab", "quizzes": "Quiz",
}
UNIQUE_KINDS = {"final exam", "final project", "midterm"}


def canonical_item(kind: str, num: str | None) -> str:
    k = re.sub(r"\s+", " ", kind.lower())
    name = CANON.get(k) or k.title().replace("Hw", "HW")
    return f"{name} {num.upper()}" if num else name


def short_key(title: str) -> str | None:
    """'Quiz 8 closes' -> 'quiz 8'; 'HW #5' -> 'hw 5'. None when too generic to match on."""
    fallback = None
    for m in ITEM_RE.finditer(title or ""):
        key = canonical_item(m.group("kind"), m.group("num")).lower()
        if m.group("num"):
            return key  # "Chapter 17 Written Homework (HW #5)" -> "hw 5"
        if key in UNIQUE_KINDS and fallback is None:
            fallback = key
    return fallback


# ---------------------------------------------------------------- extraction

@dataclass
class Found:
    title: str
    when: date
    at: time | None
    context: str
    key: str | None
    approximate: bool = False


def _year_for(month: int, day: int, ref: date) -> int:
    """Pick the year that puts the date closest to the term (ref = term start or today)."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        dist = (d - ref).days
        score = abs(dist - 60)  # most deadlines fall in the ~4 months after term start
        if best is None or score < best[0]:
            best = (score, y)
    return best[1] if best else ref.year


def _dates_in(line: str, ref: date, slash_order: str):
    """Yield (start, end, date) for every explicit date on the line."""
    taken: list[tuple[int, int]] = []
    for rx in DATE_RES:
        for m in rx.finditer(line):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            g = m.groupdict()
            try:
                if g.get("mon"):
                    month = MONTHS[g["mon"].lower().rstrip(".")]
                    day = int(g["day"])
                elif g.get("mnum"):
                    month, day = int(g["mnum"]), int(g["day"])
                else:
                    a, b = int(g["a"]), int(g["b"])
                    month, day = (a, b) if slash_order == "md" else (b, a)
                year = g.get("year")
                if year:
                    year = int(year) + (2000 if len(year) == 2 else 0)
                else:
                    year = _year_for(month, day, ref)
                d = date(year, month, day)
            except (ValueError, KeyError):
                continue
            taken.append((m.start(), m.end()))
            yield m.start(), m.end(), d


def _time_in(line: str) -> time | None:
    m = TIME_RE.search(line)
    if not m:
        return None
    if m.group("word"):
        return time(12, 0) if m.group("word").lower() == "noon" else time(23, 59)
    h, mi = int(m.group("h")), int(m.group("m") or 0)
    if h > 12 or mi > 59:
        return None
    pm = m.group("ap").lower().startswith("p")
    h = (h % 12) + (12 if pm else 0)
    return time(h, mi)


def _weekday(text: str | None) -> int | None:
    if not text:
        return None
    return WEEKDAYS.get(text.strip().lower()[:3])


def _week_date(line: str, term_start: date | None):
    if not term_start:
        return None
    m = WEEK_RE.search(line)
    if not m:
        return None
    n = int(m.group("n"))
    if not 1 <= n <= 16:
        return None
    wd = _weekday(m.group("wd"))
    if wd is None:
        other = WEEKDAY_ONLY_RE.search(line[m.end():])
        wd = _weekday(other.group("wd")) if other else None
    approximate = wd is None
    monday = term_start - timedelta(days=term_start.weekday())
    return monday + timedelta(weeks=n - 1, days=4 if wd is None else wd), m.start(), approximate


def _clean_title(line: str) -> str:
    t = line
    for rx in DATE_RES + [TIME_RE, WEEK_RE]:
        t = rx.sub(" ", t)
    t = re.sub(r"\b(due|by|on|at|deadline)\b", " ", t, flags=re.I)
    t = re.sub(r"[|•·\t/]+", " ", t)
    t = re.sub(r"^\s*\d{1,2}[.)]?\s+", "", t)  # leading row/question number
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—:,;()[]")
    return t


WK_AFTER_RE = re.compile(r"\W{0,3}(?:wk|week)\.?\s*(\d{1,2})\b", re.I)


def _wk_after(line: str, m):
    """'Quiz: Wk 7' -> '7' (CMPT 401 style: quiz named after the week it covers)."""
    w = WK_AFTER_RE.match(line[m.end():])
    return w.group(1) if w else None


def _items(line: str):
    """Item mentions on a line, minus 'Quiz 8 opens'-style lines and generic words
    that overlap a more specific item ('Final project presentation' -> just 'Final Project')."""
    found = []
    for m in ITEM_RE.finditer(line):
        rest = line[m.end():]
        if OPENS_RE.search(rest) and not DUE_RE.search(rest):
            continue
        found.append(m)
    specific = [m for m in found if m.group("num") or short_key(m.group(0)) or _wk_after(line, m)]
    if specific:
        found = specific
    return found


def find_deadlines(text: str, *, ref: date, term_start: date | None = None,
                   slash_order: str = "md") -> list[Found]:
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    out: list[Found] = []
    seen: set[tuple] = set()

    for i, line in enumerate(lines):
        if SKIP_RE.search(line) or len(line) > 400:
            continue
        dates = list(_dates_in(line, ref, slash_order))
        approximate = False
        if not dates:
            wk = _week_date(line, term_start)
            if wk:
                d, pos, approximate = wk
                dates = [(pos, pos, d)]
        if not dates:
            continue

        items = _items(line)
        context = line
        # Table cells often get split onto their own line in PDFs: borrow the item from a neighbour.
        if not items and not DUE_RE.search(line):
            for j in (i + 1, i - 1):
                if 0 <= j < len(lines) and not list(_dates_in(lines[j], ref, slash_order)) \
                        and not SKIP_RE.search(lines[j]):
                    nb = [m for m in _items(lines[j]) if m.group("num") or short_key(m.group(0))]
                    if nb:
                        items = [(m, lines[j]) for m in nb]
                        context = f"{line} / {lines[j]}" if j > i else f"{lines[j]} / {line}"
                        break
            if not items:
                continue
            pairs = [(m, dates[-1][2], src) for m, src in items]
        elif not items:
            # "Choose and audit a dataset — due Nov 30": no keyword, but clearly a deadline
            title = _clean_title(line)
            if len(title) < 4:
                continue
            pairs = [(title, dates[-1][2], line)]
        else:
            pairs = []
            due = DUE_RE.search(line)
            for m in items:
                if len(dates) == 1:
                    d = dates[0][2]
                elif due and len(items) == 1:
                    after = [x for x in dates if x[0] >= due.start()]
                    d = (after[0] if after else dates[-1])[2]
                else:
                    d = min(dates, key=lambda x: abs(x[0] - m.start()))[2]
                pairs.append((m, d, line))

        at = _time_in(line)
        for item, d, src in pairs:
            if isinstance(item, str):
                title, key = item, short_key(item)
            else:
                title = canonical_item(item.group("kind"), item.group("num"))
                key = short_key(title)
                wk = None if item.group("num") else _wk_after(src, item)
                if wk:
                    title, key = f"{title} (Wk {wk})", None
                elif not item.group("num") and key is None:
                    # generic word like "project": use the line for a more specific title
                    cleaned = _clean_title(src)
                    if 4 <= len(cleaned) <= 80:
                        title = cleaned
            sig = (title.lower(), d)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(Found(title=title, when=d, at=at, context=context[:300], key=key,
                             approximate=approximate))
    return out


# ---------------------------------------------------------------- grade weights

PCT_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s*%")
CATEGORY_RE = re.compile(
    r"\b(quiz(?:zes)?|assignments?|homework|hws?|labs?|projects?|presentations?|participation|"
    r"attendance|essays?|papers?|midterms?|final\s+exam|final\s+project|exams?|tests?|reflections?|"
    r"case\s+stud(?:y|ies)|problem\s+sets?|discussions?|journals?)\b", re.I)
_CAT_CANON = [("final exam", "final exam"), ("final project", "final project"), ("midterm", "midterm"),
              ("quiz", "quiz"), ("lab", "lab"), ("hw", "assignment"), ("homework", "assignment"),
              ("assignment", "assignment"), ("problem set", "assignment"), ("case stud", "assignment"),
              ("project", "project"), ("presentation", "project"), ("essay", "assignment"),
              ("paper", "assignment"), ("exam", "exam"), ("test", "exam"), ("participation", "participation"),
              ("attendance", "participation"), ("discussion", "participation"), ("reflection", "assignment"),
              ("journal", "assignment")]


def find_weights(text: str) -> dict[str, float]:
    """Grading breakdown lines like 'Midterm 2 .... 20%' or 'Quizzes (10%)'.
    Returns {'midterm 2': 20, 'category:quiz': 10, ...}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or len(line) > 160:
            continue
        if re.search(r"\b(late|penalty|deduct|per day|off\b|bonus|curve|attempts?)\b", line, re.I):
            continue
        pcts = PCT_RE.findall(line)
        if len(pcts) != 1:
            continue
        pct = float(pcts[0])
        if not 0 < pct <= 70:
            continue
        key = short_key(line)
        if key:
            out.setdefault(key, pct)
            continue
        m = CATEGORY_RE.search(line)
        if m:
            word = m.group(1).lower()
            canon = next((c for w, c in _CAT_CANON if word.startswith(w)), None)
            if canon:
                out.setdefault(f"category:{canon}", pct)
    return out


# ---------------------------------------------------------------- quick add

_REL_RE = re.compile(r"\b(today|tonight|tomorrow|tmrw|tmr)\b", re.I)
_NEXT_WD_RE = re.compile(rf"\b(next\s+)?(?P<wd>{_DAYNAMES})", re.I)


def parse_quick_add(text: str, now: datetime) -> dict:
    """'BUSI 222 group meeting Thu 3pm' -> {title, course, date, time}. Raises ValueError if no date."""
    from .models import extract_course_code
    raw = re.sub(r"\s+", " ", text).strip()
    course = extract_course_code(raw)
    today = now.date()
    when = None
    consumed: list[tuple[int, int]] = []
    dates = list(_dates_in(raw, today, "md"))
    if dates:
        s, e, when = dates[0]
        consumed.append((s, e))
    else:
        m = _REL_RE.search(raw)
        if m:
            w = m.group(1).lower()
            when = today if w in ("today", "tonight") else today + timedelta(days=1)
            consumed.append(m.span())
        else:
            for m in _NEXT_WD_RE.finditer(raw):
                wd = _weekday(m.group("wd"))
                if wd is None:
                    continue
                ahead = (wd - today.weekday()) % 7 or 7
                if m.group(1):  # "next Friday" = the one after this week's
                    ahead += 7 if ahead < 7 else 0
                when = today + timedelta(days=ahead)
                consumed.append(m.span())
                break
    if when is None:
        raise ValueError("Add a date, like 'Nov 14', 'Friday' or 'tomorrow'.")
    at = _time_in(raw)
    tm = TIME_RE.search(raw)
    if tm:
        consumed.append(tm.span())
    title = raw
    for s, e in sorted(consumed, reverse=True):
        title = title[:s] + " " + title[e:]
    if course:
        title = re.sub(re.escape(course).replace(r"\ ", r"[\s\-]?"), " ", title, flags=re.I)
    title = re.sub(r"\b(due|by|on|at)\b", " ", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title).strip(" -–—:,;")
    if not title:
        raise ValueError("Add a short title, like 'group meeting'.")
    title = title[0].upper() + title[1:]
    return {"title": title[:120], "course": course, "date": when.isoformat(),
            "time": at.strftime("%H:%M") if at else None}
