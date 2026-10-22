// Injected into an application page when you use the side panel. It reads the form's questions,
// fills the empty ones you have answers for, attaches files, and collects what you typed so you
// can save it. It never submits the form, and never touches fields that look sensitive.
// Besides plain inputs it handles Workday's widgets: button dropdowns, search-and-pick boxes,
// Yes/No questions labelled by their field wrapper, and numbered Work Experience / Websites sections.
(() => {
  const VERSION = 2;
  if (window.__jobHunterApi && window.__jobHunterApi.version >= VERSION) return;

  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (s) => (s || "").replace(/\s+/g, " ").replace(/\*/g, "").trim().slice(0, 300);
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "password", "search"]);
  const PLACEHOLDER = /^(select one|select|choose|choose one|--|none selected)?$/i;

  function textOf(id) {
    const el = id && document.getElementById(id);
    return el ? el.innerText || el.textContent || "" : "";
  }

  // Workday wraps each question in data-automation-id="formField-<name>".
  const formField = (el) => el.closest('[data-automation-id^="formField"]');
  const fieldName = (el) => (formField(el)?.getAttribute("data-automation-id") || "").replace("formField-", "");

  // The numbered section an element sits in, e.g. {kind: "Work Experience", n: 2}.
  function section(el) {
    for (let g = el.closest("[role=group][aria-labelledby]"); g; g = g.parentElement?.closest("[role=group][aria-labelledby]")) {
      const m = textOf(g.getAttribute("aria-labelledby")).trim().match(/^(.*?)\s+(\d+)$/);
      if (m) return { kind: m[1], n: +m[2] };
    }
    return null;
  }

  // The question a form control belongs to.
  function labelOf(el) {
    const ff = formField(el);
    if (ff && el.type !== "radio" && el.type !== "checkbox") {
      const own = ff.querySelector("label, legend");
      if (own && clean(own.innerText)) return clean(own.innerText);
    }
    const parts = [];
    if (el.getAttribute("aria-labelledby")) parts.push(...el.getAttribute("aria-labelledby").split(/\s+/).map(textOf));
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) parts.push(lab.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(wrap.innerText);
    const field = el.closest(".field, .form-group, .application-question, li, td");
    if (field && !parts.join("").trim()) {
      const lab = field.querySelector("label, legend, .label");
      if (lab) parts.push(lab.innerText);
    }
    if (!parts.join("").trim() && el.placeholder) parts.push(el.placeholder);
    if (!parts.join("").trim() && el.name) parts.push(el.name.replace(/[_\-[\]]+/g, " "));
    return clean(parts.join(" "));
  }

  // The question of a radio group (its legend or Workday field label), not the option's own text.
  function radioQuestion(radio) {
    const legend = radio.closest("fieldset")?.querySelector("legend")?.innerText;
    if (legend) return clean(legend);
    const ff = formField(radio);
    const lab = ff && [...ff.querySelectorAll("label, legend")]
      .find((l) => !(l.htmlFor && document.getElementById(l.htmlFor)?.type === "radio"));
    if (lab) return clean(lab.innerText);
    const group = radio.closest("[role=radiogroup]");
    if (group?.getAttribute("aria-labelledby")) return clean(textOf(group.getAttribute("aria-labelledby")));
    return labelOf(radio);
  }
  const optionText = (radio) => clean((radio.id && document.querySelector(`label[for="${CSS.escape(radio.id)}"]`)?.innerText)
    || radio.closest("label")?.innerText || radio.value);

  const isListButton = (el) => el.tagName === "BUTTON" && el.getAttribute("aria-haspopup") === "listbox";
  function isPrompt(el) {
    if (el.tagName !== "INPUT") return false;
    return el.getAttribute("data-uxi-widget-type") === "selectinput" || el.getAttribute("role") === "combobox"
      || !!formField(el)?.querySelector('[data-automation-id="promptIcon"], [data-automation-id="selectedItemList"]');
  }
  const selectedItems = (el) => [...(formField(el)?.querySelectorAll(
    '[data-automation-id="selectedItemList"] li, [data-automation-id="selectedItem"]') || [])];
  const hasSelection = (el) => selectedItems(el).length > 0;

  function controls() {
    return [...document.querySelectorAll("input, textarea, select, button[aria-haspopup='listbox']")].filter(
      (el) => (isListButton(el) || !SKIP_TYPES.has((el.type || "").toLowerCase())) && !el.disabled && visible(el)
    );
  }

  // Questions on the page (radio groups once), for the assistant.
  function questions() {
    const seen = new Set();
    const out = [];
    for (const el of controls()) {
      const key = el.type === "radio" ? "radio:" + el.name : el;
      if (seen.has(key)) continue;
      seen.add(key);
      const label = el.type === "radio" ? radioQuestion(el) : labelOf(el);
      if (label) out.push(label);
    }
    return out;
  }

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value); // works with React-controlled inputs
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }
  const key = (el, k) => ["keydown", "keyup"].forEach((t) =>
    el.dispatchEvent(new KeyboardEvent(t, { key: k, code: k, keyCode: k === "Enter" ? 13 : 27, bubbles: true })));
  function press(el) {
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    }
  }

  function similarity(a, b) {
    const A = new Set(norm(a).split(" ").filter((w) => w.length > 2));
    const B = new Set(norm(b).split(" ").filter((w) => w.length > 2));
    if (!A.size || !B.size) return 0;
    let both = 0;
    for (const w of A) if (B.has(w)) both++;
    return both / Math.max(A.size, B.size);
  }

  // The option that best matches an answer ("YES" -> "Yes"); nothing if no option is close.
  function bestOption(options, answer) {
    const a = norm(answer);
    if (!a) return null;
    const by = (f) => options.find((o) => f(norm(o.text)));
    return by((t) => t === a) || by((t) => t.startsWith(a + " ")) ||
      (a.length > 3 ? by((t) => t.includes(a)) : null) ||
      options.map((o) => [o, similarity(o.text, answer)]).filter(([, s]) => s >= 0.5).sort((x, y) => y[1] - x[1])[0]?.[0] || null;
  }

  async function waitForOptions(ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const opts = [...document.querySelectorAll('[role="option"], [data-automation-id="promptOption"]')]
        .filter(visible).map((el) => ({ el, text: clean(el.innerText || el.getAttribute("aria-label") || "") }))
        .filter((o) => o.text);
      if (opts.length) return opts;
      await sleep(150);
    }
    return [];
  }

  // Workday button dropdown: open it, click the matching option.
  async function chooseFromButton(btn, answer) {
    press(btn);
    const opt = bestOption(await waitForOptions(2000), answer);
    if (opt) { press(opt.el); await sleep(300); return !PLACEHOLDER.test(clean(btn.innerText)); }
    key(document.activeElement || btn, "Escape");
    return false;
  }

  // Workday search-and-pick box: type, search, click the matching result; leave no stray text.
  async function chooseFromPrompt(input, answer) {
    input.focus();
    setValue(input, answer);
    key(input, "Enter");
    await sleep(400);
    const opt = bestOption(await waitForOptions(3000), answer);
    if (opt) { press(opt.el); await sleep(500); }
    if (!hasSelection(input)) {
      setValue(input, "");
      key(input, "Escape");
      return false;
    }
    return true;
  }

  // The answer for a question: a saved answer to (nearly) the same question, else a profile field.
  function answerFor(label, ctx, sensitive) {
    if (!label || sensitive.test(label)) return null;
    let best = null, bestScore = 0;
    for (const a of ctx.answers) {
      const s = norm(a.question) === norm(label) ? 1 : similarity(a.question, label);
      if (s > bestScore) { best = a.answer; bestScore = s; }
    }
    if (bestScore >= 0.75) return { value: best, from: "saved answer" };
    const l = norm(label);
    const words = new Set(l.split(" "));
    let field = null, fieldLen = 0;
    for (const f of ctx.fields) {
      // "Phone Extension" is not your phone; "Country Phone Code" is not your country.
      if ((f.exclude || []).some((x) => (x.includes(" ") ? l.includes(x) : words.has(x)))) continue;
      for (const kw of f.keywords) {
        if (l.includes(norm(kw)) && kw.length > fieldLen) { field = f; fieldLen = kw.length; }
      }
    }
    if (!field && /^(your )?name$/.test(l)) field = ctx.fields.find((f) => f.key === "full_name");
    return field ? { value: field.value, from: field.label } : null;
  }

  // Values for numbered sections, from your master resume and profile. undefined = use the normal rules.
  function sectionValue(sec, el, ctx) {
    const name = fieldName(el);
    const aid = el.getAttribute("data-automation-id") || "";
    const field = (k) => ctx.fields.find((f) => f.key === k)?.value;
    if (/work experience/i.test(sec.kind)) {
      const job = ctx.history?.jobs?.[sec.n - 1];
      if (!job) return null;
      if (name === "jobTitle") return job.title;
      if (name === "companyName") return job.company;
      if (name === "roleDescription") return job.description;
      if (name === "currentlyWorkHere") return job.current ? "check" : null;
      const date = name === "startDate" ? job.start : name === "endDate" ? job.end : "";
      if (date) {
        const [mm, yyyy] = date.split("/");
        return /Month/.test(aid) ? String(+mm) : /Year/.test(aid) ? yyyy : date;
      }
      return null;
    }
    if (/website/i.test(sec.kind) && name === "url") {
      return [field("linkedin"), field("github"), field("website")].filter(Boolean)[sec.n - 1] || null;
    }
    return sec.n > 1 ? null : undefined; // e.g. Education 2: yours to fill
  }

  async function fill(ctx) {
    const sensitive = new RegExp(ctx.sensitive, "i");
    const filled = [], skipped = [];
    const doneRadio = new Set();
    const note = (label, value, from) => filled.push({ label, value: String(value).replace(/\s+/g, " ").slice(0, 80), from });
    for (const el of controls()) {
      const type = (el.type || "").toLowerCase();
      if (type === "file") continue;
      const label = type === "radio" ? radioQuestion(el) : labelOf(el);
      if (sensitive.test(label)) continue;

      const sec = section(el);
      if (sec) {
        const v = sectionValue(sec, el, ctx);
        if (v === null) continue;
        if (v !== undefined) {
          const where = `${sec.kind} ${sec.n}: ${label}`;
          if (type === "checkbox") {
            if (v === "check" && !el.checked) { el.click(); note(where, "checked", "master resume"); }
          } else if (!(el.value || "").trim()) {
            setValue(el, v);
            note(where, v, "master resume");
          }
          continue;
        }
      }

      if (type === "radio") {
        if (doneRadio.has(el.name)) continue;
        doneRadio.add(el.name);
        const group = el.name ? [...document.getElementsByName(el.name)] : [el];
        if (group.some((r) => r.checked)) continue;
        const ans = answerFor(label, ctx, sensitive);
        if (!ans) { if (label) skipped.push(label); continue; }
        const target = bestOption(group.map((r) => ({ el: r, text: optionText(r) })), ans.value);
        if (target) { target.el.click(); note(label, ans.value, ans.from); }
        else skipped.push(`${label} (no option like "${ans.value}")`);
        continue;
      }
      if (type === "checkbox") continue; // consent and preference boxes are yours to tick

      if (isListButton(el)) {
        if (!PLACEHOLDER.test(clean(el.innerText))) continue; // already chosen
        const ans = answerFor(label, ctx, sensitive);
        if (!ans) { if (label) skipped.push(label); continue; }
        if (await chooseFromButton(el, ans.value)) note(label, ans.value, ans.from);
        else skipped.push(`${label} (no option like "${ans.value}")`);
        continue;
      }
      if (hasSelection(el) || (el.value || "").trim()) continue; // never overwrite what's there
      const ans = answerFor(label, ctx, sensitive);
      if (!ans) { if (label) skipped.push(label); continue; }
      if (isPrompt(el)) {
        if (await chooseFromPrompt(el, ans.value)) note(label, ans.value, ans.from);
        else skipped.push(`${label} (pick it from the list)`);
        continue;
      }
      const ok = el.tagName === "SELECT" ? chooseOption(el, ans.value) : (setValue(el, ans.value), true);
      if (ok) { el.style.outline = "2px solid #2f7d4f"; note(label, ans.value, ans.from); }
      else skipped.push(label);
    }
    return { filled, skipped };
  }

  function chooseOption(select, answer) {
    const opt = bestOption([...select.options].map((o) => ({ el: o, text: o.text })), answer);
    if (!opt) return false;
    select.value = opt.el.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // Put a file (e.g. your tailored resume) into the page's resume / CV upload field.
  function attach(base64, filename, mime, which) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], filename, { type: mime });
    const inputs = [...document.querySelectorAll('input[type="file"]')];
    const want = which === "cover" ? /cover/i : /resume|cv|curriculum/i;
    const target = inputs.find((i) => want.test(labelOf(i) + " " + (i.name || "") + " " + (i.id || ""))) ||
                   (which === "resume" ? inputs[0] : null);
    if (!target) return { attached: false, inputs: inputs.length };
    const dt = new DataTransfer();
    dt.items.add(file);
    target.files = dt.files;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    return { attached: true, inputs: inputs.length };
  }

  // What you answered on this page, so you can save it for next time. Sensitive questions and
  // numbered sections (Work Experience 2, Websites 1: they differ per entry) are left out.
  function collect(sensitivePattern) {
    const sensitive = new RegExp(sensitivePattern, "i");
    const out = [];
    const seen = new Set();
    for (const el of controls()) {
      const type = (el.type || "").toLowerCase();
      if (type === "file" || type === "checkbox" || section(el)) continue;
      let label, value = "";
      if (type === "radio") {
        if (seen.has(el.name) || !el.checked) continue;
        seen.add(el.name);
        label = radioQuestion(el);
        value = optionText(el);
      } else if (isListButton(el)) {
        label = labelOf(el);
        value = PLACEHOLDER.test(clean(el.innerText)) ? "" : clean(el.innerText);
      } else if (el.tagName === "SELECT") {
        label = labelOf(el);
        value = el.selectedIndex > 0 ? el.options[el.selectedIndex].text : "";
      } else if (isPrompt(el)) {
        const ff = formField(el);
        if (ff && seen.has(ff)) continue;
        if (ff) seen.add(ff);
        label = labelOf(el);
        value = selectedItems(el).map((li) => clean(li.innerText)).filter(Boolean).join(", ");
      } else {
        label = labelOf(el);
        value = (el.value || "").trim();
      }
      if (label && value && !sensitive.test(label) && value.length < 5000) out.push({ question: label, answer: value });
    }
    return out;
  }

  // Called by the side panel in every frame of the page (forms are often in an embedded frame).
  window.__jobHunterApi = {
    version: VERSION,
    questions: () => ({ questions: questions(), title: document.title }),
    fill: (ctx) => fill(ctx),
    attach: (a) => attach(a.base64, a.filename, a.mime, a.which),
    collect: (pattern) => ({ items: collect(pattern) }),
  };
})();
