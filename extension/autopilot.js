// Fill to Review, run by the background service worker so no side panel has to be open. For one
// application tab it goes from the posting to Review: presses the posting's Apply, signs in to (or
// creates) the Workday account with your saved job-site login, fills each step, attaches the
// tailored resume and presses Save and Continue. It stops and tells you (a notification and the
// app's "Waiting on you" list) at Review, at a question that's yours, a CAPTCHA, an email
// verification or any error the page shows. It never presses Submit.
// Each tab's run lives in chrome.storage.session ("runs"), so it carries on if Chrome restarts
// the service worker; tick() is called on page loads and every minute.

const WORKDAY_HOST = /(^|\.)(myworkdayjobs|myworkdaysite|workday)\.com$/i;
const ONE_PAGE_HOST = /(^|\.)(greenhouse\.io|lever\.co|ashbyhq\.com)$/i;
// Lever and Ashby postings keep the form on a sub-page.
const FORM_PAGE = [[/(^|\.)jobs\.lever\.co$/i, "/apply"], [/(^|\.)jobs\.ashbyhq\.com$/i, "/application"]];
const SIGN_IN_STEP = /sign in|sign-in|create account|log ?in|register/i;
const YOURS = /answer this one yourself|saved answers disagree|more than one option fits/;
const GIVE_UP_MS = 15 * 60 * 1000; // no progress for this long: tell you instead of waiting on
const busy = new Set();     // tabs with a run going in this service worker
const stopping = new Set(); // tabs whose run you stopped
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// Run one of content.js's helpers in the tab's frames (only the top frame for `mainOnly`).
async function inTab(tabId, name, arg, mainOnly = false) {
  const target = mainOnly ? { tabId } : { tabId, allFrames: true };
  await chrome.scripting.executeScript({ target, files: ["matching.js", "content.js"] });
  const results = await chrome.scripting.executeScript({
    target, func: (n, a) => (window.__jobHunterApi ? window.__jobHunterApi[n](a) : null), args: [name, arg ?? null],
  });
  return results.map((r) => r.result).filter(Boolean);
}

// What the page shows, or null while it loads (or if it blocks scripts). The frame with the
// application wins; a CAPTCHA in any frame counts.
async function pageStatus(tabId) {
  let all;
  try { all = await inTab(tabId, "status"); } catch { return null; }
  const st = all.find((s) => s.workday) || all.find((s) => s.account) || all.find((s) => s.posting) ||
             [...all].sort((a, b) => b.form - a.form)[0];
  return st ? { ...st, captcha: all.some((s) => s.captcha) } : null;
}
const known = (st) => st && (st.workday || st.account || st.posting || st.captcha || st.verifyEmail || st.form > 0);
async function waitStatus(tabId, tries = 8) {
  let st = null;
  for (let i = 0; i < tries && !known(st); i++) {
    if (i) await pause(1500);
    st = await pageStatus(tabId);
  }
  return st;
}

let writing = Promise.resolve();
const allRuns = async () => (await chrome.storage.session.get("runs")).runs || {};
function saveRun(tabId, run) { // one write at a time, so runs in two tabs don't overwrite each other
  writing = writing.then(async () => {
    const runs = await allRuns();
    if (run) runs[tabId] = { ...run, updated: Date.now() }; else delete runs[tabId];
    await chrome.storage.session.set({ runs });
  }).catch(() => {});
  return writing;
}
function say(run, text, kind = "") {
  run.lines = [...(run.lines || []), { text, kind }].slice(-40);
  return saveRun(run.tabId, run);
}

// Start (or restart) Fill to Review on a tab. `trust`: you just answered what it stopped on.
async function startRun(tabId, { jobId = null, trust = false } = {}) {
  const old = (await allRuns())[tabId];
  const run = { tabId, jobId: jobId || old?.jobId || null, title: old?.title || "", state: "waiting",
                lines: trust ? old?.lines || [] : [], tried: {}, starts: 0, pages: 0, since: Date.now(), progressAt: Date.now() };
  if (trust) await say(run, "Continuing from here.", "muted"); else await saveRun(tabId, run);
  stopping.delete(tabId);
  return advance(tabId, { trust });
}

// Carry on with every run that isn't finished (or just this tab's).
async function tick(onlyTab = null) {
  const runs = await allRuns();
  for (const [id, run] of Object.entries(runs)) {
    const tabId = Number(id);
    if ((onlyTab && tabId !== onlyTab) || !["waiting", "running"].includes(run.state)) continue;
    try { await chrome.tabs.get(tabId); } catch { await saveRun(tabId, null); continue; } // tab closed
    advance(tabId).catch(() => {});
  }
}
const activeRuns = async () => Object.values(await allRuns()).filter((r) => ["waiting", "running"].includes(r.state));

