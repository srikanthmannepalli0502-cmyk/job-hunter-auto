// Calls to your Job Hunter app. The app address and extension key are kept in chrome.storage.local.
async function settings() {
  const { appUrl = "", key = "" } = await chrome.storage.local.get(["appUrl", "key"]);
  return { appUrl: appUrl.replace(/\/+$/, ""), key };
}

async function api(path, { method = "GET", body = null, raw = false } = {}) {
  const { appUrl, key } = await settings();
  if (!appUrl || !key) throw new Error("Open the extension settings and enter your app address and key.");
  // The key is the only credential. Azure's sign-in layer refuses a POST from the extension that
  // carries your app sign-in cookie (HTTP 403, empty body), so cookies are left off.
  const resp = await fetch(appUrl + path, {
    method,
    credentials: "omit",
    headers: { "X-Extension-Key": key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : null,
  });
  if (!resp.ok) {
    // The app answers in JSON; anything else came from in between (e.g. Azure), so say what and where.
    const text = await resp.text().catch(() => "");
    let error = "";
    try { error = JSON.parse(text).error || ""; } catch { /* not JSON */ }
    throw new Error(error || `HTTP ${resp.status} from ${path.split("?")[0]}: ${text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 150)}`);
  }
  return raw ? resp : resp.json().catch(() => ({}));
}

// The job's tailored resume (or cover note) as content.js's attach() takes it.
async function fileArg(which, jobId) {
  const resp = await api(`/api/x/${which}/${jobId}`, { raw: true });
  const buf = new Uint8Array(await resp.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { base64: btoa(bin), filename: resp.headers.get("X-Filename") || `${which}.pdf`,
           mime: resp.headers.get("Content-Type") || "application/pdf", which };
}
