"""Pull from every source, de-duplicate, diff against state, push to every destination."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .models import Deadline
from .state import State

log = logging.getLogger("deadline_hub")


def merge(deadlines: list[Deadline], conflicts: list | None = None) -> list[Deadline]:
    """Combine the same deadline coming from several places into one.

    - Same item from the same source (same id) -> once.
    - Same course + same title + same due minute -> once.
    - Same course + same numbered item ("Quiz 8", "HW 5", "Final Exam") from different
      sources -> once, keeping the most trusted source (Moodle's official date beats a
      date found in a syllabus). If their dates disagree, it's recorded in `conflicts`.
    """
    from .extract import short_key

    kept: dict[str, Deadline] = {}
    seen_exact: set[tuple] = set()
    by_item: dict[tuple, Deadline] = {}
    for d in sorted(deadlines, key=lambda x: (-x.priority, x.due)):
        exact = (d.course, d.title.lower().strip(), d.due.replace(second=0, microsecond=0))
        if d.id in kept or exact in seen_exact:
            continue
        sk = short_key(d.title)
        if sk and d.course:
            prev = by_item.get((d.course, sk))
            if prev is not None:
                if prev.due.date() != d.due.date() and conflicts is not None:
                    conflicts.append((prev, d))
                continue
            by_item[(d.course, sk)] = d
        # A date found in a file that lands on the same day, same course and same type as an
        # official Moodle item is almost always that item under another name
        # ("Assignment 2" in the outline = "Choose and Audit a Dataset" in Moodle).
        if d.priority < 10 and d.course and any(
                o.priority >= 10 and o.course == d.course and o.kind == d.kind
                and o.due.date() == d.due.date() for o in kept.values()):
            continue
        kept[d.id] = d
        seen_exact.add(exact)
    return sorted(kept.values(), key=lambda x: x.due)


def run_sync(sources, destinations, state: State, *, now: datetime,
             lookahead_days: int = 120, dry_run: bool = False,
             done: set | None = None, adjust=None) -> dict:
    """done: ids the student checked off (kept in the list, left out of reminders).
    adjust: optional function applied to the merged list before diffing (e.g. class times)."""
    done = set(done or ())
    collected: list[Deadline] = []
    errors: list[str] = []
    for src in sources:
        try:
            items = src.fetch()
        except Exception as e:  # one broken source shouldn't stop the others
            msg = f"Source '{src.name}' failed, skipped this run: {type(e).__name__}: {str(e)[:200]}"
            print(msg)
            errors.append(msg)
            continue
        log.info("source %s: %d items", src.name, len(items))
        collected.extend(items)

    horizon = now + timedelta(days=lookahead_days)
    conflicts: list = []
    upcoming = [d for d in merge(collected, conflicts) if now <= d.due <= horizon]
    if adjust:
        adjust(upcoming)

    summary = {"new": [], "changed": [], "unchanged": 0, "errors": errors,
               "conflicts": [c for c in conflicts if c[0].due >= now]}
    plan: list[tuple[Deadline, str]] = []  # (deadline, "new" | "changed" | "same")
    for d in upcoming:
        prev = state.get(d.id)
        if prev is None:
            plan.append((d, "new"))
            summary["new"].append(d)
        elif prev.get("fingerprint") != d.fingerprint():
            plan.append((d, "changed"))
            summary["changed"].append((d, prev.get("due")))
        else:
            plan.append((d, "same"))
            summary["unchanged"] += 1

    remote_done: set[str] = set()
    # Apps first (they can report items checked off there), then whole-file outputs like the calendar.
    for dest in sorted(destinations, key=lambda x: x.full_rebuild):
        if dest.full_rebuild:
            if not dry_run:
                dest.begin(upcoming)
                dest.finish([d for d in upcoming if d.id not in done | remote_done])
            continue
        dest.begin(upcoming)
        for d, status in plan:
            remote = state.remote_id(d.id, dest.name)
            if dest.full_rebuild:
                continue  # handled in finish()
            if remote is None:
                if dry_run:
                    log.info("[dry-run] %s: would create %s", dest.name, d.display_title)
                    continue
                remote = dest.create(d)
                state.record(d, dest.name, remote)
            elif status == "changed":
                if dry_run:
                    log.info("[dry-run] %s: would update %s", dest.name, d.display_title)
                    continue
                dest.update(remote, d)
                state.record(d, dest.name, remote)
            # Two-way "done": checked off in the app <-> checked off in Notion/Todoist
            if not dry_run and remote and hasattr(dest, "is_done"):
                try:
                    there = dest.is_done(remote)
                    if there and d.id not in done:
                        remote_done.add(d.id)
                    elif d.id in done and not there:
                        dest.set_done(remote)
                except Exception as e:
                    log.warning("%s: couldn't sync done status: %s", dest.name, e)
        if not dry_run:
            dest.finish(upcoming)

    if not dry_run:
        for d in upcoming:
            state.record(d, "_seen", None)
        state.save()
    summary["upcoming"] = upcoming
    summary["remote_done"] = remote_done
    return summary
