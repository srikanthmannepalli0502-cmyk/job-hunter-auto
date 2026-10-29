const $ = (id) => document.getElementById(id);

chrome.storage.local.get(["appUrl", "key", "autoOpen", "maxOpen"]).then(({ appUrl = "", key = "", autoOpen = true, maxOpen = MAX_OPEN }) => {
  $("appUrl").value = appUrl;
  $("key").value = key;
  $("autoOpen").checked = autoOpen;
  $("maxOpen").value = maxOpen;
});

$("save").addEventListener("click", async () => {
  const appUrl = $("appUrl").value.trim();
  if (!/^https:\/\/|^http:\/\/(127\.0\.0\.1|localhost)/.test(appUrl)) {
    $("status").textContent = "The address must start with https:// (or http://127.0.0.1 for a local app).";
    return;
  }
  const maxOpen = Math.min(25, Math.max(1, parseInt($("maxOpen").value, 10) || MAX_OPEN));
  $("maxOpen").value = maxOpen;
  await chrome.storage.local.set({ appUrl, key: $("key").value.trim(), autoOpen: $("autoOpen").checked, maxOpen });
  $("status").textContent = "Testing...";
  try {
    const ctx = await api("/api/x/context?url=" + encodeURIComponent("https://example.com/"));
    $("status").textContent = `Connected. ${ctx.fields.length} profile fields and ${ctx.answers.length} saved answers.`;
  } catch (e) {
    $("status").textContent = "Could not connect: " + e.message;
  }
});

// Job-site login (vault.js): shown masked; "Make one" fills in a strong new password.
chrome.storage.local.get("consentOnCreate").then(({ consentOnCreate = true }) => { $("consentOnCreate").checked = consentOnCreate; });
Vault.load().then((login) => {
  if (login) {
    $("loginEmail").value = login.email;
    $("loginPassword").value = login.password;
    $("loginStatus").textContent = "A login is saved.";
  }
});
$("showPassword").addEventListener("change", () => { $("loginPassword").type = $("showPassword").checked ? "text" : "password"; });
$("makePassword").addEventListener("click", () => {
  $("loginPassword").value = Vault.generate();
  $("loginPassword").type = "text";
  $("showPassword").checked = true;
  $("loginStatus").textContent = "Note it down somewhere safe, then Save login.";
});
$("saveLogin").addEventListener("click", async () => {
  const email = $("loginEmail").value.trim(), password = $("loginPassword").value;
  await chrome.storage.local.set({ consentOnCreate: $("consentOnCreate").checked });
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || password.length < 8) {
    $("loginStatus").textContent = "Enter an email and a password of at least 8 characters.";
    return;
  }
  try {
    await Vault.save(email, password);
  } catch (e) {
    $("loginStatus").textContent = `Could not save it in this browser: ${e.message}`;
    return;
  }
  $("loginPassword").type = "password";
  $("showPassword").checked = false;
  $("loginStatus").textContent = "Saved (encrypted, in this browser only).";
});
$("removeLogin").addEventListener("click", async () => {
  await Vault.clear();
  $("loginEmail").value = $("loginPassword").value = "";
  $("loginStatus").textContent = "Removed. Job Hunter will ask you to sign in yourself.";
});

// What auto-open found the last time it looked (every minute), and a way to look now.
const showAuto = (st) => { $("autoStatus").textContent = st ? `${new Date(st.at).toLocaleTimeString()}: ${st.text}` : "not yet"; };
chrome.storage.local.get("autoStatus").then(({ autoStatus }) => showAuto(autoStatus));
chrome.storage.onChanged.addListener((c, area) => { if (area === "local" && c.autoStatus) showAuto(c.autoStatus.newValue); });
$("checkNow").addEventListener("click", async () => {
  $("autoStatus").textContent = "checking...";
  showAuto(await chrome.runtime.sendMessage({ autoOpen: "check" }));
});
