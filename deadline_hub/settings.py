"""Per-user settings for the app: which school accounts are connected, timezone, reminders.

Stored on the student's own computer:
  Windows: %APPDATA%\\DeadlineHub\\settings.json
  macOS:   ~/Library/Application Support/DeadlineHub/settings.json
  Linux:   ~/.local/share/DeadlineHub/settings.json
(set DEADLINE_HUB_HOME to use another folder)
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "DeadlineHub"


def data_dir() -> Path:
    if os.environ.get("DEADLINE_HUB_HOME"):
        d = Path(os.environ["DEADLINE_HUB_HOME"])
    elif os.name == "nt":
        d = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# Paths people often paste from the address bar; we only want the site's base address.
_LMS_PATHS = re.compile(
    r"/(my|login|course|courses|calendar|user|admin|mod|dashboard|profile|grades|"
    r"conversations|accounts|d2l|ultra|webapps)(/.*)?$", re.I)


def normalize_site(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    path = _LMS_PATHS.sub("", p.path or "").rstrip("/")
    return f"{p.scheme}://{p.netloc}{path}"


class Settings:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else data_dir()
        self.path = self.home / "settings.json"
        self.data = {"timezone": None, "accounts": [], "scan_files": True,
                     "done": {},            # deadline id -> {"at", "title", "due"}
                     "manual": [],          # quick-added items
                     "class_times": {},     # "CMPT 401" -> "18:15"
                     "reminders": {"start_by": True, "digest": True, "digest_time": "08:00"},
                     "integrations": {},    # "notion": {...}, "todoist": {...}
                     "grades_enabled": True}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in saved.items():
                    if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                        self.data[k] = {**self.data[k], **v}
                    else:
                        self.data[k] = v
            except json.JSONDecodeError:
                pass

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def accounts(self) -> list[dict]:
        return self.data["accounts"]

    @property
    def timezone(self) -> str:
        return self.data.get("timezone") or "America/Vancouver"

    def set_timezone_if_missing(self, tz: str | None):
        if tz and not self.data.get("timezone"):
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)
                self.data["timezone"] = tz
            except Exception:
                pass

    def add_account(self, acct: dict, acct_id: str | None = None) -> dict:
        acct = {"id": acct_id or f"{acct['type']}-{uuid.uuid4().hex[:6]}", **acct}
        # replace an existing account for the same site (e.g. reconnecting with a new key)
        self.data["accounts"] = [a for a in self.accounts
                                 if not (a["type"] == acct["type"] and a.get("site") == acct.get("site")
                                         and a.get("site"))]
        self.accounts.append(acct)
        self.save()
        return acct

    def remove_account(self, acct_id: str):
        self.data["accounts"] = [a for a in self.accounts if a["id"] != acct_id]
        self.save()

    # ---- done / streak
    def set_done(self, deadline_id: str, done: bool, title: str = "", due: str = ""):
        from datetime import datetime
        if done:
            self.data["done"][deadline_id] = {"at": datetime.now().astimezone().isoformat(),
                                              "title": title, "due": due}
        else:
            self.data["done"].pop(deadline_id, None)
        self.save()

    def done_ids(self) -> set[str]:
        return set(self.data.get("done", {}))

    def streak(self) -> dict:
        """Days in a row (up to today) on which you finished at least one thing, plus on-time count."""
        from datetime import datetime, timedelta
        days = set()
        on_time = 0
        for rec in self.data.get("done", {}).values():
            try:
                at = datetime.fromisoformat(rec["at"])
            except (KeyError, ValueError):
                continue
            days.add(at.date())
            if rec.get("due"):
                try:
                    if at <= datetime.fromisoformat(rec["due"]):
                        on_time += 1
                except ValueError:
                    pass
        today = datetime.now().astimezone().date()
        d = today if today in days else today - timedelta(days=1)
        n = 0
        while d in days:
            n += 1
            d -= timedelta(days=1)
        return {"days": n, "on_time": on_time, "total": len(self.data.get("done", {}))}

    def prune_done(self, keep_days: int = 60):
        """Forget done records for deadlines long past, so the file stays small."""
        from datetime import datetime, timedelta
        cutoff = datetime.now().astimezone() - timedelta(days=keep_days)
        keep = {}
        for k, rec in self.data.get("done", {}).items():
            try:
                if datetime.fromisoformat(rec.get("due") or rec["at"]) >= cutoff:
                    keep[k] = rec
            except (KeyError, ValueError):
                keep[k] = rec
        self.data["done"] = keep

    # ---- quick add
    def add_manual(self, item: dict) -> dict:
        item = {"id": f"m-{uuid.uuid4().hex[:8]}", **item}
        self.data["manual"].append(item)
        self.save()
        return item

    def remove_manual(self, item_id: str):
        self.data["manual"] = [m for m in self.data["manual"] if m["id"] != item_id]
        self.save()

    def public_accounts(self) -> list[dict]:
        """Accounts without secrets, for showing in the app."""
        return [{k: v for k, v in a.items() if k not in ("token", "url")} for a in self.accounts]

    def to_config(self) -> dict:
        """The same shape config.yaml has, so the sync engine doesn't care where it came from."""
        sources = []
        for a in self.accounts:
            base = {"name": a["id"], "timezone": self.timezone}
            cache = str(self.home / "cache" / a["id"])
            if a["type"] == "moodle":
                sources.append({**base, "type": "moodle", "base_url": a["site"], "token": a["token"],
                                "scan_files": self.data.get("scan_files", True), "cache_dir": cache})
            elif a["type"] == "canvas":
                sources.append({**base, "type": "canvas", "base_url": a["site"], "token": a["token"],
                                "scan_files": self.data.get("scan_files", True), "cache_dir": cache})
            elif a["type"] == "ics":
                sources.append({**base, "type": "ics", "url": a["url"]})
        if self.data.get("manual"):
            sources.append({"type": "manual", "name": "manual", "items": self.data["manual"],
                            "timezone": self.timezone})
        rem = self.data.get("reminders", {})
        dests = [{"type": "ics_feed", "name": "calendar", "path": str(self.home / "out" / "deadlines.ics"),
                  "calendar_name": "School Deadlines", "start_by": rem.get("start_by", True),
                  "digest": rem.get("digest", True), "digest_time": rem.get("digest_time", "08:00")}]
        integ = self.data.get("integrations", {})
        if integ.get("notion", {}).get("enabled"):
            n = integ["notion"]
            dests.append({"type": "notion", "name": "notion", "token": n["token"],
                          "tasks_database_id": n["database_id"],
                          "courses_database_id": n.get("courses_database_id") or None})
        if integ.get("todoist", {}).get("enabled"):
            t = integ["todoist"]
            dests.append({"type": "todoist", "name": "todoist", "token": t["token"],
                          "project": t.get("project") or "School"})
        return {
            "timezone": self.timezone,
            "lookahead_days": 120,
            "state_file": str(self.home / "state.json"),
            "sources": sources,
            "destinations": dests,
        }

    def migrate_from_project(self, project_dir: Path):
        """First run of the app for someone who used the command-line version: keep their Moodle key."""
        if self.accounts:
            return
        env = Path(project_dir) / ".env"
        if not env.exists():
            return
        vals = {}
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
        if vals.get("MOODLE_TOKEN") and vals.get("MOODLE_URL"):
            self.add_account({"type": "moodle", "site": normalize_site(vals["MOODLE_URL"]),
                              "token": vals["MOODLE_TOKEN"], "label": "Moodle"}, acct_id="moodle")
