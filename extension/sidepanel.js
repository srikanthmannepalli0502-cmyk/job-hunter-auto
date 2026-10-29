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
  tab = await activeTab();
  showRun();
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

async function attachFile(which) {
  if (!previewed.has(`${which}:${ctx.job.id}`)) {
    await preview(which);
    $("attachResult").innerHTML = `<span class="warn">Opened the ${which} in a new tab. Read it, then click Attach again to upload it.</span>`;
    return;
  }
  const file = await fileArg(which, ctx.job.id);
  const frames = await inPage("attach", file);
  const ok = frames.some((f) => f.attached);
  $("attachResult").innerHTML = ok
    ? `<span class="ok">Attached ${esc(file.filename)}.</span> Check the page shows it.`
    : `<span class="warn">No ${which} upload field found on this page (some sites only show it after you click "Upload").</span>`;
  return ok;
}

// Fill to Review runs in the background (autopilot.js), so it carries on with this panel closed.
// The panel starts and stops it and shows how far it got on this tab.
$("auto").addEventListener("click", () => chrome.runtime.sendMessage({ autopilot: "run", tabId: tab.id, jobId: ctx?.job?.id || null }));
$("stopAuto").addEventListener("click", () => chrome.runtime.sendMessage({ autopilot: "stop", tabId: tab.id }));
$("autoResult").addEventListener("click", (e) => {
  if (e.target.id === "continueAuto") chrome.runtime.sendMessage({ autopilot: "run", tabId: tab.id, trust: true });
});
const LINE = { ok: "ok", warn: "warn", needs: "warn", bad: "bad", muted: "muted" };
async function showRun() {
  if (!tab) return;
  const run = ((await chrome.storage.session.get("runs")).runs || {})[tab.id];
  const going = !!run && ["waiting", "running"].includes(run.state);
  $("auto").disabled = going;
  $("stopAuto").hidden = !going;
  $("autoResult").innerHTML = !run ? "" : run.lines.map((l) =>
    `<div>${l.kind === "step" ? `<b>${esc(l.text)}</b>` : `<span class="${LINE[l.kind] || ""}">${esc(l.text)}</span>`}</div>`).join("") +
    (run.state === "needs_you" ? `<button id="continueAuto" class="primary">I've filled it, continue</button>` : "");
}
chrome.storage.onChanged.addListener((changes, area) => { if (area === "session" && changes.runs) showRun(); });
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

// Auto-open's last check, so you can see why a shortlisted job hasn't opened.
const showAuto = (st) => { $("autoStatus").textContent = st ? `Auto-open (${new Date(st.at).toLocaleTimeString()}): ${st.text}` : ""; };
chrome.storage.local.get("autoStatus").then(({ autoStatus }) => showAuto(autoStatus));
chrome.storage.onChanged.addListener((c, area) => { if (area === "local" && c.autoStatus) showAuto(c.autoStatus.newValue); });

chrome.tabs.onActivated.addListener(refresh);
// Workday moves from the posting to the application without a page load: a new URL is enough.
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.status === "complete" || info.url) refresh(); });

refresh();
