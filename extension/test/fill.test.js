// Runs content.js's fill() on fill_fixture.html in headless Chrome; skipped when Chrome isn't installed.
// Set CHROME_PATH to use a particular browser.
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
  const url = pathToFileURL(path.join(__dirname, "fill_fixture.html")).href + "?run";
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
    "--virtual-time-budget=15000", "--dump-dom", url], { encoding: "utf8", timeout: 60000, stdio: ["ignore", "pipe", "ignore"] });
  const json = dom.match(/<pre id="result">([\s\S]*?)<\/pre>/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  assert.ok(json, "fill() did not finish");
  return JSON.parse(json);
}

test("fill() leaves fail-closed, ambiguous and dropped fields to you", { skip: !CHROME && "Chrome not found" }, () => {
  const r = run();
  assert.equal(r.error, undefined);
  const filled = Object.fromEntries(r.filled.map((f) => [f.label, f.value]));
  assert.deepEqual(filled, {
    "First Name": "Sam",
    "City": "Syracuse",                // re-rendered by the page: still filled
    "Phone Number": "+1-315-289-0183", // reformatted by the page: still filled
    "Country": "United States of America",
    "Are you legally authorized to work in the United States?": "Yes",
  });
  const skipped = r.skipped.join("\n");
  assert.match(skipped, /Last Name \(the page didn't keep "Doe"\)/);
  assert.match(skipped, /Do you currently require visa sponsorship\? \(answer this one yourself\)/);
  assert.match(skipped, /require sponsorship for employment\? \(no option like "Yes"\)/);
  assert.match(skipped, /willing to relocate\? \(your saved answers disagree\)/);
  assert.match(skipped, /How did you hear about us\? \(more than one option fits "LinkedIn"\)/);
});
