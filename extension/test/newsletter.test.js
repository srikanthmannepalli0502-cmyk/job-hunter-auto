// A newsletter email box on a posting is not an email-first sign-in (Stripe); headless Chrome.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const CHROME = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium"]
  .find((p) => p && fs.existsSync(p));

test("a posting's newsletter email box is not a sign-in: Apply wins", { skip: !CHROME && "Chrome not found" }, () => {
  const url = pathToFileURL(path.join(__dirname, "newsletter_fixture.html")).href;
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--allow-file-access-from-files",
    "--virtual-time-budget=5000", "--dump-dom", url], { encoding: "utf8", timeout: 60000, stdio: ["ignore", "pipe", "ignore"] });
  const st = JSON.parse(dom.match(/<pre id="result">([\s\S]*?)<\/pre>/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
  assert.equal(st.account, null);
  assert.equal(st.posting, true);
});
