"""Deadline Hub app: a local web page where each student connects their own school accounts.

Runs only on the student's computer at http://127.0.0.1:8737 (not reachable from the internet).
Start: double-click the app (or "Deadline Hub.pyw"), or `python -m deadline_hub ui`.
"""
from __future__ import annotations

import json
import os
import re
import plistlib
import socket
import subprocess
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .settings import Settings, normalize_site

PORT = 8737
TASK_NAME = "DeadlineHub"
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.deadlinehub.sync.plist"


# ------------------------------------------------------------------ shared with the CLI

def _weight_for(d, weights: dict) -> float | None:
    """Grade weight of one deadline from the course's syllabus breakdown, if known."""
    from .extract import short_key
    w = weights.get(d.course or "", {})
    if not w:
        return None
    k = short_key(d.title)
    if k and k in w:
        return w[k]
    if k:  # "midterm 2" when the syllabus says "Midterms 40%" etc.
        base = k.split(" ")[0]
        if f"category:{base}" in w:
            return w[f"category:{base}"]
    cat = {"quiz": "quiz", "lab": "lab", "assignment": "assignment", "project": "project",
           "exam": "exam"}.get(d.kind)
    return w.get(f"category:{cat}") if cat else None


def _origin(d) -> str:
    if d.source == "manual":
        return "added by you"
    if d.priority >= 10:
        return "official due date"
    return "announcement" if d.priority > 0 else "course file"


