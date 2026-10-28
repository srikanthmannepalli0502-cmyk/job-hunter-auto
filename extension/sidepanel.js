// Side panel: knows which job the current tab belongs to and runs the form helpers in the page.
const $ = (id) => document.getElementById(id);
let ctx = null; // context from the app: matched job, profile fields, saved answers
let tab = null;

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

// Run one of content.js's helpers in every frame of the page and return the frames' results.
async function inPage(name, arg) {
  await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["matching.js", "content.js"] });
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    func: (n, a) => (window.__jobHunterApi ? window.__jobHunterApi[n](a) : null),
    args: [name, arg ?? null],
  });
  return results.map((r) => r.result).filter(Boolean);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function refresh() {
  if (autoRunning) return; // keep acting on the application being filled
  tab = await activeTab();
  const { appUrl, key } = await settings();
  $("setup").hidden = !!(appUrl && key);
  if (!appUrl || !key) { $("jobInfo").textContent = "Not connected yet."; return; }
  try {
    ctx = await api(`/api/x/context?url=${encodeURIComponent(tab.url || "")}&title=${encodeURIComponent(tab.title || "")}`);
  } catch (e) {
    $("jobInfo").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
    return;
  }
  const j = ctx.job;
  $("jobInfo").innerHTML = j
    ? `<b>${esc(j.title)}</b><br>${esc(j.company)}<br><span class="muted">${j.decision}${j.has_resume ? " &middot; tailored resume ready" : " &middot; no tailored resume yet"}</span>`
    : `<span class="muted">This page isn't one of your saved jobs. You can still fill it and ask questions.</span>`;
  $("attachResume").disabled = $("previewResume").disabled = !(j && j.has_resume);
  $("attachCover").disabled = !(j && j.has_cover);
  $("applied").disabled = !j;
  $("revise").disabled = !(j && j.has_resume);
}

$("openOptions").addEventListener("click", (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); });
$("openApp").addEventListener("click", async (e) => {
  e.preventDefault();
  const { appUrl } = await settings();
  chrome.tabs.create({ url: ctx?.job ? `${appUrl}/job/${ctx.job.id}` : appUrl });
});

