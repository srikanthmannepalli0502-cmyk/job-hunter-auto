importScripts("api.js");

// Clicking the toolbar icon opens the Job Hunter side panel.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

// Fill to Review's notifications ("ready to submit", "needs you") are named "jh-<tab id>":
// clicking one brings that application's tab and window to the front.
chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = Number(id.replace(/^jh-/, ""));
  if (!tabId) return;
  try {
    const t = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
    await chrome.sidePanel.open({ tabId }); // so Fill to Review can start there
  } catch { /* the tab was closed, or Chrome wants a click on the toolbar icon */ }
  chrome.notifications.clear(id);
});

// Shortlisted in the app: once the tailored resume is ready, open the application (a few at a
// time) and mark the job so the side panel runs Fill to Review when the application form opens.
// Signing in and clicking the posting's Apply button stay yours.
chrome.alarms.get("ready").then((a) => a || chrome.alarms.create("ready", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "ready") openReady().catch(() => {}); });

async function openReady() {
  const { autoOpen = true } = await chrome.storage.local.get("autoOpen");
  if (!autoOpen) return;
  const { jobs = [] } = await api("/api/x/ready");
  for (const job of jobs.slice(0, 3)) {
    await api(`/api/x/opened/${job.id}`, { method: "POST", body: {} }); // once, even if a step below fails
    const tab = await chrome.tabs.create({ url: job.url, active: true });
    const { autostart = {} } = await chrome.storage.session.get("autostart");
    autostart[job.id] = Date.now();
    await chrome.storage.session.set({ autostart });
    chrome.notifications.create(`jh-${tab.id}`, {
      type: "basic", iconUrl: "icon128.png", title: "Resume ready: application opened", requireInteraction: true,
      message: `${job.title} at ${job.company}. Click Apply on the posting (sign in if asked); with the Job Hunter panel open, it fills everything up to Review.`,
    });
  }
}