def record_result(out_dir: Path, summary: dict, now: datetime, labels: dict | None = None,
                  weights: dict | None = None, grades: list | None = None):
    """Save the latest sync so the app can show it (also called by background syncs)."""
    labels, weights = labels or {}, weights or {}
    new_ids = {d.id for d in summary.get("new", [])}
    changed_ids = {d.id for d, _ in summary.get("changed", [])}

    def label(src: str) -> str:
        base = src[:-6] if src.endswith("-files") else src
        return labels.get(base, "You" if base == "manual" else base)

    items = []
    for d in summary.get("upcoming", []):
        from_file = d.source.endswith("-files")
        items.append({
            "id": d.id, "title": d.title, "course": d.course, "kind": d.kind,
            "due": d.due.isoformat(), "all_day": d.all_day, "url": d.url, "account": label(d.source),
            "from_file": from_file, "note": d.description if (from_file or d.source == "manual"
                                                               or "class schedule" in (d.description or "")) else None,
            "manual_id": d.source_uid if d.source == "manual" else None,
            "weight": _weight_for(d, weights),
            "status": "new" if d.id in new_ids else "changed" if d.id in changed_ids else "",
        })
    errors = summary.get("errors", [])
    for src_id, name in labels.items():  # show account names instead of internal ids
        errors = [e.replace(f"'{src_id}'", f"'{name}'") for e in errors]
    data = {
        "time": now.isoformat(),
        "counts": {"upcoming": len(items), "new": len(new_ids), "changed": len(changed_ids)},
        "errors": errors,
        "conflicts": [{"title": k.display_title, "kept": k.due.strftime("%b %d"), "other": o.due.strftime("%b %d"),
                       "kept_from": _origin(k), "other_from": _origin(o)}
                      for k, o in summary.get("conflicts", [])],
        "deadlines": items,
        "weights": weights,
        "grades": grades,
    }
    (Path(out_dir) / "last_sync.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def _notion_id(text: str) -> str:
    """Pull the 32-character database id out of a Notion link (or accept a bare id)."""
    m = re.findall(r"[0-9a-f]{32}", (text or "").split("?")[0].replace("-", "").lower())  # ?v= is the view id
    return m[-1] if m else ""


def background_command() -> list[str]:
    """How the scheduled background sync starts this app."""
    if getattr(sys, "frozen", False):  # packaged app (.exe / .app)
        return [sys.executable, "--sync"]
    runner = Path(__file__).resolve().parent.parent / "run_sync.pyw"
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    return [str(exe), str(runner)]


def _apply_class_times(class_times: dict):
    """In-class exams/quizzes found in files without a time get the class start time."""

    def adjust(items):
        for d in items:
            t = class_times.get(d.course or "")
            if t and d.all_day and d.kind in ("exam", "quiz", "project"):
                h, m = (int(x) for x in t.split(":"))
                d.due = d.due.replace(hour=h, minute=m)
                d.all_day = False
                d.description = ((d.description or "") + "\nTime from your class schedule.").strip()
    return adjust


def sync_now(settings: Settings, keep: dict | None = None) -> dict:
    """One full sync using the student's accounts. Used by the app and by background sync.
    keep: optional dict that receives the deadline list for quick calendar rebuilds."""
    from .cli import build
    from .engine import run_sync
    from .state import State
    cfg = settings.to_config()
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    sources, dests = build(cfg, None)
    summary = run_sync(sources, dests, State(cfg["state_file"]), now=now, lookahead_days=cfg["lookahead_days"],
                       done=settings.done_ids(), adjust=_apply_class_times(settings.data.get("class_times", {})))
    # checked off in Notion/Todoist -> checked off here too
    by_id = {d.id: d for d in summary["upcoming"]}
    for did in summary.get("remote_done", ()):
        d = by_id.get(did)
        if d:
            settings.data["done"][did] = {"at": now.isoformat(), "title": d.title, "due": d.due.isoformat()}
    settings.prune_done()
    settings.save()

    weights: dict = {}
    grades: list = []
    for src in sources:
        for course, w in (getattr(src, "weights", None) or {}).items():
            if w:
                weights.setdefault(course, {}).update(w)
        if settings.data.get("grades_enabled", True) and hasattr(src, "grades"):
            try:
                grades.extend(src.grades())
            except Exception as e:
                summary.setdefault("errors", []).append(f"Grades from '{src.name}' unavailable: {str(e)[:120]}")
    record_result(settings.home, summary, now, {a["id"]: a.get("label") or a["type"] for a in settings.accounts},
                  weights, grades)
    if keep is not None:
        keep["upcoming"] = summary["upcoming"]
    return summary


# ------------------------------------------------------------------ app state

class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.running = False
        self.run_error: str | None = None
        self.conn: dict[str, dict] = {}
        self.cache: dict = {}  # last synced deadline objects, for instant calendar updates
        self.on_synced = None  # tray notification hook
        self.signin: dict = {}  # progress of "Sign in with Moodle" for the page to show

    # ---- accounts
    def _check(self, a: dict) -> dict:
        try:
            if a["type"] == "moodle":
                from .sources.moodle import MoodleClient
                info = MoodleClient(a["site"], a["token"], timeout=20).call("core_webservice_get_site_info")
                return {"ok": True, "user": info.get("fullname")}
            if a["type"] == "canvas":
                from .sources.canvas import verify
                return {"ok": True, "user": verify(a["site"], a["token"])}
            r = requests.get(a["url"].replace("webcal://", "https://", 1), timeout=20)
            ok = r.ok and "BEGIN:VCALENDAR" in r.text[:500]
            return {"ok": ok, "error": None if ok else "The calendar link stopped working."}
        except Exception as e:
            msg = str(e)
            if "invalidtoken" in msg or "rejected the access token" in msg or "Invalid token" in msg:
                msg = "This key expired or was reset. Remove the account and add it again."
            elif "Max retries" in msg or "ConnectionError" in type(e).__name__ or "timed out" in msg:
                msg = "Couldn't reach the site. Check your internet connection."
            return {"ok": False, "error": msg[:200]}

    def check_all(self):
        for a in list(self.settings.accounts):
            self.conn[a["id"]] = {"checking": True}
        for a in list(self.settings.accounts):
            self.conn[a["id"]] = self._check(a)

    def add_account(self, p: dict) -> dict:
        kind = p.get("type")
        self.settings.set_timezone_if_missing(p.get("tz"))
        try:
            if kind == "moodle":
                from .sources.moodle import MoodleClient, get_token, token_from_launch_url
                site = normalize_site(p.get("site", ""))
                if not site:
                    return {"ok": False, "error": "Enter your school's Moodle address."}
                if p.get("mode") == "password":
                    try:
                        token = get_token(site, p.get("username", "").strip(), p.get("password", ""))
                    except Exception as e:
                        return {"ok": False, "error": f"Sign-in didn't work ({e}). If your school signs in "
                                                     "with Microsoft, Google or a school login page, use that option instead."}
                else:
                    token = token_from_launch_url(p.get("link", ""))
                info = MoodleClient(site, token, timeout=20).call("core_webservice_get_site_info")
                acct = self.settings.add_account({"type": "moodle", "site": site, "token": token,
                                                  "label": info.get("sitename") or "Moodle",
                                                  "user": info.get("fullname")})
                self.conn[acct["id"]] = {"ok": True, "user": info.get("fullname")}
            elif kind == "canvas":
                from .sources.canvas import verify
                site = normalize_site(p.get("site", ""))
                token = (p.get("token") or "").strip()
                if not (site and token):
                    return {"ok": False, "error": "Enter your Canvas address and access token."}
                name = verify(site, token)
                acct = self.settings.add_account({"type": "canvas", "site": site, "token": token,
                                                  "label": "Canvas", "user": name})
                self.conn[acct["id"]] = {"ok": True, "user": name}
            elif kind == "ics":
                url = (p.get("url") or "").strip()
                if not url.lower().startswith(("http://", "https://", "webcal://")):
                    return {"ok": False, "error": "Paste the full calendar link (it starts with https:// or webcal://)."}
                r = requests.get(url.replace("webcal://", "https://", 1), timeout=30)
                if not (r.ok and "BEGIN:VCALENDAR" in r.text[:500]):
                    return {"ok": False, "error": "That link didn't return a calendar. Copy the subscribe/export link, "
                                                 "not the address of the calendar page."}
                acct = self.settings.add_account({"type": "ics", "url": url,
                                                  "label": (p.get("label") or "Calendar").strip()[:40]})
                self.conn[acct["id"]] = {"ok": True}
            else:
                return {"ok": False, "error": "Unknown account type."}
        except Exception as e:
            msg = str(e)
            if "Max retries" in msg or "Name or service not known" in msg or "getaddrinfo" in msg or "SSLError" in msg:
                msg = "Couldn't reach that address. Check it's the same one you see in your browser when you're on the site."
            elif "invalidtoken" in msg or "Invalid token" in msg or "rejected the access token" in msg:
                msg = "That key was rejected. Make a new one and try again."
            elif "Expecting value" in msg or "JSON" in msg:
                msg = "That address doesn't look like the right kind of site. Double-check it."
            return {"ok": False, "error": msg[:300]}
        self.start_sync()
        return {"ok": True}

    # ---- one-click Moodle sign-in
    def moodle_start(self, site: str, tz: str | None = None) -> dict:
        """Open the school's Moodle in the student's own browser. After they sign in (if needed),
        Moodle hands a key to 'moodlemobile://...', which the operating system passes to this app."""
        import secrets
        from . import system
        site = normalize_site(site)
        if not site:
            return {"ok": False, "error": "Pick your school or type its Moodle address."}
        self.settings.set_timezone_if_missing(tz)
        handler = system.register_link_handler()
        passport = secrets.token_hex(8)
        self.settings.data["pending_signin"] = {"site": site, "passport": passport,
                                                "at": datetime.now().timestamp()}
        self.settings.save()
        self.signin = {"status": "waiting", "site": site}
        launch = (f"{site}/admin/tool/mobile/launch.php?service=moodle_mobile_app"
                  f"&passport={passport}&urlscheme=moodlemobile")
        webbrowser.open(launch)
        return {"ok": True, "launch": launch, "handler": handler}

    def moodle_callback(self, link: str) -> dict:
        """The moodlemobile://token=... link came back from the browser."""
        import base64
        import hashlib
        pend = self.settings.data.get("pending_signin") or {}
        if not pend or datetime.now().timestamp() - pend.get("at", 0) > 30 * 60:
            self.signin = {"status": "error", "message": "That sign-in link expired. Click Sign in with Moodle again."}
            return {"ok": False, "error": self.signin["message"]}
        try:
            raw = link.split("token=", 1)[1].split("&")[0]
            parts = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode().split(":::")
            if parts[0] != hashlib.md5((pend["site"] + pend["passport"]).encode()).hexdigest():
                print("note: sign-in signature didn't match the expected site (continuing)")
        except Exception:
            pass
        r = self.add_account({"type": "moodle", "site": pend["site"], "mode": "sso", "link": link})
        self.settings.data.pop("pending_signin", None)
        self.settings.save()
        self.signin = ({"status": "ok", "message": "Connected! Syncing your deadlines…"} if r.get("ok")
                       else {"status": "error", "message": r.get("error", "Sign-in failed.")})
        return r

    def remove_account(self, acct_id: str) -> dict:
        self.settings.remove_account(acct_id)
        self.conn.pop(acct_id, None)
        if self.settings.accounts:
            self.start_sync()
        else:
            p = self.settings.home / "last_sync.json"
            if p.exists():
                p.write_text("null")
        return {"ok": True}

    # ---- sync
    def start_sync(self) -> bool:
        with self.lock:
            if self.running or not (self.settings.accounts or self.settings.data.get("manual")):
                return False
            self.running, self.run_error = True, None
        threading.Thread(target=self._sync, daemon=True).start()
        return True

    def _sync(self):
        try:
            summary = sync_now(self.settings, keep=self.cache)
            if self.on_synced:
                try:
                    self.on_synced(summary)
                except Exception:
                    pass
            if any(("invalidtoken" in e or "Invalid token" in e or "rejected the access token" in e)
                   for e in summary.get("errors", [])):
                self.check_all()
        except Exception as e:
            traceback.print_exc()
            self.run_error = f"{type(e).__name__}: {e}"
        finally:
            self.running = False

    def last(self):
        p = self.settings.home / "last_sync.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except json.JSONDecodeError:
            return None

    # ---- done, quick add, preferences
    def _find(self, deadline_id: str) -> dict | None:
        last = self.last() or {}
        return next((d for d in last.get("deadlines", []) if d["id"] == deadline_id), None)

    def rebuild_calendar(self):
        """Update the calendar file right away (e.g. after checking something off)."""
        from .destinations.ics_feed import IcsFeedDestination
        if not self.cache.get("upcoming"):
            return
        cfg = next(d for d in self.settings.to_config()["destinations"] if d["type"] == "ics_feed")
        cfg = {k: v for k, v in cfg.items() if k != "type"}
        done = self.settings.done_ids()
        IcsFeedDestination(**cfg).finish([d for d in self.cache["upcoming"] if d.id not in done])

    def set_done(self, deadline_id: str, done: bool) -> dict:
        d = self._find(deadline_id) or {}
        self.settings.set_done(deadline_id, done, d.get("title", ""), d.get("due", ""))
        self.rebuild_calendar()
        if any(v.get("enabled") for v in self.settings.data.get("integrations", {}).values()):
            self.start_sync()  # pushes the checkmark to Notion/Todoist
        return {"ok": True, "streak": self.settings.streak()}

    def quick_add(self, text: str) -> dict:
        from .extract import parse_quick_add
        try:
            item = parse_quick_add(text, datetime.now(ZoneInfo(self.settings.timezone)))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        self.settings.add_manual(item)
        self.start_sync()
        return {"ok": True, "item": item}

    def remove_manual(self, item_id: str) -> dict:
        self.settings.remove_manual(item_id)
        self.start_sync()
        return {"ok": True}

    def set_prefs(self, p: dict) -> dict:
        rem = self.settings.data["reminders"]
        for k in ("start_by", "digest"):
            if k in p:
                rem[k] = bool(p[k])
        if re.fullmatch(r"\d{1,2}:\d{2}", str(p.get("digest_time", ""))):
            rem["digest_time"] = p["digest_time"]
        if "grades_enabled" in p:
            self.settings.data["grades_enabled"] = bool(p["grades_enabled"])
        self.settings.save()
        self.rebuild_calendar()
        return {"ok": True}

    def set_class_time(self, course: str, at: str) -> dict:
        course = (course or "").strip().upper()
        if not course:
            return {"ok": False, "error": "Pick a course."}
        if at:
            if not re.fullmatch(r"\d{1,2}:\d{2}", at):
                return {"ok": False, "error": "Use a time like 18:15."}
            self.settings.data["class_times"][course] = at
        else:
            self.settings.data["class_times"].pop(course, None)
        self.settings.save()
        self.start_sync()
        return {"ok": True}

    def connect_integration(self, name: str, p: dict) -> dict:
        integ = self.settings.data["integrations"]
        try:
            if name == "notion":
                from .destinations.notion import verify
                token = (p.get("token") or "").strip()
                db = _notion_id(p.get("database", ""))
                if not (token and db):
                    return {"ok": False, "error": "Paste your integration token and the link to your task board."}
                title, _cols = verify(token, db)
                courses = _notion_id(p.get("courses_database", "")) if p.get("courses_database") else None
                integ["notion"] = {"enabled": True, "token": token, "database_id": db,
                                   "courses_database_id": courses, "label": title}
            elif name == "todoist":
                from .destinations.todoist import verify
                token = (p.get("token") or "").strip()
                verify(token)
                integ["todoist"] = {"enabled": True, "token": token,
                                    "project": (p.get("project") or "School").strip()[:40], "label": "Todoist"}
            else:
                return {"ok": False, "error": "Unknown app."}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}
        self.settings.save()
        self.start_sync()
        return {"ok": True}

    def remove_integration(self, name: str) -> dict:
        self.settings.data["integrations"].pop(name, None)
        self.settings.save()
        return {"ok": True}

    # ---- background sync
    def autosync_on(self) -> bool | None:
        if IS_WINDOWS:
            r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, creationflags=NO_WINDOW)
            return r.returncode == 0
        if IS_MAC:
            return MAC_AGENT.exists()
        return None

    def set_autosync(self, on: bool) -> dict:
        cmd = background_command()
        if IS_WINDOWS:
            if on:
                task = " ".join(f'"{c}"' if " " in c or "\\" in c else c for c in cmd)
                args = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "HOURLY", "/MO", "3", "/TR", task]
            else:
                args = ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]
            r = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
            return {"ok": True}
        if IS_MAC:
            if on:
                MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
                MAC_AGENT.write_bytes(plistlib.dumps({
                    "Label": "com.deadlinehub.sync", "ProgramArguments": cmd,
                    "StartInterval": 3 * 3600, "RunAtLoad": False}))
                subprocess.run(["launchctl", "load", "-w", str(MAC_AGENT)], capture_output=True)
            elif MAC_AGENT.exists():
                subprocess.run(["launchctl", "unload", "-w", str(MAC_AGENT)], capture_output=True)
                MAC_AGENT.unlink()
            return {"ok": True}
        return {"ok": False, "error": "Background sync isn't available on this system yet."}

    def open_calendar(self) -> dict:
        ics = self.settings.home / "out" / "deadlines.ics"
        if not ics.exists():
            return {"ok": False, "error": "Sync first to create the calendar file."}
        if IS_WINDOWS:
            os.startfile(str(ics))
        elif IS_MAC:
            subprocess.run(["open", str(ics)])
        else:
            return {"ok": False, "error": "Use Download instead."}
        return {"ok": True}

    def state(self) -> dict:
        accounts = []
        for a in self.settings.public_accounts():
            c = self.conn.get(a["id"], {})
            accounts.append({**a, "status": c})
        from . import system
        integ = {k: {"label": v.get("label"), "enabled": v.get("enabled"), "project": v.get("project")}
                 for k, v in self.settings.data.get("integrations", {}).items()}
        return {
            "running": self.running, "run_error": self.run_error, "accounts": accounts,
            "last": self.last() if (self.settings.accounts or self.settings.data.get("manual")) else None,
            "autosync": self.autosync_on(), "can_open": IS_WINDOWS or IS_MAC,
            "done": sorted(self.settings.done_ids()), "streak": self.settings.streak(),
            "reminders": self.settings.data.get("reminders"), "class_times": self.settings.data.get("class_times"),
            "grades_enabled": self.settings.data.get("grades_enabled", True), "integrations": integ,
            "signin": self.signin, "link_handler": system.link_handler_status(),
            "autostart": system.autostart_enabled(),
        }


