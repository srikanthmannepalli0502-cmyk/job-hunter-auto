// Runs content.js's fill() on combobox_fixture.html (an amazon.jobs-like dropdown) in headless
// Chrome; skipped without Chrome.
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

test("an ARIA combobox: read by its question, the company's careers site chosen, no hidden resume box counted",
  { skip: !CHROME && "Chrome not found" }, () => {
    const url = pathToFileURL(path.join(__dirname, "combobox_fixture.html")).href + "?run";
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
      "--virtual-time-budget=30000", "--dump-dom", url], { encoding: "utf8", timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
    const json = dom.match(/<pre id="result">([\s\S]*?)<\/pre>/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    const r = JSON.parse(json);
    assert.equal(r.error, undefined);
    assert.deepEqual(r.questions, ["How did you hear about this role?"]);
    assert.equal(r.before.needsResume, false); // the Resume step's Replace input is hidden on this step
    assert.equal(r.chosen, "Amazon Career Site"); // the saved Comcast answer matches nothing here
    assert.deepEqual(r.fill.skipped, []);
  });
