// Calls to your Job Hunter app. The app address and extension key are kept in chrome.storage.local.
async function settings() {
  const { appUrl = "", key = "" } = await chrome.storage.local.get(["appUrl", "key"]);
  return { appUrl: appUrl.replace(/\/+$/, ""), key };
}

async function api(path, { method = "GET", body = null, raw = false } = {}) {
  const { appUrl, key } = await settings();
  if (!appUrl || !key) throw new Error("Open the extension settings and enter your app address and key.");
  const resp = await fetch(appUrl + path, {
    method,
    headers: { "X-Extension-Key": key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : null,
  });
  if (raw) {
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).error || `HTTP ${resp.status}`);
    return resp;
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}
