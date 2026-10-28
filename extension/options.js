const $ = (id) => document.getElementById(id);

chrome.storage.local.get(["appUrl", "key", "autoOpen"]).then(({ appUrl = "", key = "", autoOpen = true }) => {
  $("appUrl").value = appUrl;
  $("key").value = key;
  $("autoOpen").checked = autoOpen;
});

$("save").addEventListener("click", async () => {
  const appUrl = $("appUrl").value.trim();
  if (!/^https:\/\/|^http:\/\/(127\.0\.0\.1|localhost)/.test(appUrl)) {
    $("status").textContent = "The address must start with https:// (or http://127.0.0.1 for a local app).";
    return;
  }
  await chrome.storage.local.set({ appUrl, key: $("key").value.trim(), autoOpen: $("autoOpen").checked });
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
