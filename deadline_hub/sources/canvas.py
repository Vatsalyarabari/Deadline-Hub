"""Source: Canvas LMS (e.g. https://canvas.school.edu or https://school.instructure.com).

Uses a personal access token the student makes in Canvas:
Account -> Settings -> Approved Integrations -> + New Access Token.

Reads official due dates (assignments, quizzes, discussions, calendar events) from the
Canvas planner, then scans each course's syllabus, front page and files for extra dates.
Everything runs on the student's computer.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .. import extract
from ..models import Deadline, extract_course_code
from .scanning import FileCache, deadlines_from_texts

log = logging.getLogger("deadline_hub.canvas")

DEFAULT_FILE_TYPES = [".pdf", ".docx", ".pptx", ".html", ".htm", ".txt"]


class CanvasError(RuntimeError):
    pass


class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "DeadlineHub/0.3"})
        self.timeout = timeout

    def get(self, path: str, **params):
        r = self.s.get(f"{self.base}/api/v1{path}", params=params, timeout=self.timeout)
        if r.status_code == 401:
            raise CanvasError("Canvas rejected the access token (expired or deleted).")
        if r.status_code >= 400:
            raise CanvasError(f"{path}: HTTP {r.status_code}")
        return r.json()

    def get_all(self, path: str, limit_pages: int = 20, **params):
        """Follow Canvas pagination (Link: rel=next)."""
        params.setdefault("per_page", 100)
        url, out = f"{self.base}/api/v1{path}", []
        for _ in range(limit_pages):
            r = self.s.get(url, params=params, timeout=self.timeout)
            if r.status_code == 401:
                raise CanvasError("Canvas rejected the access token (expired or deleted).")
            if r.status_code >= 400:
                raise CanvasError(f"{path}: HTTP {r.status_code}")
            out.extend(r.json())
            nxt = r.links.get("next", {}).get("url")
            if not nxt:
                break
            url, params = nxt, None
        return out

    def download(self, url: str, dest: Path):
        with self.s.get(url, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)


KIND_BY_TYPE = {"quiz": "quiz", "discussion_topic": "assignment", "assignment": "assignment",
                "calendar_event": "other", "wiki_page": "other", "planner_note": "other"}


class CanvasSource:
    def __init__(self, name: str, base_url: str, token: str, timezone: str = "America/Vancouver",
                 course_aliases: dict | None = None, scan_files: bool = True,
                 file_types: list[str] | None = None, max_file_mb: float = 30,
                 cache_dir: str = ".cache/canvas", slash_order: str = "md",
                 default_due_time: str = "23:59", lookahead_days: int = 120):
        if not token:
            raise ValueError("Canvas access token missing")
        self.name = name
        self.client = CanvasClient(base_url, token)
        self.tz = ZoneInfo(timezone)
        self.course_aliases = {k.upper(): v for k, v in (course_aliases or {}).items()}
        self.scan_files = scan_files
        self.cache = FileCache(Path(cache_dir), file_types or DEFAULT_FILE_TYPES, max_file_mb * 1024 * 1024)
        self.slash_order = slash_order
        h, m = default_due_time.split(":")
        self.default_time = time(int(h), int(m))
        self.lookahead = lookahead_days
        self.report: list[str] = []
        self.weights: dict[str, dict] = {}

    def _code(self, c: dict) -> str:
        for text in (c.get("course_code", ""), c.get("name", "")):
            for alias, code in self.course_aliases.items():
                if alias in text.upper():
                    return code
            code = extract_course_code(text)
            if code:
                return code
        return c.get("course_code") or c.get("name") or str(c["id"])

    def _dt(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(self.tz)

    def courses(self) -> list[dict]:
        courses = self.client.get_all("/courses", enrollment_state="active",
                                      **{"include[]": ["term", "syllabus_body"]})
        now = datetime.now(self.tz)
        out = []
        for c in courses:
            if not isinstance(c, dict) or c.get("access_restricted_by_date"):
                continue
            end = c.get("end_at") or (c.get("term") or {}).get("end_at")
            if end and self._dt(end) < now - timedelta(days=14):
                continue
            c["_code"] = self._code(c)
            out.append(c)
        return out

    # ---- official dates
    def _planner(self, courses: list[dict]) -> list[Deadline]:
        code = {c["id"]: c["_code"] for c in courses}
        start = datetime.now(self.tz) - timedelta(days=1)
        end = datetime.now(self.tz) + timedelta(days=self.lookahead)
        items = self.client.get_all("/planner/items", start_date=start.isoformat(), end_date=end.isoformat())
        out = []
        for it in items:
            p = it.get("plannable") or {}
            when = it.get("plannable_date") or p.get("due_at") or p.get("todo_date")
            if not when:
                continue
            ptype = it.get("plannable_type", "")
            sub_types = p.get("submission_types") or []
            kind = "quiz" if ptype == "quiz" or "online_quiz" in sub_types else KIND_BY_TYPE.get(ptype, "other")
            title = p.get("title") or p.get("name") or "Untitled"
            url = it.get("html_url")
            if url and url.startswith("/"):
                url = self.client.base + url
            d = Deadline(source=self.name, source_uid=f"{ptype}:{it.get('plannable_id')}", title=title,
                         due=self._dt(when), course=code.get(it.get("course_id")) or it.get("context_name"),
                         url=url, priority=20)
            if kind != "other":
                d.kind = kind
            out.append(d)
        return out

    # ---- files and pages
    def _texts(self, c: dict):
        base = self.client.base
        cid = c["id"]
        try:  # recent announcements first ("quiz moved to Friday")
            start = (datetime.now(self.tz) - timedelta(days=45)).date().isoformat()
            for a in self.client.get_all("/announcements", limit_pages=1, start_date=start,
                                         **{"context_codes[]": f"course_{cid}"}):
                yield (f"announcement '{a.get('title', '')}'", a.get("html_url") or f"{base}/courses/{cid}",
                       f"{a.get('title', '')}\n{extract.html_to_text(a.get('message', ''))}", 5)
        except CanvasError:
            pass
        if c.get("syllabus_body"):
            yield ("the syllabus", f"{base}/courses/{cid}/assignments/syllabus",
                   extract.html_to_text(c["syllabus_body"]))
        try:
            fp = self.client.get(f"/courses/{cid}/front_page")
            if fp.get("body"):
                yield ("the course home page", f"{base}/courses/{cid}", extract.html_to_text(fp["body"]))
        except CanvasError:
            pass
        try:
            files = self.client.get_all(f"/courses/{cid}/files", limit_pages=5)
        except CanvasError as e:  # files tab hidden by the instructor is common
            self.report.append(f"  {c['_code']}: files not available ({e})")
            files = []
        for f in files:
            name = f.get("display_name") or f.get("filename") or ""
            if not f.get("url") or not self.cache.wanted(name, f.get("size")):
                continue
            try:
                path = self.cache.get(cid, f["id"], name, f.get("updated_at"),
                                      lambda dest, u=f["url"]: self.client.download(u, dest))
                text = extract.text_from_file(path)
            except Exception as e:
                self.report.append(f"  could not read {name}: {e}")
                continue
            yield (name, f"{base}/courses/{cid}/files/{f['id']}", text)

    def _file_deadlines(self, c: dict) -> list[Deadline]:
        start_iso = c.get("start_at") or (c.get("term") or {}).get("start_at")
        start = self._dt(start_iso).date() if start_iso else None
        out, n = deadlines_from_texts(self._texts(c), source=self.name, course_code=c["_code"],
                                      course_key=c["id"], start=start, tz=self.tz,
                                      default_time=self.default_time, slash_order=self.slash_order,
                                      weights=self.weights.setdefault(c["_code"], {}))
        self.report.append(f"{c['_code']}: read {n} pages/files, found {len(out)} dates")
        return out

    def fetch(self) -> list[Deadline]:
        self.report = []
        courses = self.courses()
        self.report.append("Courses: " + ", ".join(c["_code"] for c in courses))
        items = self._planner(courses)
        self.report.append(f"Official Canvas due dates: {len(items)}")
        if self.scan_files:
            for c in courses:
                try:
                    items.extend(self._file_deadlines(c))
                except CanvasError as e:
                    self.report.append(f"{c['_code']}: could not read course content ({e})")
        return items


    def grades(self) -> list[dict]:
        out = []
        for c in self.client.get_all("/courses", enrollment_state="active",
                                     **{"include[]": ["total_scores", "term"]}):
            if not isinstance(c, dict) or c.get("access_restricted_by_date"):
                continue
            enr = next((e for e in c.get("enrollments", []) if e.get("type") == "student"), None)
            if not enr:
                continue
            items = []
            try:
                for g in self.client.get_all(f"/courses/{c['id']}/assignment_groups", limit_pages=1):
                    items.append({"name": g.get("name"), "weight": g.get("group_weight") or None, "percent": None})
            except CanvasError:
                pass
            out.append({"course": self._code(c), "name": c.get("name"),
                        "percent": enr.get("computed_current_score"), "graded_weight": None, "items": items,
                        "url": f"{self.client.base}/courses/{c['id']}/grades"})
        return out


def verify(base_url: str, token: str) -> str:
    """Returns the student's name if the token works."""
    me = CanvasClient(base_url, token, timeout=20).get("/users/self")
    return me.get("name") or me.get("short_name") or "Canvas user"
