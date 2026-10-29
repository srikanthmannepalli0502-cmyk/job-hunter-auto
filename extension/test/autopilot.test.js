// Runs content.js's status()/next() on autopilot_fixture.html in headless Chrome; skipped without Chrome.
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
  const url = pathToFileURL(path.join(__dirname, "autopilot_fixture.html")).href + "?run";
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
    "--virtual-time-budget=60000", "--dump-dom", url], { encoding: "utf8", timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
  const json = dom.match(/<pre id="result">([\s\S]*?)<\/pre>/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  assert.ok(json, "the fixture did not finish");
  return JSON.parse(json);
}

test("autopilot presses Save and Continue up to Review and never Submit", { skip: !CHROME && "Chrome not found" }, () => {
  const r = run();
  assert.equal(r.error, undefined);
  assert.deepEqual(r.start.step, { n: 1, of: 3, name: "My Information" });
  assert.equal(r.start.next, true);
  assert.equal(r.blocked.moved, false);
  assert.deepEqual(r.blocked.errors, ["First Name is required"]);
  assert.equal(r.toQuestions.moved, true);
  assert.equal(r.questions.step.name, "Application Questions");
  assert.equal(r.questions.needsResume, true);
  assert.deepEqual(r.submitLabel, { moved: false, reason: "no Next or Save and Continue button" });
  assert.equal(r.toReview.moved, true);
  assert.equal(r.review.review, true);
  assert.equal(r.review.next, false);
  assert.deepEqual(r.atReview, { moved: false, reason: "review" });
  assert.equal(r.submitted, false);
});