# ------------------------------------------------------------------ HTTP

def make_handler(app: App, server_ref: dict):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else (
                json.dumps(body).encode() if ctype == "application/json" else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _same_origin(self) -> bool:
            # Stop other websites from using this local app through the browser.
            origin = self.headers.get("Origin")
            host_ok = self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
            return host_ok and origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

        def do_GET(self):
            if not self._same_origin():
                return self._send(403, {"error": "forbidden"})
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, app.state())
            if self.path == "/calendar.ics":
                ics = app.settings.home / "out" / "deadlines.ics"
                if not ics.exists():
                    return self._send(404, {"error": "no calendar yet"})
                return self._send(200, ics.read_bytes(), "text/calendar",
                                  {"Content-Disposition": 'attachment; filename="school-deadlines.ics"'})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._same_origin() or self.headers.get("Content-Type") != "application/json":
                return self._send(403, {"error": "forbidden"})
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            routes = {
                "/api/sync": lambda: {"started": app.start_sync()},
                "/api/done": lambda: app.set_done(body.get("id", ""), bool(body.get("done"))),
                "/api/quick-add": lambda: app.quick_add(body.get("text", "")),
                "/api/manual/remove": lambda: app.remove_manual(body.get("id", "")),
                "/api/prefs": lambda: app.set_prefs(body),
                "/api/class-time": lambda: app.set_class_time(body.get("course", ""), body.get("time", "")),
                "/api/integrations/connect": lambda: app.connect_integration(body.get("name", ""), body),
                "/api/integrations/remove": lambda: app.remove_integration(body.get("name", "")),
                "/api/moodle/start": lambda: app.moodle_start(body.get("site", ""), body.get("tz")),
                "/api/moodle/callback": lambda: app.moodle_callback(body.get("link", "")),
                "/api/autostart": lambda: _set_autostart(app, bool(body.get("on"))),
                "/api/accounts/add": lambda: app.add_account(body),
                "/api/accounts/remove": lambda: app.remove_account(body.get("id", "")),
                "/api/recheck": lambda: (app.check_all(), {"ok": True})[1],
                "/api/autosync": lambda: app.set_autosync(bool(body.get("on"))),
                "/api/open-calendar": app.open_calendar,
            }
            if self.path in routes:
                return self._send(200, routes[self.path]())
            if self.path == "/api/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=server_ref["server"].shutdown, daemon=True).start()
                if server_ref.get("icon"):
                    server_ref["icon"].stop()
                return
            self._send(404, {"error": "not found"})
    return H


