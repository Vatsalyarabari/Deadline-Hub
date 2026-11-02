"""Destination: one clean calendar file with built-in alerts.

Subscribe to it (or import it) in Google Calendar, Apple Calendar or Outlook and
your phone alerts you before each deadline, no extra app needed.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from icalendar import Alarm, Calendar, Event

from .base import Destination

DEFAULT_ALERTS = {  # minutes before due, per deadline type
    "exam": [3 * 24 * 60, 24 * 60],
    "project": [3 * 24 * 60, 24 * 60],
    "quiz": [24 * 60, 2 * 60],
    "assignment": [24 * 60, 2 * 60],
    "lab": [24 * 60],
    "other": [24 * 60, 2 * 60],
}
# "Start this" heads-up: days before the due date, per type
START_BY_DAYS = {"exam": 7, "project": 5, "assignment": 3, "lab": 2, "quiz": 2, "other": 2}
EMOJI = {"exam": "📝", "quiz": "❓", "assignment": "📄", "lab": "🧪", "project": "🛠", "other": "📌"}


class IcsFeedDestination(Destination):
    full_rebuild = True

    def __init__(self, name: str, path: str, alerts: dict | None = None,
                 event_minutes: int = 30, calendar_name: str = "Deadlines",
                 start_by: bool = False, digest: bool = False, digest_time: str = "08:00"):
        self.name = name
        self.start_by = start_by
        self.digest = digest
        dh, dm = (digest_time or "08:00").split(":")
        self.digest_offset = timedelta(hours=int(dh), minutes=int(dm))
        self.path = Path(path).expanduser()
        self.alerts = {**DEFAULT_ALERTS, **(alerts or {})}
        self.event_minutes = event_minutes
        self.calendar_name = calendar_name

    def finish(self, upcoming):
        cal = Calendar()
        cal.add("prodid", "-//Deadline Hub//EN")
        cal.add("version", "2.0")
        cal.add("x-wr-calname", self.calendar_name)
        cal.add("x-published-ttl", "PT2H")  # hint to subscribers: refresh every 2h
        stamp = datetime.now(timezone.utc)
        for d in upcoming:
            ev = Event()
            ev.add("uid", f"{d.id}@deadline-hub")
            ev.add("summary", f"{EMOJI.get(d.kind, '📌')} {d.display_title}")
            if d.all_day:
                # No time listed: all-day event, reminders the evening before (6 PM)
                ev.add("dtstart", d.due.date())
                ev.add("dtend", d.due.date() + timedelta(days=1))
            else:
                # Event ends at the due time so it sits right before the deadline.
                ev.add("dtstart", d.due - timedelta(minutes=self.event_minutes))
                ev.add("dtend", d.due)
            ev.add("dtstamp", stamp)
            desc = [f"Due: {d.due.strftime('%a %b %d, %I:%M %p')}", f"Type: {d.kind}"]
            if d.course:
                desc.append(f"Course: {d.course}")
            if d.description and d.source.endswith("-files"):
                desc.append(d.description)
            if d.url:
                desc.append(d.url)
                ev.add("url", d.url)
            ev.add("description", "\n".join(desc))
            ev.add("categories", [c for c in (d.course, d.kind) if c])
            if d.all_day:
                # relative to midnight of the due day: -6h = 6 PM the day before
                triggers = [timedelta(hours=-6)]
                if d.kind in ("exam", "project"):
                    triggers.append(timedelta(days=-2, hours=-6))  # 6 PM, 3 days before
            else:
                # relative to DTSTART; shift by the event length so it's relative to the due time
                triggers = [timedelta(minutes=-(m - self.event_minutes))
                            for m in self.alerts.get(d.kind, self.alerts["other"])]
            if self.start_by:
                days = START_BY_DAYS.get(d.kind, 2)
                # 9 AM on the start day, measured from this event's start
                start_day = datetime.combine(d.due.date() - timedelta(days=days), time(9, 0), d.due.tzinfo)
                ev_start = (datetime.combine(d.due.date(), time(0, 0), d.due.tzinfo) if d.all_day
                            else d.due - timedelta(minutes=self.event_minutes))
                if start_day > datetime.now(d.due.tzinfo):
                    a = Alarm()
                    a.add("action", "DISPLAY")
                    a.add("description", f"Start: {d.display_title} (due in {days} days)")
                    a.add("trigger", start_day - ev_start)
                    ev.add_component(a)
            for trig in triggers:
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", f"{d.display_title} due" + (" tomorrow" if d.all_day else " soon"))
                alarm.add("trigger", trig)
                ev.add_component(alarm)
            cal.add_component(ev)
        if self.digest:
            self._add_digest(cal, upcoming, stamp)
        try:
            cal.add_missing_timezones()  # embeds VTIMEZONE so every calendar app agrees
        except AttributeError:
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(cal.to_ical())

    def _add_digest(self, cal, upcoming, stamp):
        """One all-day 'Today' event per day that has deadlines, with a morning alert,
        so the phone shows a short list of what's due each morning."""
        by_day: dict = {}
        for d in upcoming:
            by_day.setdefault(d.due.date(), []).append(d)
        for day, items in sorted(by_day.items()):
            items.sort(key=lambda x: (x.all_day, x.due))
            names = ", ".join(i.display_title for i in items[:4]) + (" …" if len(items) > 4 else "")
            ev = Event()
            ev.add("uid", f"digest-{day.isoformat()}@deadline-hub")
            ev.add("summary", f"📋 Due today ({len(items)}): {names}")
            ev.add("dtstart", day)
            ev.add("dtend", day + timedelta(days=1))
            ev.add("dtstamp", stamp)
            ev.add("description", "\n".join(
                f"• {i.display_title} — {'all day' if i.all_day else i.due.strftime('%I:%M %p').lstrip('0')}"
                for i in items))
            ev.add("transp", "TRANSPARENT")  # doesn't block your calendar as busy
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", f"{len(items)} due today: {names}")
            a.add("trigger", self.digest_offset)
            ev.add_component(a)
            cal.add_component(ev)
