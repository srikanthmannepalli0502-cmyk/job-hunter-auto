// Fill to Review, run by the background service worker so no side panel has to be open. For one
// application tab it goes from the posting to Review on any site: presses the posting's Apply,
// signs in to (or creates) the site's account with your saved job-site login, confirms the account
// from the site's email when it asks (mail.js), fills each page, attaches the tailored resume and
// presses Save and Continue / Next. It stops and tells you (a notification and the app's "Waiting
// on you" list) at Review, at a question that's yours, a CAPTCHA or any error the page shows.
// It never presses Submit.
// Each tab's run lives in chrome.storage.session ("runs"), so it carries on if Chrome restarts
// the service worker; tick() is called on page loads and every minute. Runs in different tabs go
// on at the same time.

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
// A page the run can act on. A page of only buttons counts when one is Next / Skip & continue
// (amazon.jobs's SMS updates) or Submit.
const known = (st) => st && (st.workday || st.account || st.posting || st.closed || st.captcha || st.verifyEmail ||
                             st.form > 0 || st.next || st.review);
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
                jobUrl: old?.jobUrl, hosts: old?.hosts || [], accountAt: old?.accountAt,
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

// Your saved login is typed only on the sites this application's tab went through on its own
// (the posting, and where its Apply button led), never on a sign-in page of Google, Microsoft,
// LinkedIn, Amazon.com and the like that a site hands you to.
const IDENTITY_HOST = /(^|\.)(google\.com|googleapis\.com|microsoftonline\.com|microsoft\.com|live\.com|linkedin\.com|facebook\.com|apple\.com|amazon\.com|github\.com|okta\.com|auth0\.com|indeed\.com|yahoo\.com)$/i;
const MAIL_WAIT_MS = 10 * 60 * 1000; // how long to keep looking for a site's confirmation email
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const loginSite = (run, host) => !IDENTITY_HOST.test(host) && (run.hosts || []).includes(baseDomain(host));
const allowPattern = (run) => `(^|\\.)(${(run.hosts || []).map(escape).join("|")})$`;

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
    if (ctx.job?.url && !run.jobUrl) run.jobUrl = ctx.job.url;
    return ctx;
  };
  const moved = () => { run.progressAt = Date.now(); };
  const waitHere = async (why) => {
    if (Date.now() - run.progressAt > GIVE_UP_MS) return finish(run, "needs_you", "", `Waited 15 minutes: ${why}. Take it from here.`);
    run.state = "waiting";
    if (run.waitingFor !== why) { run.waitingFor = why; return say(run, `Waiting: ${why} (it looks again each minute).`, "muted"); }
    return saveRun(tabId, run);
  };
  // The site's confirmation email: {link} / {code}, or {stop} once it has finished the run or
  // set it to wait for the email.
  const mail = async (kind, where) => {
    const login = await Vault.load();
    const since = run.accountAt || run.since;
    await say(run, `Looking in ${login?.email || "your email"} for the site's ${kind === "code" ? "code" : "confirmation link"}`, "muted");
    const r = await fromMail({ email: login?.email, kind, since, hosts: [...(run.hosts || []), new URL((await chrome.tabs.get(tabId)).url).hostname] })
      .catch((e) => ({ error: e.message }));
    if (r.link || r.code) return r;
    const yourself = kind === "code" ? "Type the code from the site's email yourself" : "Open the link in the site's email yourself";
    if (r.error) return { stop: await finish(run, "needs_you", where, `${yourself}, then click Continue (${r.error}).`) };
    if (Date.now() - since > MAIL_WAIT_MS) return { stop: await finish(run, "needs_you", where, `The site's email hasn't come in 10 minutes. ${yourself}, then click Continue.`) };
    return { stop: await waitHere("the site's email didn't come") }; // looks again on the next check
  };

  for (let i = 0, first = true; i < 50; i++, first = false) {
    if (stopping.delete(tabId)) return finish(run, "stopped", "", "Stopped. Nothing past this step was touched.");
    const tab = await chrome.tabs.get(tabId);
    const host = new URL(tab.url || "about:blank").hostname;
    if (/^https?:/.test(tab.url || "") && !IDENTITY_HOST.test(host) && !(run.hosts ||= []).includes(baseDomain(host))) run.hosts.push(baseDomain(host));
    const st = await waitStatus(tabId);
    if (!known(st)) {
      // After the confirmation link: back to the application, where it signs in.
      if (run.verified && !run.reopened && run.jobUrl) {
        run.reopened = true;
        await chrome.tabs.update(tabId, { url: run.jobUrl });
        moved();
        return waitHere("the application didn't open again after confirming your email");
      }
      return waitHere("the application form didn't appear");
    }
    const where = st.step ? `step ${st.step.n} of ${st.step.of} (${st.step.name})` : "";

    if (st.closed) {
      // Marked closed in the app, so it isn't opened again; the next shortlisted job opens on the next check.
      if (run.jobId) await api(`/api/x/closed/${run.jobId}`, { method: "POST", body: {} }).catch(() => {});
      return finish(run, "stopped", where, `The posting is closed ("${st.closed}"). Marked closed in Job Hunter; the next shortlisted job opens next.`);
    }
    if (st.captcha) return finish(run, "needs_you", where, "The page shows a CAPTCHA. Solve it yourself, then click Continue.");
    if (st.verifyEmail && !st.account && !st.posting) {
      const r = await mail("link", where);
      if ("stop" in r) return;
      await say(run, "Opening the confirmation link from the site's email", "ok");
      await chrome.tabs.update(tabId, { url: r.link });
      Object.assign(run, { tried: {}, verified: true, reopened: false }); // then it signs in again
      moved();
      await pause(4000);
      continue;
    }

    if (st.account) {
      if (!loginSite(run, host)) return finish(run, "needs_you", where, `This sign-in is on ${host}, not the job's own site; Job Hunter doesn't type your login there. Sign in yourself, then click Continue.`);
      const login = await Vault.load();
      if (!login) return finish(run, "needs_you", where, "Save your job-site login in the Job Hunter settings (or sign in yourself), then click Continue.");
      if (trust && first) run.tried = {};
      const workday = WORKDAY_HOST.test(host);
      let mode = st.account;
      if (mode === "code") {
        const r = await mail("code", where);
        if ("stop" in r) return;
        if ((run.codes = (run.codes || 0) + 1) > 3) return finish(run, "needs_you", where, "The site kept asking for a code. Type it yourself, then click Continue.");
        await say(run, "Typing the code from the site's email", "muted");
        const [c] = await inTab(tabId, "account", { mode, code: r.code, allow: allowPattern(run) }, true).catch(() => []);
        if (c && !c.done) await say(run, (c.errors || [c.reason]).join("; "), "warn");
        moved();
        await pause(3000);
        continue;
      }
      if (mode === "email") {
        if ((run.emailPages = (run.emailPages || 0) + 1) > 3) return finish(run, "needs_you", where, "The sign-in page didn't move on after your email. Sign in yourself, then click Continue.");
        await say(run, `Signing in as ${login.email}`, "muted");
        const [e] = await inTab(tabId, "account", { mode, email: login.email, allow: allowPattern(run) }, true).catch(() => []);
        if (e && !e.done) await say(run, (e.errors || [e.reason]).join("; "), "warn");
        moved();
        await pause(3000);
        continue;
      }
      // Workday shows Create Account first, so it tries Sign In first; other sites show the form that
      // fits your email (amazon.jobs asks for the email first).
      mode = mode === "create" && !workday && !run.tried.create ? "create"
           : !run.tried.signin ? "signin" : !run.tried.create ? "create" : null;
      if (!mode) return finish(run, "needs_you", where, `Couldn't sign in or create an account with your saved login (${run.accountError || "no reason shown"}). Sign in yourself, then click Continue.`);
      run.tried[mode] = true;
      await say(run, mode === "signin" ? `Signing in as ${login.email}` : "No account on this site yet: creating one", "muted");
      if (mode === "create" && !workday) await fillPage(tabId, await context(tab)).catch(() => {}); // name, phone ...
      const { consentOnCreate = true } = await chrome.storage.local.get("consentOnCreate");
      let r = null;
      try { [r] = await inTab(tabId, "account", { ...login, mode, consent: consentOnCreate, allow: allowPattern(run) }, true); } catch { /* the page reloaded */ }
      if (r?.done || !r) {
        moved();
        if (mode === "create") run.accountAt = Date.now(); // its confirmation email comes after this
        await say(run, mode === "signin" ? "Signed in" : "Account created", "ok");
      } else {
        run.accountError = (r.errors || [r.reason]).join("; ");
        await say(run, run.accountError, "warn");
        if (mode === "create" && /already|exist|in use/i.test(run.accountError)) run.tried.create = true;
      }
      await pause(3000);
      continue;
    }

    if (st.posting) {
      if (run.starts >= 3) return finish(run, "needs_you", "", "Pressing Apply on the posting didn't open the application.");
      run.starts++;
      await say(run, WORKDAY_HOST.test(host) ? "Pressing Apply, then Apply Manually" : "Pressing Apply", "muted");
      const [r] = await inTab(tabId, "start", null, true).catch(() => []);
      if (r && !r.started) await say(run, r.reason, "warn");
      moved();
      await pause(3000);
      continue;
    }

    if (st.workday && SIGN_IN_STEP.test(st.step.name)) { await pause(2000); continue; } // its form is still drawing
    if (st.workday && st.review) {
      await say(run, "Reached Review. Check every answer and the resume, then submit yourself.", "ok");
      return finish(run, "review", st.step.name, "Check it and submit");
    }

    const formPage = FORM_PAGE.find(([re]) => re.test(host));
    const url = new URL(tab.url);
    if (!st.workday && formPage && !url.pathname.endsWith(formPage[1])) {
      await chrome.tabs.update(tabId, { url: url.origin + url.pathname.replace(/\/+$/, "") + formPage[1] });
      moved();
      return waitHere("the application form didn't load");
    }
    if (!st.workday && WORKDAY_HOST.test(host)) return waitHere("the Workday application didn't open");
    // A page with a few boxes (a job search) and no Apply, Next or Submit isn't an application.
    if (!st.workday && !st.next && !st.review && !ONE_PAGE_HOST.test(host) && !st.needsResume && (st.fields ?? st.form) < 8) {
      return finish(run, "needs_you", "", `No Apply button or application form on this ${host} page. Open the application yourself, then click Continue and it fills it.`);
    }

    // A step of the application: Workday's, or another site's page with Next / Submit.
    if (run.pages >= 12) return finish(run, "needs_you", where, "It went through 12 pages without reaching Review.");
    // Workday draws a step's title before its fields and buttons: wait for those first.
    const page = `${tab.url}|${st.step?.n || st.title}`;
    if (run.drawing?.page !== page) run.drawing = { page, waits: 0 };
    if (st.workday && (st.form < 2 || !st.next) && run.drawing.waits++ < 10) { await pause(1500); continue; }
    const c = await context(tab);
    await say(run, st.step ? `Step ${st.step.n} of ${st.step.of}: ${st.step.name}` : `Page ${run.pages + 1}: ${st.title || host}`, "step");
    const { filled, skipped } = await fillPage(tabId, c);
    let attached = false;
    if (st.needsResume && c.job?.has_resume && await attachResume(tabId, c.job.id)) {
      for (let k = 0; k < 40 && (await pageStatus(tabId))?.needsResume; k++) await pause(250); // upload finishing
      attached = true;
    }
    await say(run, `filled ${filled.length}${attached ? " and attached your tailored resume (open it on Review to check)" : ""}${skipped.length ? `; left ${skipped.length}: ${skipped.slice(0, 4).map((s) => s.slice(0, 50)).join("; ")}` : ""}`, "muted");
    const yours = skipped.filter((s) => YOURS.test(s));
    const now = { ...st, ...((await pageStatus(tabId)) || {}) }; // after filling, as the page is now
    if (now.review || (!now.next && ONE_PAGE_HOST.test(host))) {
      await say(run, "Reached the last page. Check every answer and the resume, then submit yourself.", "ok");
      return finish(run, "review", where || "the form", skipped.length ? `Filled. Answer the ${skipped.length} left, then submit.` : "Check it and submit");
    }
    if (yours.length && !(trust && run.pages === 0)) return finish(run, "needs_you", where, `Answer these on the page: ${yours.slice(0, 3).join("; ")}`, yours);
    if (stopping.delete(tabId)) return finish(run, "stopped", where, "Stopped. Nothing past this step was touched.");
    if (!now.next) return finish(run, "needs_you", where || "the form", `${filled.length ? "Filled this page, but it" : "Nothing on this page could be filled, and it"} has no Next or Continue button Job Hunter can press. Finish it yourself.`);
    const next = await inTab(tabId, "next");
    if (!next.some((r) => r.moved)) {
      const errs = (await pageStatus(tabId))?.errors || [];
      // Workday rejecting the Work Experience dates it shows: delete those entries and fill them
      // fresh, once per step.
      const key = `${tab.url}|${st.step?.n}`;
      if (st.workday && !(run.redone ||= {})[key] && errs.some((e) => /\b(From|To)\b.*(required|Invalid Date)/i.test(e))) {
        run.redone[key] = true;
        const [r] = await inTab(tabId, "redoWork", null, true).catch(() => []);
        await say(run, `Workday didn't keep the Work Experience dates: filling those entries again (${r?.removed || 0} removed)`, "warn");
        moved();
        continue;
      }
      return finish(run, "needs_you", where, errs.length ? `The page didn't move on. It says: ${errs.join("; ")}` : "The page didn't move on.", errs);
    }
    run.pages++;
    trust = false;
    moved();
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
  if (typeof checkReady === "function") checkReady(); // its place is free for the next ready job
  if (state === "stopped") return;
  // Many applications fill at once, so each notification names its job first; clicking it brings
  // that application's tab to the front.
  const job = run.title || "An application";
  chrome.notifications.create(`jh-${run.tabId}`, {
    type: "basic", iconUrl: "icon128.png", requireInteraction: true, priority: 2,
    title: `${state === "review" ? "Ready to submit" : "Needs you"}: ${job}`.slice(0, 120),
    message: (state === "review" ? "At Review. Check every answer and the resume, then submit." : `${step ? `${step}: ` : ""}${note}`).slice(0, 300),
    contextMessage: "Job Hunter · click to open its tab",
    ...(state === "needs_you" ? { buttons: [{ title: "I've done it, continue" }] } : {}),
  });
  if (run.jobId) {
    const tab = await chrome.tabs.get(run.tabId).catch(() => ({}));
    await api(`/api/x/progress/${run.jobId}`, { method: "POST", body: { state, step, note: items.length ? items.slice(0, 3).join("; ") : note, url: tab.url || "" } }).catch(() => {});
  }
}