async function advance(tabId, { trust = false } = {}) {
  if (busy.has(tabId)) return;
  const run = (await allRuns())[tabId];
  if (!run || !["waiting", "running"].includes(run.state)) return;
  busy.add(tabId);
  try {
    await drive(tabId, run, trust);
  } catch (e) {
    await finish(run, "needs_you", "", `Job Hunter hit an error: ${e.message}`);
  } finally {
    busy.delete(tabId);
  }
}

async function drive(tabId, run, trust) {
  run.state = "running";
  await saveRun(tabId, run);
  let ctx = null;
  const context = async (tab) => {
    if (ctx) return ctx;
    const q = new URLSearchParams({ url: tab.url || "", title: tab.title || "" });
    if (run.jobId) q.set("job_id", run.jobId);
    ctx = await api(`/api/x/context?${q}`);
    if (ctx.job) Object.assign(run, { jobId: ctx.job.id, title: `${ctx.job.title} at ${ctx.job.company}` });
    return ctx;
  };
  const moved = () => { run.progressAt = Date.now(); };
  const waitHere = async (why) => {
    if (Date.now() - run.progressAt > GIVE_UP_MS) return finish(run, "needs_you", "", `Waited 15 minutes: ${why}. Take it from here.`);
    run.state = "waiting";
    return saveRun(tabId, run);
  };

  for (let i = 0, first = true; i < 40; i++, first = false) {
    if (stopping.delete(tabId)) return finish(run, "stopped", "", "Stopped. Nothing past this step was touched.");
    const tab = await chrome.tabs.get(tabId);
    const host = new URL(tab.url || "about:blank").hostname;
    const st = await waitStatus(tabId);
    if (!known(st)) return waitHere("the application form didn't appear");
    const where = st.step ? `step ${st.step.n} of ${st.step.of} (${st.step.name})` : "";

    if (st.captcha) return finish(run, "needs_you", where, "The page shows a CAPTCHA. Solve it yourself, then click Continue.");
    if (st.verifyEmail && !st.account && !st.posting) {
      run.tried = {}; // after you verify, it signs in again
      return finish(run, "needs_you", where, "Workday sent a verification email. Open the link in it, then click Continue.");
    }

    if (st.account) {
      if (!WORKDAY_HOST.test(host)) return finish(run, "needs_you", where, "Sign in yourself (Job Hunter signs in only on Workday sites), then click Continue.");
      const login = await Vault.load();
      if (!login) return finish(run, "needs_you", where, "Save your job-site login in the Job Hunter settings (or sign in yourself), then click Continue.");
      if (trust && first) run.tried = {};
      const mode = !run.tried.signin ? "signin" : !run.tried.create ? "create" : null;
      if (!mode) return finish(run, "needs_you", where, `Couldn't sign in or create an account with your saved login (${run.accountError || "no reason shown"}). Sign in yourself, then click Continue.`);
      run.tried[mode] = true;
      await say(run, mode === "signin" ? `Signing in as ${login.email}` : "No account on this site yet: creating one", "muted");
      const { consentOnCreate = true } = await chrome.storage.local.get("consentOnCreate");
      let r = null;
      try { [r] = await inTab(tabId, "account", { ...login, mode, consent: consentOnCreate }, true); } catch { /* the page reloaded */ }
      if (r?.done || !r) { moved(); await say(run, mode === "signin" ? "Signed in" : "Account created", "ok"); }
      else {
        run.accountError = (r.errors || [r.reason]).join("; ");
        await say(run, run.accountError, "warn");
        if (mode === "create" && /already|exist|in use/i.test(run.accountError)) run.tried.create = true;
      }
      await pause(3000);
      continue;
    }

    if (st.workday) {
      if (st.review) {
        await say(run, "Reached Review. Check every answer and the resume, then submit yourself.", "ok");
        return finish(run, "review", st.step.name, "Check it and submit");
      }
      if (SIGN_IN_STEP.test(st.step.name)) { await pause(2000); continue; } // its form is still drawing
      if (run.pages >= 8) return finish(run, "needs_you", where, "It went through 8 steps without reaching Review.");
      const c = await context(tab);
      await say(run, `Step ${st.step.n} of ${st.step.of}: ${st.step.name}`, "step");
      const { filled, skipped } = await fillPage(tabId, c);
      await say(run, `filled ${filled.length}${skipped.length ? `; left ${skipped.length}: ${skipped.slice(0, 4).map((s) => s.slice(0, 50)).join("; ")}` : ""}`, "muted");
      if (st.needsResume && c.job?.has_resume && await attachResume(tabId, c.job.id)) {
        for (let k = 0; k < 40 && (await pageStatus(tabId))?.needsResume; k++) await pause(250); // upload finishing
        await say(run, "attached your tailored resume (open it on Review to check)", "muted");
      }
      const yours = skipped.filter((s) => YOURS.test(s));
      if (yours.length && !(trust && run.pages === 0)) return finish(run, "needs_you", where, `Answer these on the page: ${yours.slice(0, 3).join("; ")}`, yours);
      if (stopping.delete(tabId)) return finish(run, "stopped", where, "Stopped. Nothing past this step was touched.");
      const next = await inTab(tabId, "next");
      if (!next.some((r) => r.moved)) {
        const errs = (await pageStatus(tabId))?.errors || [];
        return finish(run, "needs_you", where, errs.length ? `The page didn't move on. It says: ${errs.join("; ")}` : "The page didn't move on.", errs);
      }
      run.pages++;
      trust = false;
      moved();
      continue;
    }

    if (st.posting) {
      if (run.starts >= 3) return finish(run, "needs_you", "", "Pressing Apply on the posting didn't open the application.");
      run.starts++;
      await say(run, "Pressing Apply, then Apply Manually", "muted");
      const [r] = await inTab(tabId, "start", null, true).catch(() => []);
      if (r && !r.started) await say(run, r.reason, "warn");
      moved();
      await pause(3000);
      continue;
    }

    const formPage = FORM_PAGE.find(([re]) => re.test(host));
    const url = new URL(tab.url);
    if (formPage && !url.pathname.endsWith(formPage[1])) {
      await chrome.tabs.update(tabId, { url: url.origin + url.pathname.replace(/\/+$/, "") + formPage[1] });
      moved();
      return waitHere("the application form didn't load");
    }
    if (WORKDAY_HOST.test(host)) return waitHere("the Workday application didn't open");

    // A one-page form (Greenhouse, Lever, Ashby, or a site Job Hunter doesn't step through).
    const c = await context(tab);
    const { filled, skipped } = await fillPage(tabId, c);
    const attached = c.job?.has_resume && await attachResume(tabId, c.job.id);
    await say(run, `filled ${filled.length}${attached ? " and attached your tailored resume" : ""}${skipped.length ? `; left ${skipped.length}: ${skipped.slice(0, 4).map((s) => s.slice(0, 50)).join("; ")}` : ""}`, "muted");
    return ONE_PAGE_HOST.test(host)
      ? finish(run, "review", "the form", skipped.length ? `Filled. Answer the ${skipped.length} left, then submit.` : "Filled. Check it and submit.")
      : finish(run, "needs_you", "the form", "Filled what it could. Job Hunter doesn't step through this site: finish it yourself.");
  }
  return finish(run, "needs_you", "", "It kept going without reaching Review.");
}

