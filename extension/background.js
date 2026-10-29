importScripts("api.js", "vault.js", "mail.js", "autopilot.js");

// Clicking the toolbar icon opens the Job Hunter side panel.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

// Notifications are named "jh-<tab id>": clicking one brings that application's tab to the front;
// "I've done it, continue" carries on from where it stopped.
chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = Number(id.replace(/^jh-/, ""));
  if (!tabId) return;
  try {
    const t = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
  } catch { /* the tab was closed */ }
  chrome.notifications.clear(id);
});
chrome.notifications.onButtonClicked.addListener((id) => {
  const tabId = Number(id.replace(/^jh-/, ""));
  chrome.notifications.clear(id);
  if (tabId) startRun(tabId, { trust: true }).catch(() => {});
});

// The side panel's Fill to Review, Continue and Stop buttons.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.autopilot === "run") startRun(msg.tabId, { jobId: msg.jobId, trust: !!msg.trust }).catch(() => {});
  if (msg?.autopilot === "stop") stopping.add(msg.tabId);
  if (msg?.autoOpen === "check") { checkReady().then(reply); return true; } // reply once the check is done
  reply({ ok: true });
});

// Runs carry on as their pages load (Workday changes the URL without a page load).
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === "complete" || info.url) tick(tabId).catch(() => {}); });
// A closed tab frees its place: the next ready job opens right away, not on the next minute.
chrome.tabs.onRemoved.addListener((tabId) => saveRun(tabId, null).then(checkReady));

// Every minute: carry on with unfinished runs, and open every shortlisted job whose tailored
// resume is ready, each in its own background tab, filling them all at once (up to the "at most"
// setting; the rest open as places free up).
chrome.alarms.get("ready").then((a) => a || chrome.alarms.create("ready", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "ready") tick().then(checkReady);
});

// What the last check found, shown in the settings and the side panel ("autoStatus"). One check
// at a time, so two never open the same job.
let checking = null;
function checkReady() {
  checking ||= (async () => {
    let text;
    try { text = await openReady(); } catch (e) { text = `Couldn't ask your Job Hunter app: ${e.message}`; }
    const autoStatus = { at: Date.now(), text };
    await chrome.storage.local.set({ autoStatus });
    return autoStatus;
  })().finally(() => { checking = null; });
  return checking;
}

async function openReady() {
  const { autoOpen = true, maxOpen = MAX_OPEN } = await chrome.storage.local.get(["autoOpen", "maxOpen"]);
  if (!autoOpen) return "Turned off in the settings.";
  const going = await activeRuns();
  const { jobs = [] } = await api("/api/x/ready");
  if (!jobs.length) return `No shortlisted job waiting to open (a resume may still be in progress).${going.length ? ` Filling ${going.length} now.` : ""}`;
  const room = maxOpen - going.length;
  if (room <= 0) return `Filling ${going.length} (the most at once); ${jobs.length} more open as these finish.`;
  const opened = [];
  for (const job of jobs.slice(0, room)) {
    await api(`/api/x/opened/${job.id}`, { method: "POST", body: {} }); // once, even if a step below fails
    // In the background, so a new one never pulls you away from the one you're checking.
    const tab = await chrome.tabs.create({ url: job.url, active: false });
    chrome.tabs.update(tab.id, { autoDiscardable: false }).catch(() => {}); // Chrome mustn't unload it mid-fill
    await saveRun(tab.id, { tabId: tab.id, jobId: job.id, title: `${job.title} at ${job.company}`, state: "waiting", jobUrl: job.url,
                            lines: [{ text: "Opened after you shortlisted it; filling it up to Review.", kind: "muted" }],
                            tried: {}, starts: 0, pages: 0, since: Date.now(), progressAt: Date.now() });
    opened.push(`${job.title} at ${job.company}`);
  }
  const more = jobs.length - opened.length;
  return `Opened ${opened.length}: ${opened.join("; ")}. ${going.length + opened.length} filling now.${more ? ` ${more} more open as these finish.` : ""}`;
}
