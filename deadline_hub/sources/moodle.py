"""Source: crawl your Moodle site (e.g. learn.twu.ca) directly.

For every current course it:
  1. Reads official due dates for assignments and quizzes from Moodle itself.
  2. Walks every section, page, label and file (PDF, Word, PowerPoint, HTML)
     and finds dates in them with pattern matching (see extract.py).

Everything runs on your computer. Files are cached in .cache/moodle and only
re-downloaded when the instructor changes them.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .. import extract
from ..models import Deadline, extract_course_code

log = logging.getLogger("deadline_hub.moodle")

DEFAULT_FILE_TYPES = [".pdf", ".docx", ".pptx", ".html", ".htm", ".txt"]


class MoodleError(RuntimeError):
    pass


class MoodleClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "DeadlineHub/0.2"

    @staticmethod
    def _flatten(params: dict, prefix: str = "") -> dict:
        out = {}
        for k, v in params.items():
            key = f"{prefix}[{k}]" if prefix else k
            if isinstance(v, dict):
                out.update(MoodleClient._flatten(v, key))
            elif isinstance(v, (list, tuple)):
                out.update(MoodleClient._flatten(dict(enumerate(v)), key))
            else:
                out[key] = v
        return out

    def call(self, function: str, **params):
        data = {"wstoken": self.token, "wsfunction": function, "moodlewsrestformat": "json",
                **self._flatten(params)}
        r = self.s.post(f"{self.base}/webservice/rest/server.php", data=data, timeout=self.timeout)
        r.raise_for_status()
        res = r.json()
        if isinstance(res, dict) and res.get("exception"):
            raise MoodleError(f"{function}: {res.get('errorcode')}: {res.get('message')}")
        return res

    def download(self, fileurl: str, dest: Path):
        sep = "&" if "?" in fileurl else "?"
        with self.s.get(f"{fileurl}{sep}token={self.token}", stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "")
            if "application/json" in ctype:  # Moodle reports file errors as JSON
                raise MoodleError(f"download failed: {r.text[:200]}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)


def get_token(base_url: str, username: str, password: str) -> str:
    """Exchange a Moodle username/password for a personal API token (same as the Moodle app does)."""
    r = requests.post(f"{base_url.rstrip('/')}/login/token.php",
                      data={"username": username, "password": password,
                            "service": "moodle_mobile_app"}, timeout=30)
    r.raise_for_status()
    res = r.json()
    if "token" not in res:
        raise MoodleError(res.get("error") or res.get("errorcode") or str(res))
    return res["token"]


def token_from_launch_url(text: str) -> str:
    """Decode the token from a 'moodlemobile://token=...' link (used when the site uses SSO)."""
    import base64
    text = text.strip().strip('"').strip("'")
    if "launch.php" in text:
        raise MoodleError("that's the launch.php page address. Copy the URL of the red "
                          "'token=...' row in the Network tab instead (right-click -> Copy -> Copy URL).")
    if "token=" not in text:
        if not re.fullmatch(r"[A-Za-z0-9]{20,64}", text):
            raise MoodleError("that doesn't look like a Moodle key or a moodlemobile://token= link.")
        return text  # already a plain token
    b64 = text.split("token=", 1)[1].split("&")[0].strip()
    b64 += "=" * (-len(b64) % 4)
    parts = base64.b64decode(b64).decode().split(":::")
    if len(parts) < 2:
        raise MoodleError("could not read a token from that link")
    return parts[1]


class MoodleSource:
    def __init__(self, name: str, base_url: str, token: str, timezone: str = "America/Vancouver",
                 course_aliases: dict | None = None, courses: list[str] | None = None,
                 scan_files: bool = True, file_types: list[str] | None = None,
                 max_file_mb: float = 30, cache_dir: str = ".cache/moodle",
                 term_start: str | None = None, slash_order: str = "md",
                 default_due_time: str = "23:59", include_past_courses_days: int = 14):
        if not token:
            raise ValueError("Moodle token missing: run  python -m deadline_hub moodle-login")
        self.name = name
        self.client = MoodleClient(base_url, token)
        self.tz = ZoneInfo(timezone)
        self.course_filter = {c.upper().replace(" ", "") for c in (courses or [])}
        self.course_aliases = {k.upper(): v for k, v in (course_aliases or {}).items()}
        self.scan_files = scan_files
        self.file_types = [t.lower() for t in (file_types or DEFAULT_FILE_TYPES)]
        self.max_bytes = max_file_mb * 1024 * 1024
        self.cache = Path(cache_dir)
        self.term_start = date.fromisoformat(term_start) if term_start else None
        self.slash_order = slash_order
        h, m = default_due_time.split(":")
        self.default_time = time(int(h), int(m))
        self.grace = timedelta(days=include_past_courses_days)
        self.report: list[str] = []  # what was scanned, for `scan` output
        self.weights: dict[str, dict] = {}  # course -> grading breakdown found in files
        self.news_forums: dict[int, int] = {}  # course id -> announcements forum id

    # ------------------------------------------------------------ courses
    def _course_code(self, c: dict) -> str:
        for text in (c.get("shortname", ""), c.get("fullname", "")):
            for alias, code in self.course_aliases.items():
                if alias in text.upper():
                    return code
            code = extract_course_code(text)
            if code:
                return code
        return c.get("shortname") or str(c["id"])

    def current_courses(self) -> list[dict]:
        info = self.client.call("core_webservice_get_site_info")
        courses = self.client.call("core_enrol_get_users_courses", userid=info["userid"])
        now = datetime.now(self.tz)
        out = []
        for c in courses:
            code = self._course_code(c)
            c["_code"] = code
            if self.course_filter:
                if code.upper().replace(" ", "") not in self.course_filter:
                    continue
            else:
                end = c.get("enddate") or 0
                start = c.get("startdate") or 0
                if end and datetime.fromtimestamp(end, self.tz) < now - self.grace:
                    continue
                if start and datetime.fromtimestamp(start, self.tz) > now + timedelta(days=45):
                    continue
                if c.get("hidden") or c.get("visible") == 0:
                    continue
            out.append(c)
        return out

    # ------------------------------------------------------------ official dates
    def _ts(self, ts: int) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def _api_deadlines(self, courses: list[dict]) -> list[Deadline]:
        ids = [c["id"] for c in courses]
        code = {c["id"]: c["_code"] for c in courses}
        out: list[Deadline] = []
        base = self.client.base

        try:
            res = self.client.call("mod_assign_get_assignments", courseids=ids)
            for c in res.get("courses", []):
                for a in c.get("assignments", []):
                    due = a.get("duedate") or a.get("cutoffdate")
                    if not due:
                        continue
                    out.append(Deadline(
                        source=self.name, source_uid=f"assign:{a['id']}", title=a["name"],
                        due=self._ts(due), course=code.get(c["id"]), kind="assignment",
                        url=f"{base}/mod/assign/view.php?id={a.get('cmid')}", priority=20))
        except (MoodleError, requests.RequestException) as e:
            log.warning("assignments unavailable: %s", e)

        try:
            res = self.client.call("mod_quiz_get_quizzes_by_courses", courseids=ids)
            for q in res.get("quizzes", []):
                if not q.get("timeclose"):
                    continue
                out.append(Deadline(
                    source=self.name, source_uid=f"quiz:{q['id']}", title=q["name"],
                    due=self._ts(q["timeclose"]), course=code.get(q["course"]),
                    kind="quiz", url=f"{base}/mod/quiz/view.php?id={q.get('coursemodule')}",
                    priority=20))
        except (MoodleError, requests.RequestException) as e:
            log.warning("quizzes unavailable: %s", e)

        for fn, key, field_names, kind, path in [
            ("mod_forum_get_forums_by_courses", None, ("duedate",), "assignment", "forum"),
            ("mod_workshop_get_workshops_by_courses", "workshops", ("submissionend",), "project", "workshop"),
            ("mod_lesson_get_lessons_by_courses", "lessons", ("deadline",), "assignment", "lesson"),
        ]:
            try:
                res = self.client.call(fn, courseids=ids)
            except (MoodleError, requests.RequestException):
                continue
            items = res if key is None else res.get(key, [])
            if path == "forum":
                self.news_forums = {f.get("course"): f["id"] for f in items if f.get("type") == "news"}
            for it in items:
                due = next((it.get(f) for f in field_names if it.get(f)), None)
                if not due:
                    continue
                out.append(Deadline(
                    source=self.name, source_uid=f"{path}:{it['id']}", title=it["name"],
                    due=self._ts(due), course=code.get(it.get("course")), kind=kind,
                    url=f"{base}/mod/{path}/view.php?id={it.get('cmid') or it.get('coursemodule')}",
                    priority=20))
        return out

    # ------------------------------------------------------------ files and pages
    def _cached_file(self, course_id: int, module_id, f: dict) -> Path | None:
        name = f.get("filename", "")
        ext = Path(name).suffix.lower()
        if ext not in self.file_types or f.get("type", "file") != "file":
            return None
        if (f.get("filesize") or 0) > self.max_bytes:
            self.report.append(f"  skipped (too large): {name}")
            return None
        safe = re.sub(r"[^\w.\- ]", "_", name)
        dest = self.cache / str(course_id) / f"{module_id}_{safe}"
        meta = dest.with_suffix(dest.suffix + ".json")
        stamp = f.get("timemodified")
        if dest.exists() and meta.exists() and json.loads(meta.read_text()).get("timemodified") == stamp:
            return dest
        self.client.download(f["fileurl"], dest)
        meta.write_text(json.dumps({"timemodified": stamp, "fileurl": f["fileurl"]}))
        return dest

    def _announcements(self, course: dict):
        """Recent announcements: 'Quiz moved to Friday' style changes."""
        fid = self.news_forums.get(course["id"])
        if not fid:
            return
        try:
            res = self.client.call("mod_forum_get_forum_discussions", forumid=fid, sortorder=1,
                                   page=0, perpage=15)
        except (MoodleError, requests.RequestException):
            return
        cutoff = datetime.now(self.tz) - timedelta(days=45)
        for d in res.get("discussions", []):
            if d.get("timemodified") and self._ts(d["timemodified"]) < cutoff:
                continue
            text = f"{d.get('subject', '')}\n{extract.html_to_text(d.get('message', ''))}"
            yield (f"announcement '{d.get('subject', '')}'",
                   f"{self.client.base}/mod/forum/discuss.php?d={d.get('discussion')}", text, 5)

    def _texts(self, course: dict):
        """Yield (label, url, text[, priority]) for every readable thing in the course."""
        yield from self._announcements(course)
        base = self.client.base
        sections = self.client.call("core_course_get_contents", courseid=course["id"])
        for sec in sections:
            if sec.get("summary"):
                yield (f"section '{sec.get('name')}'", f"{base}/course/view.php?id={course['id']}",
                       extract.html_to_text(sec["summary"]))
            for mod in sec.get("modules", []):
                url = mod.get("url") or f"{base}/course/view.php?id={course['id']}"
                if mod.get("description"):
                    yield (f"{mod.get('modname')} '{mod.get('name')}'", url,
                           extract.html_to_text(mod["description"]))
                if mod.get("modname") in ("assign", "quiz"):
                    continue  # official dates already come from the API
                for f in mod.get("contents") or []:
                    try:
                        path = self._cached_file(course["id"], mod.get("id"), f)
                    except (MoodleError, requests.RequestException) as e:
                        self.report.append(f"  could not download {f.get('filename')}: {e}")
                        continue
                    if not path:
                        continue
                    try:
                        text = extract.text_from_file(path)
                    except Exception as e:  # damaged or encrypted file: skip it, keep going
                        self.report.append(f"  could not read {f.get('filename')}: {e}")
                        continue
                    yield (f.get("filename"), url, text)

    def _file_deadlines(self, course: dict) -> list[Deadline]:
        from .scanning import deadlines_from_texts
        start = self.term_start
        if not start and course.get("startdate"):
            start = self._ts(course["startdate"]).date()
        out, n = deadlines_from_texts(
            self._texts(course), source=self.name, course_code=course["_code"],
            course_key=course["id"], start=start, tz=self.tz, default_time=self.default_time,
            slash_order=self.slash_order, weights=self.weights.setdefault(course["_code"], {}))
        self.report.append(f"{course['_code']}: read {n} pages/files, found {len(out)} dates")
        return out

    # ------------------------------------------------------------ entry point
    def fetch(self) -> list[Deadline]:
        self.report = []
        courses = self.current_courses()
        self.report.append("Courses: " + ", ".join(c["_code"] for c in courses))
        items = self._api_deadlines(courses)
        self.report.append(f"Official Moodle due dates: {len(items)}")
        if self.scan_files:
            for c in courses:
                try:
                    items.extend(self._file_deadlines(c))
                except (MoodleError, requests.RequestException) as e:
                    self.report.append(f"{c['_code']}: could not read course contents ({e})")
        return items

    # ------------------------------------------------------------ grades
    def grades(self) -> list[dict]:
        info = self.client.call("core_webservice_get_site_info")
        out = []
        for c in self.current_courses():
            try:
                res = self.client.call("gradereport_user_get_grade_items", courseid=c["id"], userid=info["userid"])
            except (MoodleError, requests.RequestException):
                continue
            ug = (res.get("usergrades") or [{}])[0]
            total, items = None, []
            for gi in ug.get("gradeitems", []):
                pct = _pct(gi.get("percentageformatted"))
                if gi.get("itemtype") == "course":
                    total = pct
                    continue
                if gi.get("itemtype") == "category":
                    continue
                items.append({"name": gi.get("itemname") or "Item", "percent": pct,
                              "weight": round(gi["weightraw"] * 100, 2) if gi.get("weightraw") else None})
            graded_weight = sum(i["weight"] or 0 for i in items if i["percent"] is not None)
            out.append({"course": c["_code"], "name": c.get("fullname"), "percent": total,
                        "graded_weight": round(graded_weight, 1) if graded_weight else None,
                        "items": items, "url": f"{self.client.base}/grade/report/user/index.php?id={c['id']}"})
        return out


def _pct(s) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s or "")
    return float(m.group(1)) if m else None