def _port_in_use() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _run_tray(app: App, server, url: str, ref: dict) -> bool:
    """Icon next to the clock with Open / Sync now / Quit. Returns False if not available here."""
    try:
        import pystray
        from PIL import Image
        image = Image.open(Path(__file__).with_name("assets") / "icon.png")
    except Exception:
        return False

    def open_app(icon=None, item=None):
        webbrowser.open(url)

    def sync(icon=None, item=None):
        app.start_sync()

    def quit_app(icon, item=None):
        icon.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    icon = pystray.Icon("DeadlineHub", image, "Deadline Hub", menu=pystray.Menu(
        pystray.MenuItem("Open Deadline Hub", open_app, default=True),
        pystray.MenuItem("Sync now", sync),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app)))

    def notify(summary):
        new = len(summary.get("new", []))
        changed = len(summary.get("changed", []))
        if (new or changed) and getattr(icon, "HAS_NOTIFICATION", False):
            parts = [f"{new} new" if new else "", f"{changed} changed" if changed else ""]
            icon.notify(" · ".join(p for p in parts if p) + " deadline(s). Open Deadline Hub to see them.",
                        "Deadline Hub")
    app.on_synced = notify
    ref["icon"] = icon
    from . import system
    system.install_mac_url_handler(lambda link: app.moodle_callback(link))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        icon.run()  # blocks until Quit
    except Exception:
        ref.pop("icon", None)
        return False
    return True


