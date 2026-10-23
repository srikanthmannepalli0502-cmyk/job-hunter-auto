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
  } catch { /* the tab was closed */ }
  chrome.notifications.clear(id);
});