$("fill").addEventListener("click", async () => {
  if (!ctx) return;
  $("fillResult").textContent = "Filling...";
  try {
    const frames = await inPage("fill", ctx);
    const filled = frames.flatMap((f) => f.filled || []);
    const skipped = frames.flatMap((f) => f.skipped || []);
    $("fillResult").innerHTML =
      `<span class="ok">Filled ${filled.length} field(s).</span> Check them before submitting.` +
      (filled.length ? `<ul>${filled.map((f) => `<li>${esc(f.label.slice(0, 60))}: <b>${esc(f.value.slice(0, 40))}</b> <span class="muted">(${esc(f.from)})</span></li>`).join("")}</ul>` : "") +
      (skipped.length ? `<div class="muted">No answer yet for ${skipped.length}: ${esc(skipped.slice(0, 6).map((s) => s.slice(0, 40)).join("; "))}${skipped.length > 6 ? "..." : ""}. Fill them yourself, then "Save my answers".</div>` : "");
  } catch (e) {
    $("fillResult").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
});

const previewed = new Set(); // "resume:<job id>": shown to you before it can be attached

async function preview(which) {
  const resp = await api(`/api/x/${which}/${ctx.job.id}`, { raw: true });
  const url = URL.createObjectURL(await resp.blob());
  chrome.tabs.create({ url });
  previewed.add(`${which}:${ctx.job.id}`);
}

async function attachFile(which, auto = false) {
  if (!auto && !previewed.has(`${which}:${ctx.job.id}`)) {
    await preview(which);
    $("attachResult").innerHTML = `<span class="warn">Opened the ${which} in a new tab. Read it, then click Attach again to upload it.</span>`;
    return;
  }
  const resp = await api(`/api/x/${which}/${ctx.job.id}`, { raw: true });
  const buf = new Uint8Array(await resp.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  const filename = resp.headers.get("X-Filename") || `${which}.pdf`;
  const mime = resp.headers.get("Content-Type") || "application/pdf";
  const frames = await inPage("attach", { base64: btoa(bin), filename, mime, which });
  const ok = frames.some((f) => f.attached);
  $("attachResult").innerHTML = ok
    ? `<span class="ok">Attached ${esc(filename)}.</span> Check the page shows it.`
    : `<span class="warn">No ${which} upload field found on this page (some sites only show it after you click "Upload").</span>`;
  return ok;
}

// Fill to Review (Workday): fill the step, attach the resume where asked, press Save and Continue,
// and repeat. Stops at Review, at a question that's yours, at any error the page shows, or after 8
// steps, and tells you (a desktop notification and the app's "Waiting on you" list). After you
// answer what it stopped on, "I've filled it, continue" goes on from that step.
let stopAuto = false;
let autoRunning = false; // the panel stays on the application's tab while this runs
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const workdayStatus = async () => (await inPage("status")).find((s) => s.workday);
const SIGN_IN = /sign in|sign-in|create account|log ?in|register/i;
$("stopAuto").addEventListener("click", () => { stopAuto = true; });
$("auto").addEventListener("click", () => fillToReview(false));
$("autoResult").addEventListener("click", (e) => { if (e.target.id === "continueAuto") fillToReview(true); });

// A desktop notification (click it to come back to this tab) and the job's state in the app.
async function tellYou(state, step, note) {
  const job = ctx?.job;
  const title = state === "review" ? "Ready for you to submit" : "Job Hunter needs you";
  const where = job ? `${job.title} at ${job.company}` : tab.title || "this application";
  try {
    chrome.notifications.create(`jh-${tab.id}`, {
      type: "basic", iconUrl: "icon128.png", title, requireInteraction: true, priority: 2,
      message: state === "review" ? `${where} is at Review. Check it and submit.` : `${where}, ${step}: ${note}`.slice(0, 300),
    });
  } catch { /* notifications are a convenience */ }
  if (job) {
    try { await api(`/api/x/progress/${job.id}`, { method: "POST", body: { state, step, note, url: tab.url } }); } catch { /* app offline */ }
  }
}

async function fillToReview(trustThisStep) {
  if (!ctx || autoRunning) return;
  stopAuto = false;
  autoRunning = true;
  $("auto").disabled = true;
  $("stopAuto").hidden = false;
  const lines = [];
  const say = (html) => { lines.push(html); $("autoResult").innerHTML = lines.map((l) => `<div>${l}</div>`).join(""); };
  const stopped = `<span class="warn">Stopped. Nothing past this step was touched.</span>`;
  const again = `<button id="continueAuto" class="primary">I've filled it, continue</button>`;
  const needsYou = async (step, items, note) => {
    say(`<span class="warn">Stopped at ${esc(step)}. ${esc(note)}</span>` +
        (items.length ? `<ul>${items.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : "") + again);
    await tellYou("needs_you", step, items.length ? items.slice(0, 3).join("; ") : note);
  };
  try {
    for (let page = 0; ; page++) {
      if (stopAuto) return say(stopped);
      const st = await workdayStatus();
      if (!st) return say(`<span class="muted">Fill to Review works on Workday applications. Here, use Fill this page and submit yourself.</span>`);
      if (st.review) {
        say(`<span class="ok"><b>Reached Review.</b></span> Check every answer and the resume; change anything you want, then submit yourself.`);
        return await tellYou("review", st.step.name, "Check it and submit");
      }
      if (page >= 8) return await needsYou(st.step.name, [], "It went through 8 steps without reaching Review.");
      if (SIGN_IN.test(st.step.name) || st.signIn) {
        return await needsYou(st.step.name, [], "Sign in or create the account yourself; Job Hunter never does. Then continue.");
      }
      const step = `step ${st.step.n} of ${st.step.of} (${st.step.name})`;
      say(`<b>Step ${st.step.n} of ${st.step.of}: ${esc(st.step.name)}</b>`);
      const frames = await inPage("fill", ctx);
      const filled = frames.flatMap((f) => f.filled || []);
      const skipped = frames.flatMap((f) => f.skipped || []);
      say(`<span class="muted">filled ${filled.length}${skipped.length ? `; left ${skipped.length}: ${esc(skipped.slice(0, 4).map((s) => s.slice(0, 50)).join("; "))}` : ""}</span>`);
      if (st.needsResume && ctx.job?.has_resume && await attachFile("resume", true)) {
        for (let i = 0; i < 40 && (await workdayStatus())?.needsResume; i++) await sleep(250); // upload finishing
        say(`<span class="muted">attached your tailored resume (open it on Review to check)</span>`);
      }
      // Questions only you can answer stop here, unless you just said this step is done.
      const yours = skipped.filter((s) => /answer this one yourself|saved answers disagree|more than one option fits/.test(s));
      if (yours.length && !(trustThisStep && page === 0)) return await needsYou(step, yours, "Answer these on the page.");
      if (stopAuto) return say(stopped);
      if (!(await inPage("next")).some((r) => r.moved)) {
        const errs = (await workdayStatus())?.errors || [];
        return await needsYou(step, errs, errs.length ? "The page didn't move on. It says:" : "The page didn't move on.");
      }
    }
  } catch (e) {
    say(`<span class="bad">${esc(e.message)}</span>`);
  } finally {
    autoRunning = false;
    $("auto").disabled = false;
    $("stopAuto").hidden = true;
  }
}
$("previewResume").addEventListener("click", () => preview("resume").catch((e) => ($("attachResult").textContent = e.message)));
$("attachResume").addEventListener("click", () => attachFile("resume").catch((e) => ($("attachResult").textContent = e.message)));
$("attachCover").addEventListener("click", () => attachFile("cover").catch((e) => ($("attachResult").textContent = e.message)));

$("collect").addEventListener("click", async () => {
  if (!ctx) return;
  const frames = await inPage("collect", ctx.sensitive);
  const known = new Set(ctx.answers.map((a) => a.question.toLowerCase()));
  const fieldValues = new Set(ctx.fields.map((f) => f.value.toLowerCase()));
  // Offer only answers that aren't already saved and aren't just your profile basics.
  const items = frames.flatMap((f) => f.items || []).filter((i) => !known.has(i.question.toLowerCase()) && !fieldValues.has(i.answer.toLowerCase()));
  if (!items.length) { $("collectResult").innerHTML = `<span class="muted">Nothing new to save on this page.</span>`; return; }
  $("collectResult").innerHTML =
    `<div>Save these for next time?</div><ul>${items.map((i, n) => `<li><label><input type="checkbox" data-n="${n}" checked> ${esc(i.question.slice(0, 80))}: <b>${esc(i.answer.slice(0, 60))}</b></label></li>`).join("")}</ul>` +
    `<button id="saveChosen" class="primary">Save selected</button>`;
  $("saveChosen").addEventListener("click", async () => {
    const chosen = [...document.querySelectorAll("#collectResult input:checked")].map((c) => items[+c.dataset.n]);
    const r = await api("/api/x/answers", { method: "POST", body: { items: chosen, job_id: ctx?.job?.id || null } });
    $("collectResult").innerHTML = `<span class="ok">Saved ${r.saved} answer(s).</span>`;
    await refresh();
  });
});

$("ask").addEventListener("click", async () => {
  const question = $("question").value.trim();
  if (!question) return;
  $("answer").innerHTML = `<div class="muted">Thinking...</div>`;
  try {
    let pageQuestions = [];
    try { pageQuestions = (await inPage("questions")).flatMap((f) => f.questions || []); } catch { /* page may block scripts */ }
    const r = await api("/api/x/assist", { method: "POST", body: { job_id: ctx?.job?.id || null, question, page_questions: pageQuestions } });
    $("answer").innerHTML = `<div class="answer">${esc(r.answer)}</div><button id="copy">Copy</button>`;
    $("copy").addEventListener("click", () => navigator.clipboard.writeText(r.answer));
  } catch (e) {
    $("answer").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
  }
});

// Ask for a rebuilt resume, then watch for the new version (it takes a few minutes).
let watching = null;
$("revise").addEventListener("click", async () => {
  const instructions = $("revision").value.trim();
  if (!ctx?.job || !instructions) return;
  const jobId = ctx.job.id, before = ctx.job.resume_version;
  await api(`/api/x/revise/${jobId}`, { method: "POST", body: { instructions } });
  $("reviseResult").innerHTML = `<span class="muted">Rebuilding with your changes (fact-checked as always). This takes a few minutes; you can keep filling the form.</span>`;
  clearInterval(watching);
  watching = setInterval(async () => {
    try {
      const now = await api(`/api/x/context?url=${encodeURIComponent(tab.url || "")}&title=${encodeURIComponent(tab.title || "")}`);
      const j = now.job;
      if (!j || j.id !== jobId) return;
      if (j.status.startsWith("failed")) {
        clearInterval(watching);
        $("reviseResult").innerHTML = `<span class="bad">${esc(j.status)}</span>`;
      } else if (j.resume_version && j.resume_version !== before) {
        clearInterval(watching);
        ctx = now;
        previewed.delete(`resume:${jobId}`);
        $("revision").value = "";
        $("reviseResult").innerHTML = `<span class="ok">New version ready.</span> ${esc((j.notes || [])[0] || "")}<br>Preview it, then Attach.`;
      }
    } catch { /* keep waiting */ }
  }, 15000);
});

$("applied").addEventListener("click", async () => {
  if (!ctx?.job) return;
  const r = await api(`/api/x/applied/${ctx.job.id}`, { method: "POST", body: {} });
  $("appliedResult").innerHTML = `<span class="ok">Recorded application #${r.application_id}${r.resume ? " with your tailored resume" : ""}.</span>`;
});

chrome.tabs.onActivated.addListener(refresh);
// Workday moves from the posting to the application without a page load: a new URL is enough.
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.status === "complete" || info.url) refresh(); });

// Applications opened after you shortlisted (background.js marks them): start Fill to Review as
// soon as this tab shows the application form, once. Marks older than 3 hours are dropped.
setInterval(async () => {
  if (autoRunning || !ctx?.job) return;
  const { autostart = {} } = await chrome.storage.session.get("autostart");
  const since = autostart[ctx.job.id];
  if (!since) return;
  if (Date.now() - since > 3 * 3600 * 1000) {
    delete autostart[ctx.job.id];
    return chrome.storage.session.set({ autostart });
  }
  let st = null;
  try { st = await workdayStatus(); } catch { return; } // e.g. a sign-in page that blocks scripts
  if (!st || st.review || st.signIn || SIGN_IN.test(st.step.name)) return; // waits while you sign in
  delete autostart[ctx.job.id];
  await chrome.storage.session.set({ autostart });
  fillToReview(false);
}, 3000);
refresh();