async function fillPage(tabId, ctx) {
  const frames = await inTab(tabId, "fill", ctx);
  return { filled: frames.flatMap((f) => f.filled || []), skipped: frames.flatMap((f) => f.skipped || []) };
}
async function attachResume(tabId, jobId) {
  try { return (await inTab(tabId, "attach", await fileArg("resume", jobId))).some((f) => f.attached); }
  catch { return false; }
}

// End the run: a notification you can click to come back to the tab, and the job's state in the app.
async function finish(run, state, step, note, items = []) {
  run.state = state;
  if (state !== "review") await say(run, note, state === "stopped" ? "warn" : "needs");
  else await saveRun(run.tabId, run);
  if (state === "stopped") return;
  const where = run.title || "This application";
  chrome.notifications.create(`jh-${run.tabId}`, {
    type: "basic", iconUrl: "icon128.png", requireInteraction: true, priority: 2,
    title: state === "review" ? "Ready for you to submit" : "Job Hunter needs you",
    message: (state === "review" ? `${where} is at Review. Check it and submit.` : `${where}${step ? `, ${step}` : ""}: ${note}`).slice(0, 300),
    ...(state === "needs_you" ? { buttons: [{ title: "I've done it, continue" }] } : {}),
  });
  if (run.jobId) {
    const tab = await chrome.tabs.get(run.tabId).catch(() => ({}));
    await api(`/api/x/progress/${run.jobId}`, { method: "POST", body: { state, step, note: items.length ? items.slice(0, 3).join("; ") : note, url: tab.url || "" } }).catch(() => {});
  }
}
