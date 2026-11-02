"""Entry point for the downloadable app.

    DeadlineHub            -> opens the app in your browser
    DeadlineHub --sync     -> one background sync (used by the scheduled task), no window
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from .settings import Settings


def main(argv: list[str] | None = None, project_dir: Path | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    link = next((a for a in argv if a.lower().startswith("moodlemobile://")), None)
    if link:
        return _hand_over_link(link)
    settings = Settings()
    if project_dir:
        settings.migrate_from_project(project_dir)

    # pythonw / packaged apps have no console: keep messages in a log next to the settings
    if sys.stdout is None or getattr(sys, "frozen", False) or "--sync" in argv:
        log = open(settings.home / ("sync.log" if "--sync" in argv else "app.log"), "a",
                   encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    if "--sync" in argv:
        from .ui import sync_now
        print(f"\n=== {datetime.now():%Y-%m-%d %H:%M} ===")
        if not settings.accounts:
            print("No accounts connected yet.")
            return 0
        s = sync_now(settings)
        print(f"{len(s['upcoming'])} upcoming, {len(s['new'])} new, {len(s['changed'])} changed")
        for e in s.get("errors", []):
            print(e)
        return 0

    from .ui import serve
    return serve(settings, open_browser="--no-browser" not in argv, tray="--no-tray" not in argv)


def _hand_over_link(link: str) -> int:
    """Windows starts a second copy of the app with the sign-in link: pass it to the running one."""
    import json
    import urllib.request
    from .ui import PORT
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/moodle/callback",
                                 data=json.dumps({"link": link}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        # App wasn't running: start it; the sign-in is still pending in settings.
        import threading
        from .ui import serve
        settings = Settings()

        def later():
            import time
            time.sleep(2)
            try:
                urllib.request.urlopen(req, timeout=60).read()
            except Exception:
                pass
        threading.Thread(target=later, daemon=True).start()
        return serve(settings, open_browser=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
