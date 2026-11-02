# Build with:  pyinstaller packaging/DeadlineHub.spec
# Output: dist/DeadlineHub.exe (Windows) or dist/DeadlineHub.app (macOS)
import sys
from PyInstaller.utils.hooks import collect_data_files

ROOT = SPECPATH + "/.."
datas = [(ROOT + "/deadline_hub/page.html", "deadline_hub"),
         (ROOT + "/deadline_hub/assets/icon.png", "deadline_hub/assets")]
datas += collect_data_files("tzdata")          # timezone database (Windows has none built in)
datas += collect_data_files("docx")            # Word templates used by python-docx
datas += collect_data_files("pptx")

a = Analysis(
    [ROOT + "/packaging/launcher.py"],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["tzdata", "deadline_hub.sources.moodle", "deadline_hub.sources.canvas",
                   "deadline_hub.sources.ics", "deadline_hub.sources.manual",
                   "deadline_hub.destinations.notion", "deadline_hub.destinations.todoist",
                   "pystray._win32" if sys.platform == "win32" else "pystray._darwin",
                   "deadline_hub.system"],
    excludes=["googleapiclient", "google_auth_oauthlib", "tkinter", "matplotlib", "numpy"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeadlineHub", console=False,
              icon=ROOT + "/packaging/icons/icon.png")
    coll = COLLECT(exe, a.binaries, a.datas, name="DeadlineHub")
    app = BUNDLE(coll, name="DeadlineHub.app", bundle_identifier="com.deadlinehub.app",
                 icon=ROOT + "/packaging/icons/icon.png",
                 info_plist={
                     "LSUIElement": True,  # no Dock icon: it lives in the menu bar and the browser
                     "CFBundleShortVersionString": __import__("os").environ.get("APP_VERSION", "0.0.0"),
                     # receive "Sign in with Moodle" links
                     "CFBundleURLTypes": [{"CFBundleURLName": "Moodle sign-in",
                                           "CFBundleURLSchemes": ["moodlemobile"]}],
                 })
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="DeadlineHub",
              console=False, upx=False, icon=ROOT + "/packaging/icons/icon.ico")  # one file, no console window