def _set_autostart(app: App, on: bool) -> dict:
    from . import system
    ok = system.set_autostart(on)
    app.settings.data["autostart_chosen"] = True
    app.settings.save()
    return {"ok": ok} if ok else {"ok": False, "error": "Not available on this system."}


def _first_run_setup(settings: Settings):
    """New users get: Moodle sign-in links routed here, and the app opening with the computer."""
    from . import system
    try:
        system.register_link_handler()
        if not settings.data.get("autostart_chosen"):
            system.set_autostart(True)
            settings.data["autostart_chosen"] = True
            settings.save()
    except Exception as e:
        print("first-run setup:", e)


def _auto_sync_loop(app: App, every_hours: float = 3):
    """While the app is running (it starts with the computer), sync every few hours by itself."""
    import time as _time
    _time.sleep(45)
    while True:
        try:
            last = app.last()
            age = (datetime.now().astimezone() - datetime.fromisoformat(last["time"])).total_seconds() if last else 1e9
            if age > every_hours * 3600:
                app.start_sync()
        except Exception as e:
            print("auto-sync:", e)
        _time.sleep(10 * 60)


def serve(settings: Settings, open_browser: bool = True, tray: bool = True) -> int:
    url = f"http://127.0.0.1:{PORT}/"
    if _port_in_use():  # already running: just show it
        if open_browser:
            webbrowser.open(url)
        return 0
    app = App(settings)
    threading.Thread(target=app.check_all, daemon=True).start()
    _first_run_setup(settings)
    threading.Thread(target=_auto_sync_loop, args=(app,), daemon=True).start()
    ref: dict = {}
    server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(app, ref))
    ref["server"] = server
    print(f"Deadline Hub running at {url} (data: {settings.home})")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    if tray and _run_tray(app, server, url, ref):
        return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


PAGE = (Path(__file__).with_name("page.html")).read_text(encoding="utf-8")
