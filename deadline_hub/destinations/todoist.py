"""Destination: Todoist (phone + desktop to-do app with its own reminders).

Needs a personal API token: Todoist -> Settings -> Integrations -> Developer -> API token.
Creates a "School" project (name configurable), adds each deadline as a task with its due
date, and keeps "done" in sync both ways.
"""
from __future__ import annotations

import time

import requests

from .base import Destination

import os

API = os.environ.get("DH_TODOIST_API", "https://api.todoist.com/api/v1")  # override only for tests
PRIORITY = {"exam": 4, "project": 3, "assignment": 2, "quiz": 2, "lab": 2}  # 4 = red flag


class TodoistDestination(Destination):
    def __init__(self, name: str, token: str, project: str = "School"):
        self.name = name
        self.project_name = project
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})
        self.project_id = None

    def _req(self, method, path, **kw):
        for _ in range(4):
            r = self.s.request(method, f"{API}{path}", timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            return r
        return r

    @staticmethod
    def _items(data):
        return data.get("results", []) if isinstance(data, dict) else data

    def begin(self, upcoming):
        r = self._req("GET", "/projects")
        if r.status_code == 401:
            raise RuntimeError("Todoist rejected the API token.")
        r.raise_for_status()
        match = next((p for p in self._items(r.json()) if p.get("name") == self.project_name), None)
        if match is None:
            r = self._req("POST", "/projects", json={"name": self.project_name})
            r.raise_for_status()
            match = r.json()
        self.project_id = match["id"]

    def _body(self, d) -> dict:
        body = {"content": d.display_title, "priority": PRIORITY.get(d.kind, 1),
                "description": "\n".join(x for x in (d.course, d.url) if x)}
        if d.all_day:
            body["due_date"] = d.due.date().isoformat()
        else:
            body["due_datetime"] = d.due.isoformat()
        return body

    def create(self, d) -> str:
        r = self._req("POST", "/tasks", json={**self._body(d), "project_id": self.project_id})
        r.raise_for_status()
        return r.json()["id"]

    def update(self, remote_id, d):
        r = self._req("POST", f"/tasks/{remote_id}", json=self._body(d))
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def is_done(self, remote_id) -> bool:
        r = self._req("GET", f"/tasks/{remote_id}")
        if r.status_code == 404:  # completed tasks can drop out of the active list
            return True
        r.raise_for_status()
        t = r.json()
        return bool(t.get("checked") or t.get("is_completed") or t.get("completed_at"))

    def set_done(self, remote_id):
        self._req("POST", f"/tasks/{remote_id}/close")


def verify(token: str) -> str:
    r = requests.get(f"{API}/projects", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code == 401:
        raise RuntimeError("Todoist rejected that API token.")
    r.raise_for_status()
    return "Todoist"
