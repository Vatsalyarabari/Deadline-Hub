const msg = (t, cls = "") => { const m = document.getElementById("msg"); m.className = cls; m.textContent = t; };

document.getElementById("go").onclick = async () => {
  const btn = document.getElementById("go");
  btn.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    // Every Moodle page knows its own base address (M.cfg.wwwroot).
    const [{ result: wwwroot }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, world: "MAIN",
      func: () => (window.M && M.cfg && M.cfg.wwwroot) || null,
    });
    if (!wwwroot) { msg("This tab isn't a Moodle page. Open your Moodle dashboard first.", "bad"); return; }
    const origin = new URL(wwwroot).origin + "/*";
    // Only asks for access to your school's Moodle site, only when you click.
    if (!(await chrome.permissions.request({ origins: [origin] }))) { msg("Permission is needed to read the key from Moodle.", "bad"); return; }
    msg("Getting your Moodle key…");
    const r = await chrome.runtime.sendMessage({ type: "connect", wwwroot });
    r.ok ? msg("Done. Finish in the Deadline Hub tab that just opened.", "ok") : msg(r.error, "bad");
  } catch (e) {
    msg("Couldn't read this page. Open your Moodle dashboard and try again.", "bad");
  } finally {
    btn.disabled = false;
  }
};
