# Deadline Hub

Every school deadline in one place, with phone reminders.

Deadline Hub connects to your school accounts, reads your courses **and the files inside
them** (syllabi, schedules, slides), finds every due date, and puts them in your calendar
with reminders. It runs entirely on your own computer. Your passwords and keys never
leave it, and nothing is sent to an AI or an outside server.

## For students

1. Download: **Windows** [DeadlineHub-Setup.exe](https://github.com/Vatsalyarabari/Deadline-Hub/releases/latest/download/DeadlineHub-Setup.exe)
   · **Mac** [DeadlineHub-Mac.dmg](https://github.com/Vatsalyarabari/Deadline-Hub/releases/latest/download/DeadlineHub-Mac.dmg)
   (Apple-silicon Macs). Open it and click **Install** (Mac: drag it into Applications).
   - Windows may say *"Windows protected your PC"*: click **More info → Run anyway**.
   - Mac may say it can't check the developer: right-click the app → **Open → Open**.
2. It opens in your browser. Pick your school and click **Sign in with Moodle**, then **Open** when
   the browser asks. Other systems:

| System | What you need | What it finds |
|---|---|---|
| **Moodle** | Your Moodle address + a one-time key (the app walks you through it) | Official due dates **and** dates in course files |
| **Canvas** | Your Canvas address + an access token from Account → Settings | Official due dates **and** syllabus/files |
| **Calendar link** | The calendar subscribe link from D2L, Blackboard, Google Classroom, etc. | Due dates on the course calendar |

3. Press **Sync now**, then **Download calendar** (or **Open in Calendar app**) to get the
   deadlines and reminders onto your phone. Or connect **Notion** or **Todoist**.
4. That's it. Deadline Hub starts with your computer (icon by the clock) and syncs every 3 hours.

## What's in the app

- **Do first:** your work ranked by how soon it's due, how much it's worth (grade weights read
  from the syllabus) and how big it is, with the reason for each.
- **Workload heatmap:** the next 8 weeks at a glance, with your busiest week flagged.
- **Check things off:** done items leave your reminders, and you build a streak.
- **Quick add:** type "BUSI 222 group meeting Thu 3pm" for anything that isn't in Moodle/Canvas.
- **Smarter reminders:** a "start this" heads-up days before big work, and a morning digest of
  what's due today.
- **Announcements:** "Lab 7 moved to Friday" posts are read too; date changes are flagged.
- **Grades:** your current grade per course, plus "what do I need on the final".
- **Class times:** in-class exams get your class start time instead of "all day".
- **Notion and Todoist:** cards/tasks created automatically; checking off works both ways.
- **Tray icon:** Open / Sync now / Quit next to the clock, with a notice when new deadlines appear.

Reminders: deadlines with a time get alerts 1 day and 2 hours before (exams and projects
3 days and 1 day). If a file only gives a date, you get an all-day event with a reminder
at 6 PM the evening before.

Your settings live in `%APPDATA%\DeadlineHub` (Windows) or
`~/Library/Application Support/DeadlineHub` (Mac). Delete that folder to reset everything.

## For developers

```bash
pip install -r requirements.txt
python -m deadline_hub.app            # the app
python -m deadline_hub.app --sync     # one background sync
```

The command-line version (`python -m deadline_hub scan|list|sync`, configured with
`config.yaml`) still works for power users.

### Building the downloads

- **Automatically:** push a tag like `v0.3.0` to GitHub. The workflow in
  `.github/workflows/build.yml` builds `DeadlineHub-Windows.zip` and `DeadlineHub-Mac.zip`
  and attaches them to the release. You can also run it by hand from the Actions tab.
- **On a Windows PC:** double-click `build_windows.bat` → `dist\DeadlineHub.exe`.

### How it's put together

```
deadline_hub/
  app.py            entry point (app window, or --sync for background runs)
  ui.py + page.html the local web app (127.0.0.1:8737, refuses requests from other sites)
  settings.py       per-user accounts, stored in the user's app-data folder
  sources/          moodle.py, canvas.py, ics.py  ->  one Deadline format
  extract.py        finds dates in text (PDF, Word, PowerPoint, HTML), no AI
  engine.py         merges duplicates across sources, detects changes, keeps your status
  destinations/     calendar file (alerts, start-by, digest), Notion, Todoist, Google Tasks
extension/          Chrome/Edge extension for one-click Moodle connect
```

Adding a school system = one new class in `sources/` with a `fetch()` method that
returns `Deadline` objects, plus a form in `page.html`.
