importScripts("api.js", "vault.js", "autopilot.js");

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
  reply({ ok: true });
});

// Runs carry on as their pages load (Workday changes the URL without a page load).
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === "complete" || info.url) tick(tabId).catch(() => {}); });
chrome.tabs.onRemoved.addListener((tabId) => saveRun(tabId, null));

// Every minute: carry on with unfinished runs, and once nothing is running, open the next
// shortlisted job whose tailored resume is ready and fill it up to Review. One application at a
// time, in the front tab: Chrome slows pages in background tabs.
chrome.alarms.get("ready").then((a) => a || chrome.alarms.create("ready", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "ready") tick().then(openReady).catch(() => {});
});

async function openReady() {
  const { autoOpen = true } = await chrome.storage.local.get("autoOpen");
  if (!autoOpen || (await activeRuns()).length) return;
  const { jobs = [] } = await api("/api/x/ready");
  const job = jobs[0];
  if (!job) return;
  await api(`/api/x/opened/${job.id}`, { method: "POST", body: {} }); // once, even if a step below fails
  const tab = await chrome.tabs.create({ url: job.url, active: true });
  await saveRun(tab.id, { tabId: tab.id, jobId: job.id, title: `${job.title} at ${job.company}`, state: "waiting",
                          lines: [{ text: "Opened after you shortlisted it; filling it up to Review.", kind: "muted" }],
                          tried: {}, starts: 0, pages: 0, since: Date.now(), progressAt: Date.now() });
}
