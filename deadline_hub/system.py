"""Operating-system integration so students never have to configure anything:

- Receives Moodle sign-in links (moodlemobile://...), the same way the official Moodle app does.
- Opens with the computer (tray only, no browser window) so syncing just keeps happening.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
SCHEME = "moodlemobile"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAC_LOGIN_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.deadlinehub.app.plist"


def app_command() -> list[str]:
    """How to start this app (packaged .exe/.app, or from the source folder)."""
    if getattr(sys, "frozen", False):
        if IS_MAC:  # .../DeadlineHub.app/Contents/MacOS/DeadlineHub
            return [sys.executable]
        return [sys.executable]
    launcher = Path(__file__).resolve().parent.parent / "Deadline Hub.pyw"
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    return [str(exe), str(launcher)]


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' for p in parts)


# ------------------------------------------------------------------ moodlemobile:// links

def link_handler_status() -> str:
    """'ours' | 'other' (e.g. the Moodle Desktop app owns it) | 'none' | 'unsupported'."""
    if IS_MAC:
        return "ours" if getattr(sys, "frozen", False) else "unsupported"
    if not IS_WINDOWS:
        return "unsupported"
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}\shell\open\command") as k:
            cmd = winreg.QueryValue(k, None) or ""
    except OSError:
        return "none"
    me = app_command()[-1].lower()
    return "ours" if me in cmd.lower() else "other"


def register_link_handler(force: bool = False) -> str:
    """Tell Windows to send moodlemobile:// links to Deadline Hub (per-user, no admin needed).
    Leaves another app's registration alone unless force=True."""
    status = link_handler_status()
    if not IS_WINDOWS or status == "ours" or (status == "other" and not force):
        return status
    import winreg
    base = rf"Software\Classes\{SCHEME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValue(k, None, winreg.REG_SZ, "URL:Moodle sign-in (Deadline Hub)")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
        winreg.SetValue(k, None, winreg.REG_SZ, _quote(app_command()) + ' "%1"')
    return "ours"


# ------------------------------------------------------------------ open at login

def autostart_enabled() -> bool | None:
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "DeadlineHub")
                return True
        except OSError:
            return False
    if IS_MAC:
        return MAC_LOGIN_AGENT.exists()
    return None


def set_autostart(on: bool) -> bool:
    cmd = app_command() + ["--no-browser"]
    if IS_WINDOWS:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, "DeadlineHub", 0, winreg.REG_SZ, _quote(cmd))
            else:
                try:
                    winreg.DeleteValue(k, "DeadlineHub")
                except OSError:
                    pass
        return True
    if IS_MAC:
        if on:
            MAC_LOGIN_AGENT.parent.mkdir(parents=True, exist_ok=True)
            MAC_LOGIN_AGENT.write_bytes(plistlib.dumps({
                "Label": "com.deadlinehub.app", "ProgramArguments": cmd, "RunAtLoad": True}))
        elif MAC_LOGIN_AGENT.exists():
            subprocess.run(["launchctl", "unload", str(MAC_LOGIN_AGENT)], capture_output=True)
            MAC_LOGIN_AGENT.unlink()
        return True
    return False


def install_mac_url_handler(callback) -> bool:
    """macOS delivers moodlemobile:// links to the running app as an Apple Event."""
    if not IS_MAC:
        return False
    try:
        import objc  # noqa: F401  (pyobjc comes with pystray on macOS)
        from Foundation import NSAppleEventManager, NSObject

        def fourcc(s: str) -> int:
            return int.from_bytes(s.encode(), "big")

        class _Handler(NSObject):
            def handleURL_withReply_(self, event, reply):
                url = event.paramDescriptorForKeyword_(fourcc("----")).stringValue()
                if url:
                    callback(str(url))

        handler = _Handler.alloc().init()
        install_mac_url_handler._keep = handler  # keep a reference alive
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            handler, "handleURL:withReply:", fourcc("GURL"), fourcc("GURL"))
        return True
    except Exception:
        return False
