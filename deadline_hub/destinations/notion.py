"""Destination: a Notion task database (like a Not started / In progress / Done board).

New deadlines become cards with Status = "Not started", the due date, and a
relation to the matching page in your Courses database. Later runs update the
title, date and course if the portal changes them, but never touch Status,
so a card you moved to Done stays Done.
"""
from __future__ import annotations

import logging
import time

import requests

from ..models import extract_course_code
from .base import Destination

import os

API = os.environ.get("DH_NOTION_API", "https://api.notion.com/v1")  # override only for tests
log = logging.getLogger("deadline_hub.notion")


class NotionDestination(Destination):
    def __init__(self, name: str, token: str, tasks_database_id: str,
                 courses_database_id: str | None = None,
                 properties: dict | None = None, default_status: str = "Not started",
                 done_status: str = "Done",
                 courses_title_property: str | None = None):
        self.name = name
        self.tasks_db = tasks_database_id
        self.courses_db = courses_database_id
        p = {"title": "Name", "due": "Due", "status": "Status",
             "course": "Course", "type": None, "link": None}
        p.update(properties or {})
        self.props = p
        self.default_status = default_status
        self.done_status = done_status
        self.courses_title_property = courses_title_property
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        })
        self.course_pages: dict[str, str] = {}
        self.status_kind = "status"

    # --- HTTP with simple rate-limit handling (Notion allows ~3 req/s) ---
    def _req(self, method, path, **kw):
        for attempt in range(5):
            r = self.s.request(method, f"{API}{path}", timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 1)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:400]}")
            return r.json()
        raise RuntimeError("Notion rate limit: gave up after retries")

    def begin(self, upcoming):
        # Learn whether Status is a "status" or "select" property, and check names exist.
        db = self._req("GET", f"/databases/{self.tasks_db}")
        schema = db["properties"]
        # Columns not found by name are matched by type, so most student templates just work.
        by_type = {"title": ["title"], "due": ["date"], "status": ["status", "select"],
                   "course": ["relation"], "type": [], "link": ["url"]}
        for key, types in by_type.items():
            name = self.props.get(key)
            if name and name in schema:
                continue
            guess = [n for n, v in schema.items() if v["type"] in types]
            if key == "status":
                guess = sorted(guess, key=lambda n: (schema[n]["type"] != "status", "status" not in n.lower()))
            if key == "due":
                guess = sorted(guess, key=lambda n: not any(w in n.lower() for w in ("due", "date", "deadline")))
            if guess and key in ("title", "due", "status", "course"):
                self.props[key] = guess[0]
        for key in ("title", "due", "status", "course", "type", "link"):
            prop_name = self.props.get(key)
            if prop_name and prop_name not in schema:
                if key in ("title", "due"):
                    raise RuntimeError(
                        "Your Notion board needs a Date column for due dates. Add one (any name, "
                        f"e.g. 'Due') and sync again. Columns found: {', '.join(schema)}")
                log.warning("Notion: no '%s' property, skipping %s", prop_name, key)
                self.props[key] = None
        if self.props["status"]:
            self.status_kind = schema[self.props["status"]]["type"]
        if self.courses_db and self.props["course"]:
            self._load_courses()

    def _load_courses(self):
        cursor = None
        while True:
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            res = self._req("POST", f"/databases/{self.courses_db}/query", json=body)
            for page in res["results"]:
                title = self._page_title(page)
                code = extract_course_code(title)
                if code:
                    # Prefer non-archived/most recent if a code repeats
                    self.course_pages.setdefault(code, page["id"])
            if not res.get("has_more"):
                break
            cursor = res["next_cursor"]
        log.info("Notion: matched %d course pages", len(self.course_pages))

    def _page_title(self, page) -> str:
        for name, prop in page["properties"].items():
            if prop["type"] == "title" and (not self.courses_title_property
                                            or name == self.courses_title_property):
                return "".join(t["plain_text"] for t in prop["title"])
        return ""

    def _properties(self, d, include_status: bool) -> dict:
        p = self.props
        out = {
            p["title"]: {"title": [{"text": {"content": d.display_title[:200]}}]},
            p["due"]: {"date": {"start": d.due.date().isoformat() if d.all_day else d.due.isoformat()}},
        }
        if p["course"] and d.course in self.course_pages:
            out[p["course"]] = {"relation": [{"id": self.course_pages[d.course]}]}
        if p["type"]:
            out[p["type"]] = {"select": {"name": d.kind}}
        if p["link"] and d.url:
            out[p["link"]] = {"url": d.url}
        if include_status and p["status"]:
            out[p["status"]] = {self.status_kind: {"name": self.default_status}}
        return out

    def create(self, d) -> str:
        page = self._req("POST", "/pages", json={
            "parent": {"database_id": self.tasks_db},
            "properties": self._properties(d, include_status=True),
        })
        return page["id"]

    def update(self, remote_id, d):
        self._req("PATCH", f"/pages/{remote_id}",
                  json={"properties": self._properties(d, include_status=False)})

    # ---- two-way "done"
    DONE_NAMES = ("done", "complete", "completed", "finished", "submitted")

    def is_done(self, remote_id) -> bool:
        if not self.props.get("status"):
            return False
        page = self._req("GET", f"/pages/{remote_id}")
        prop = page["properties"].get(self.props["status"]) or {}
        val = prop.get(self.status_kind) or {}
        return (val.get("name") or "").strip().lower() in self.DONE_NAMES

    def set_done(self, remote_id):
        if not self.props.get("status"):
            return
        self._req("PATCH", f"/pages/{remote_id}", json={"properties": {
            self.props["status"]: {self.status_kind: {"name": self.done_status}}}})


def verify(token: str, tasks_database_id: str) -> str:
    """Returns the database title if the integration can see it."""
    r = requests.get(f"{API}/databases/{tasks_database_id}", timeout=20, headers={
        "Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"})
    if r.status_code == 401:
        raise RuntimeError("Notion rejected that integration token.")
    if r.status_code == 404:
        raise RuntimeError("Notion can't see that database. Open it in Notion → ••• → Connections → "
                           "add your integration, then try again.")
    r.raise_for_status()
    db = r.json()
    title = "".join(t.get("plain_text", "") for t in db.get("title", [])) or "Notion database"
    return title, list(db.get("properties", {}).keys())
