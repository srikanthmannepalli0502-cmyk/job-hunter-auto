// Runs content.js's start()/account() on account_fixture.html in headless Chrome; skipped without Chrome.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const CHROME = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find((p) => p && fs.existsSync(p));

function run() {
  const url = pathToFileURL(path.join(__dirname, "account_fixture.html")).href + "?run";
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
    "--virtual-time-budget=60000", "--dump-dom", url], { encoding: "utf8", timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
  const json = dom.match(/<pre id="result">([\s\S]*?)<\/pre>/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  assert.ok(json, "the fixture did not finish");
  return JSON.parse(json);
}

test("Apply, then sign in or create the Workday account with the saved login", { skip: !CHROME && "Chrome not found" }, () => {
  const r = run();
  assert.equal(r.error, undefined);
  assert.equal(r.posting.posting, true);
  assert.equal(r.posting.account, null);
  assert.deepEqual(r.start, { started: true });
  assert.equal(r.atCreate.account, "create");
  assert.equal(r.atCreate.posting, false);
  // Sign in first: the form switches to Sign In, and the site says there's no such account.
  assert.equal(r.signinFirst.done, false);
  assert.match(r.signinFirst.errors[0], /wrong email address or password/i);
  assert.equal(r.atSignin.account, "signin");
  // Creating it: the terms box is ticked only when allowed.
  assert.match(r.noConsent.reason, /agree to its terms/);
  assert.deepEqual(r.create, { done: true });
  assert.equal(r.afterCreate.workday, true);
  assert.equal(r.afterCreate.account, null);
  assert.deepEqual(r.signinAgain, { done: true });
  assert.equal(r.afterSignin.step.name, "My Information");
  // Clicks went through the click_filter overlays; the header's Sign In link was never pressed.
  assert.ok(!r.clicks.includes("utilityButtonSignIn"));
  assert.ok(r.clicks.includes("createAccountSubmitButton") && r.clicks.includes("signInSubmitButton"));
});
