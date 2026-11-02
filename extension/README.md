# Deadline Hub: Connect Moodle (browser extension)

Connects your school's Moodle to the Deadline Hub app in one click, instead of the
DevTools steps. Works in Chrome, Edge, Brave and other Chromium browsers.

**Install (until it's on the Chrome Web Store):**
1. Download the `extension` folder (or `DeadlineHub-Extension.zip` from Releases and unzip it).
2. Open `chrome://extensions` (Edge: `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and pick the `extension` folder.

**Use:** open Deadline Hub, open your Moodle dashboard, click the extension → **Connect this Moodle**.

**What it does:** it asks Moodle for the same key the official Moodle phone app gets, then
passes it straight to Deadline Hub on your own computer (127.0.0.1). It only gets access to your
Moodle site after you click, and sends nothing anywhere else.
