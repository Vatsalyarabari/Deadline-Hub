"""Usage:
    python -m deadline_hub moodle-login    # one time: connect to your Moodle account
    python -m deadline_hub scan            # crawl everything, show what was found and where
    python -m deadline_hub list            # print upcoming deadlines only
    python -m deadline_hub sync --dry-run  # show what would happen, change nothing
    python -m deadline_hub sync            # pull everything, push everywhere
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .destinations import REGISTRY as DEST_REGISTRY
from .engine import merge, run_sync
from .sources import REGISTRY as SRC_REGISTRY
from .state import State

ENV_RE = re.compile(r"\$\{(\w+)\}")


def _expand_env(obj):
    """Lets config.yaml say token: ${NOTION_TOKEN} so secrets stay out of the file."""
    if isinstance(obj, str):
        return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


def load_env_file(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build(cfg: dict, only: list[str] | None):
    tz = cfg.get("timezone", "America/Vancouver")
    sources = []
    for s in cfg.get("sources", []):
        if not s.pop("enabled", True):
            continue
        kind = s.pop("type")
        s.setdefault("timezone", tz)
        s.setdefault("course_aliases", cfg.get("course_aliases"))
        try:
            sources.append(SRC_REGISTRY[kind](**s))
        except ValueError as e:
            print(f"Skipping source '{s.get('name')}': {e}")
    dests = []
    for d in cfg.get("destinations", []):
        if not d.pop("enabled", True):
            continue
        kind = d.pop("type")
        if only and d["name"] not in only:
            continue
        dests.append(DEST_REGISTRY[kind](**d))
    return sources, dests


def fmt(d) -> str:
    when = d.due.strftime('%a %b %d') + ("  (all day)" if d.all_day else d.due.strftime(' %I:%M %p'))
    return f"  {when:<22} {d.kind:<10} {d.display_title}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deadline_hub")
    ap.add_argument("command", choices=["sync", "list", "scan", "moodle-login", "ui"])
    ap.add_argument("--url", help="Moodle site, e.g. https://learn.twu.ca (moodle-login)")
    ap.add_argument("--paste", action="store_true",
                    help="moodle-login: paste a token or moodlemobile:// link instead of a password")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", help="only these destination names")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose or args.dry_run else logging.WARNING,
                        format="%(message)s")
    cfg_path = Path(args.config).resolve()
    os.chdir(cfg_path.parent)
    load_env_file(cfg_path.parent / ".env")
    if args.command == "ui":
        from .app import main as app_main
        return app_main([], project_dir=cfg_path.parent)
    if args.command == "moodle-login":
        return moodle_login(cfg_path.parent / ".env", args.url, args.paste)
    cfg = _expand_env(yaml.safe_load(cfg_path.read_text()))
    tz = ZoneInfo(cfg.get("timezone", "America/Vancouver"))
    now = datetime.now(tz)
    sources, dests = build(cfg, args.only)

    if args.command in ("list", "scan"):
        collected = []
        for src in sources:
            try:
                collected.extend(src.fetch())
            except Exception as e:
                print(f"Source '{src.name}' failed: {type(e).__name__}: {str(e)[:200]}")
                continue
            if args.command == "scan" and getattr(src, "report", None):
                print(f"[{src.name}]")
                print("\n".join("  " + line for line in src.report))
        conflicts: list = []
        items = [d for d in merge(collected, conflicts) if d.due >= now]
        print(f"\n{len(items)} upcoming deadlines:")
        for d in items:
            print(fmt(d) + ("   [from course files]" if d.source.endswith("-files") else ""))
            if args.command == "scan" and d.description and d.source.endswith("-files"):
                print("      " + d.description.replace("\n", "\n      "))
        print_conflicts([c for c in conflicts if c[0].due >= now])
        return 0

    state = State(cfg_path.parent / cfg.get("state_file", "state.json"))
    summary = run_sync(sources, dests, state, now=now,
                       lookahead_days=cfg.get("lookahead_days", 120), dry_run=args.dry_run)
    if not args.dry_run:
        from .ui import record_result
        record_result(cfg_path.parent, summary, now)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}{len(summary['upcoming'])} upcoming · {len(summary['new'])} new · "
          f"{len(summary['changed'])} changed · {summary['unchanged']} unchanged")
    for d in summary["new"]:
        print("  NEW     " + fmt(d).strip())
    for d, old in summary["changed"]:
        old_s = datetime.fromisoformat(old).strftime("%a %b %d %I:%M %p") if old else "?"
        print(f"  CHANGED {fmt(d).strip()}   (was {old_s})")
    print_conflicts(summary["conflicts"])
    return 0


def print_conflicts(conflicts):
    if not conflicts:
        return
    print("\nDates that don't match between sources (kept the first one):")
    for kept, other in conflicts:
        print(f"  {kept.display_title}: {kept.due.strftime('%b %d %I:%M %p')} ({kept.source})"
              f"  vs  {other.due.strftime('%b %d')} ({other.source})")


def _save_env(env_path: Path, key: str, value: str):
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def moodle_login(env_path: Path, url: str | None, paste: bool) -> int:
    import getpass
    from .sources.moodle import MoodleClient, MoodleError, get_token, token_from_launch_url

    url = (url or os.environ.get("MOODLE_URL") or input("Moodle site (e.g. https://learn.twu.ca): ")).strip()
    url = url.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    if paste:
        try:
            token = token_from_launch_url(input("Paste the token or moodlemobile:// link: "))
        except Exception as e:
            print(f"Couldn't use that: {e}")
            return 1
    else:
        user = input("Moodle username: ").strip()
        pw = getpass.getpass("Moodle password (not shown, not saved): ")
        try:
            token = get_token(url, user, pw)
        except (MoodleError, Exception) as e:
            print(f"\nPassword login didn't work: {e}")
            print("If your school signs in through Microsoft/Google (SSO), do this instead:")
            print(f"  1. Sign in to {url} in Chrome.")
            print(f"  2. In the same browser, open:")
            print(f"     {url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodlemobile")
            print("  3. Chrome asks to open a link (or shows an error). Press F12 -> Console,")
            print("     copy the link that starts with moodlemobile://token=")
            print("  4. Run:  python -m deadline_hub moodle-login --paste")
            print(f"Or: {url}/user/managetoken.php may show a 'Moodle mobile web service' key you can paste.")
            return 1
    try:
        info = MoodleClient(url, token).call("core_webservice_get_site_info")
    except Exception as e:
        print(f"Got a token but Moodle rejected it: {e}")
        return 1
    _save_env(env_path, "MOODLE_URL", url)
    _save_env(env_path, "MOODLE_TOKEN", token)
    print(f"Connected as {info.get('fullname')} on {info.get('sitename')}. Token saved to {env_path.name}.")
    print("Next:  python -m deadline_hub scan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
