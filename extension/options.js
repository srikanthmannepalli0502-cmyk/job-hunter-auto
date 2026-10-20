const $ = (id) => document.getElementById(id);

chrome.storage.local.get(["appUrl", "key"]).then(({ appUrl = "", key = "" }) => {
  $("appUrl").value = appUrl;
  $("key").value = key;
});

$("save").addEventListener("click", async () => {
  const appUrl = $("appUrl").value.trim();
  if (!/^https:\/\/|^http:\/\/(127\.0\.0\.1|localhost)/.test(appUrl)) {
    $("status").textContent = "The address must start with https:// (or http://127.0.0.1 for a local app).";
    return;
  }
  await chrome.storage.local.set({ appUrl, key: $("key").value.trim() });
  $("status").textContent = "Testing...";
  try {
    const ctx = await api("/api/x/context?url=" + encodeURIComponent("https://example.com/"));
    $("status").textContent = `Connected. ${ctx.fields.length} profile fields and ${ctx.answers.length} saved answers.`;
  } catch (e) {
    $("status").textContent = "Could not connect: " + e.message;
  }
});
