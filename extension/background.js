// Gets the Moodle app key the same way the official Moodle phone app does, then hands it to
// the Deadline Hub app running on this computer. The key never goes anywhere else.
const APP = "http://127.0.0.1:8737/";

async function appRunning() {
  try { await fetch(APP + "api/state", { mode: "no-cors" }); return true; } catch { return false; }
}

function getKey(wwwroot) {
  return new Promise((resolve, reject) => {
    const passport = String(Math.floor(Math.random() * 1e9));
    const launch = `${wwwroot}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=${passport}&urlscheme=moodlemobile`;
    let tabId = null, done = false;
    const finish = (err, link) => {
      if (done) return; done = true;
      chrome.webRequest.onBeforeRedirect.removeListener(onRedirect);
      clearTimeout(timer);
      if (tabId !== null) chrome.tabs.remove(tabId).catch(() => {});
      err ? reject(err) : resolve(link);
    };
    const onRedirect = (d) => {
      if (d.redirectUrl && d.redirectUrl.startsWith("moodlemobile://")) finish(null, d.redirectUrl);
    };
    chrome.webRequest.onBeforeRedirect.addListener(onRedirect, { urls: [`${wwwroot}/admin/tool/mobile/launch.php*`] });
    const timer = setTimeout(() => finish(new Error(
      "Moodle didn't hand over a key. Make sure you're signed in to Moodle in this browser, then try again.")), 25000);
    chrome.tabs.create({ url: launch, active: false }).then(t => { tabId = t.id; });
  });
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== "connect") return;
  (async () => {
    if (!(await appRunning())) return reply({ ok: false, error: "Open the Deadline Hub app first, then click Connect again." });
    try {
      const link = await getKey(msg.wwwroot);
      await chrome.tabs.create({ url: `${APP}#moodle=${encodeURIComponent(link)}&site=${encodeURIComponent(msg.wwwroot)}` });
      reply({ ok: true });
    } catch (e) {
      reply({ ok: false, error: e.message });
    }
  })();
  return true; // reply comes later
});
