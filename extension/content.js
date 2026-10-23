// Injected into an application page when you use the side panel. It reads the form's questions,
// fills the empty ones you have answers for, attaches files, and collects what you typed so you
// can save it. It never submits the form, and never touches fields that look sensitive.
// Besides plain inputs it handles Workday's widgets: button dropdowns, search-and-pick boxes,
// Yes/No questions labelled by their field wrapper, and numbered Work Experience / Websites sections.
// Which answer and option to use comes from matching.js (injected first). Every value is read back
// after filling: one the page didn't keep is reported as skipped, not filled.
(() => {
  const VERSION = 10;
  if (window.__jobHunterApi && window.__jobHunterApi.version >= VERSION) return;
  const M = window.__jobHunterMatch;
  if (!M) throw new Error("Job Hunter: matching.js must be injected before content.js");

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

  // Options already on the page before a list opens (Workday marks the chosen "United States of
  // America (+1)" in Country Phone Code as an option too) don't count as the list having opened.
  const optionEls = () => [...document.querySelectorAll('[role="option"], [data-automation-id="promptOption"]')]
    .filter((el) => visible(el) && !el.closest('[data-automation-id="selectedItemList"]'));

  async function waitForOptions(ms, before = new Set()) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const opts = optionEls().filter((el) => !before.has(el))
        .map((el) => ({ el, text: clean(el.innerText || el.getAttribute("aria-label") || "") }))
        .filter((o) => o.text);
      if (opts.length) return opts;
      await sleep(150);
    }
    return [];
  }

  // The option an answer means, or why there isn't one ("ambiguous" / "unmatched").
  function pickOption(options, answer, label) {
    const r = M.resolveOption(options.map((o) => o.text), answer, label);
    return r.status === "matched" ? { option: options[r.index] } : { reason: r.status };
  }

  // Workday button dropdown: open it, click the matching option.
  async function chooseFromButton(btn, answer, label) {
    const before = new Set(optionEls());
    press(btn);
    const r = pickOption(await waitForOptions(2000, before), answer, label);
    if (r.option) { press(r.option.el); await sleep(300); return { text: r.option.text }; }
    key(document.activeElement || btn, "Escape");
    return r;
  }

  // Workday search-and-pick box: type, search, click the matching result; leave no stray text.
  async function chooseFromPrompt(input, answer, label) {
    const before = new Set(optionEls());
    input.focus();
    setValue(input, answer);
    key(input, "Enter");
    await sleep(400);
    const r = pickOption(await waitForOptions(3000, before), answer, label);
    if (r.option) { press(r.option.el); await sleep(500); }
    if (!hasSelection(input)) {
      setValue(input, "");
      key(input, "Escape");
      return { reason: r.reason || "unmatched" };
    }
    return { text: r.option.text };
  }

  // The first of several candidate answers (a derived answer's synonyms) that resolves to exactly
  // one option; an ambiguous candidate is skipped, never settled by picking the first.
  function pickAny(options, values, label) {
    let reason = "unmatched";
    for (const v of values) {
      const r = pickOption(options, v, label);
      if (r.option) return { ...r, value: v };
      if (r.reason === "ambiguous") reason = "ambiguous";
    }
    return { reason };
  }

  async function chooseFromButtonAny(btn, values, label) {
    const before = new Set(optionEls());
    press(btn);
    const r = pickAny(await waitForOptions(2000, before), values, label);
    if (r.option) { press(r.option.el); await sleep(300); return { text: r.option.text }; }
    key(document.activeElement || btn, "Escape");
    return r;
  }

  // Workday pick lists can be two levels deep (Comcast: "Job Sites" > "Indeed", "Other Job Site").
  // A category has an arrow and no selection circle; Workday reacts to clicks on the inner
  // promptLeafNode, and the list header's backButton steps back out. Only real options are chosen.
  const leafNode = (el) => el.querySelector('[data-automation-id="promptLeafNode"]') || el;
  const isCategoryItem = (el) => !el.querySelector('[data-automation-id="radioBtn"], [data-automation-id="checkboxPanel"], input')
    && !!el.querySelector("svg");
  // An option's inner element is also marked as an option: judge it by its list item.
  const isCategory = (el) => isCategoryItem(el.parentElement?.closest('[role="option"]') || el);
  const listBack = () => [...document.querySelectorAll('[data-automation-id="multiSelectHeader"] [data-automation-id="backButton"]')]
    .find(visible);

  // Open a pick list and wait for it to load (Workday shows a spinner first, sometimes for seconds).
  async function openList(input) {
    const before = new Set(optionEls());
    press(input);
    input.focus();
    return waitForOptions(6000, before);
  }
  const catEl = (text) => optionEls().find((el) => clean(el.innerText) === text && isCategory(el));
  const leafEl = (text) => optionEls().find((el) => clean(el.innerText) === text && !isCategory(el));

  // Read the whole list first: top-level options and the options inside each category. Then take
  // the best candidate across all of them (so "Other" in one category can't beat "Other Job Site"
  // in another), and go back and choose it. {text} | {reason, saw: number of options seen}.
  async function browsePrompt(input, values, label) {
    const top = await openList(input);
    if (!top.length) { key(document.activeElement || input, "Escape"); return { reason: "unmatched", saw: 0 }; }
    const all = top.filter((o) => !isCategory(o.el)).map((o) => ({ text: o.text, path: [] }));
    const cats = [...new Set(top.filter((o) => isCategory(o.el)).map((o) => o.text))].slice(0, 12);
    for (const cat of cats) {
      const el = catEl(cat);
      if (!el) continue;
      const shown = new Set(optionEls());
      press(leafNode(el));
      const inside = await waitForOptions(3000, shown);
      all.push(...inside.filter((o) => !isCategory(o.el)).map((o) => ({ text: o.text, path: [cat] })));
      const b = listBack();
      if (!b) break;
      press(b);
      // wait until the top level is back (categories visible again) before opening the next one
      const end = Date.now() + 4000;
      while (Date.now() < end && !optionEls().some((e) => isCategoryItem(e))) await sleep(150);
      await sleep(250);
    }
    // Workday marks both an option and its inner element as options: one entry per path + text.
    const unique = [...new Map(all.map((o) => [[...o.path, o.text].join(" > "), o])).values()];
    const r = pickAny(unique.map((o) => ({ ...o, el: null })), values, label);
    if (!r.option) { key(document.activeElement || input, "Escape"); await sleep(200); return { reason: r.reason, saw: all.length }; }
    for (const cat of r.option.path) {  // walk to it, then choose it
      const el = catEl(cat);
      if (!el) break;
      const shown = new Set(optionEls());
      press(leafNode(el));
      await waitForOptions(3000, shown);
    }
    const target = leafEl(r.option.text);
    if (target) { press(leafNode(target)); await sleep(600); }
    if (hasSelection(input)) return { text: [...r.option.path, r.option.text].join(" > "), value: r.value };
    key(document.activeElement || input, "Escape");
    return { reason: "dropped", saw: all.length };
  }

  // A search-and-pick box with candidate answers: browse the list (and its categories); only a list
  // that showed nothing is searched by typing a few short words ("career", "website").
  async function chooseFromPromptAny(input, values, label) {
    const browsed = await browsePrompt(input, values, label);
    if (browsed.text) return browsed;
    if (browsed.saw) return { reason: browsed.reason };
    const words = [...new Set(values.flatMap((v) => M.norm(v).split(" ")).filter((w) => w.length >= 4))].slice(0, 4);
    let reason = browsed.reason || "unmatched";
    for (const word of words) {
      const before = new Set(optionEls());
      input.focus();
      setValue(input, word);
      key(input, "Enter");
      await sleep(400);
      const r = pickAny(await waitForOptions(2500, before), values, label);
      if (r.option) {
        press(leafNode(r.option.el));
        await sleep(500);
        if (hasSelection(input)) return { text: r.option.text };
      } else if (r.reason === "ambiguous") reason = "ambiguous";
      setValue(input, "");
      key(input, "Escape");
      await sleep(200);
    }
    return { reason };
  }

  // Why a question was left for you, shown in the side panel.
  function why(label, status, value) {
    if (status === "conflict") return `${label} (your saved answers disagree)`;
    if (status === "yours") return `${label} (answer this one yourself)`;
    if (status === "ambiguous") return `${label} (more than one option fits "${value}")`;
    if (status === "unmatched") return `${label} (no option like "${value}")`;
    if (status === "dropped") return `${label} (the page didn't keep "${value}")`;
    return label;
  }
  const sameText = (a, b) => clean(a).toLowerCase() === clean(b).toLowerCase();
  // A <select> still on "Select One" is empty even when that option has no value="" of its own.
  const hasValue = (el) => el.tagName === "SELECT"
    ? el.selectedIndex >= 0 && !!el.value.trim() && !PLACEHOLDER.test(clean(el.options[el.selectedIndex].text))
    : !!(el.value || "").trim();

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

  // Repeated sections (Workday "Work Experience", "Education", "Websites") start empty behind an
  // "Add" button. Add as many entries as your record has; "Add" only creates an empty entry.
  // Never more than the record holds, never removes one, and only buttons labelled exactly "Add".
  async function addSections(ctx) {
    const links = (ctx.fields || []).filter((f) => ["linkedin", "github", "website"].includes(f.key) && f.value);
    const want = { "work experience": (ctx.history?.jobs || []).length,
                   education: (ctx.fields || []).some((f) => f.key === "school") ? 1 : 0,
                   websites: links.length };
    const added = [];
    for (const g of document.querySelectorAll("[role=group][aria-labelledby]")) {
      const title = clean(textOf(g.getAttribute("aria-labelledby"))).toLowerCase();
      if (!want[title]) continue;
      const entries = () => [...g.querySelectorAll("[role=group][aria-labelledby]")]
        .filter((s) => /\s\d+$/.test(clean(textOf(s.getAttribute("aria-labelledby"))))).length;
      for (let guard = 0; entries() < want[title] && guard < 6; guard++) {
        const btn = [...g.querySelectorAll("button")].filter(visible).find((b) => /^add( another)?$/i.test(clean(b.innerText)));
        if (!btn) break;
        const before = entries();
        btn.click();
        for (let i = 0; i < 20 && entries() === before; i++) await sleep(150);
        if (entries() === before) break;
        added.push(title);
      }
    }
    if (added.length) await sleep(600);
    return added;
  }

  async function fill(ctx) {
    await addSections(ctx);
    const sensitive = new RegExp(ctx.sensitive, "i");
    const done = [], skipped = [];
    const doneRadio = new Set();
    // `kept` reads the field back; it runs once the page has had a moment to re-render.
    const note = (label, value, from, kept, el) => done.push({ label, value: String(value), from, kept, el });
    const answer = (label) => (sensitive.test(label) ? { status: "none" } : M.answerFor(label, ctx));
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
            if (v === "check" && !el.checked) { el.click(); note(where, "checked", "master resume", () => el.checked); }
          } else if (!(el.value || "").trim()) {
            setValue(el, v);
            note(where, v, "master resume", () => el.isConnected && sameText(el.value, v));
          }
          continue;
        }
      }

      if (type === "radio") {
        if (doneRadio.has(el.name)) continue;
        doneRadio.add(el.name);
        const group = el.name ? [...document.getElementsByName(el.name)] : [el];
        if (group.some((r) => r.checked)) continue;
        const ans = answer(label);
        const d = ans.status === "none" ? M.derived(label, ctx) : null; // "worked here before?" etc.
        if (ans.status !== "matched" && !d) { if (label) skipped.push(why(label, ans.status)); continue; }
        const opts = group.map((g) => ({ el: g, text: optionText(g) }));
        const r = d ? pickAny(opts, d.values, label) : pickOption(opts, ans.value, label);
        if (!r.option) { skipped.push(why(label, r.reason, d ? d.values[0] : ans.value)); continue; }
        r.option.el.click();
        note(label, r.option.text, d ? d.from : ans.from, () => r.option.el.checked);
        continue;
      }
      if (type === "checkbox") continue; // consent and preference boxes are yours to tick

      if (isListButton(el)) {
        if (!PLACEHOLDER.test(clean(el.innerText))) continue; // already chosen
        const ans = answer(label);
        const d = ans.status === "none" ? M.derived(label, ctx) : null;
        if (ans.status !== "matched" && !d) { if (label) skipped.push(why(label, ans.status)); continue; }
        const r = d ? await chooseFromButtonAny(el, d.values, label) : await chooseFromButton(el, ans.value, label);
        if (r.text) note(label, r.text, d ? d.from : ans.from, () => sameText(el.innerText, r.text));
        else skipped.push(why(label, r.reason, d ? d.values[0] : ans.value));
        continue;
      }
      if (hasSelection(el) || hasValue(el)) continue; // never overwrite what's there
      const ans = answer(label);
      const d = ans.status === "none" && isPrompt(el) ? M.derived(label, ctx) : null; // "how did you hear"
      if (d) {
        const r = await chooseFromPromptAny(el, d.values, label);
        if (r.text) note(label, r.text, d.from, () => selectedItems(el).some((li) => sameText(li.innerText, r.text.split(" > ").pop())));
        else skipped.push(r.reason === "ambiguous" ? why(label, r.reason, d.values[0]) : `${label} (pick it from the list)`);
        continue;
      }
      if (ans.status !== "matched") { if (label) skipped.push(why(label, ans.status)); continue; }
      if (isPrompt(el)) {
        // a saved answer first; if no option matches it ("career site" in a list without one), the
        // derived candidates (Comcast: Email > Comcast Job Alert)
        const alt = M.derived(label, ctx);
        const r = await chooseFromPromptAny(el, [ans.value, ...(alt ? alt.values : [])], label); // browses categories too
        const from = r.value && alt && r.value !== ans.value ? alt.from : ans.from;
        if (r.text) note(label, r.text, from, () => selectedItems(el).some((li) => sameText(li.innerText, r.text.split(" > ").pop())));
        else skipped.push(r.reason === "ambiguous" ? why(label, r.reason, ans.value) : `${label} (pick it from the list)`);
        continue;
      }
      if (el.tagName === "SELECT") {
        const r = chooseOption(el, ans.value, label);
        if (r.option) note(label, r.option.text, ans.from, () => el.selectedIndex === r.option.el.index, el);
        else skipped.push(why(label, r.reason, ans.value));
        continue;
      }
      setValue(el, ans.value);
      note(label, ans.value, ans.from, () => el.isConnected && sameText(el.value, ans.value), el);
    }

    await sleep(600); // let the page re-render; React and Workday sometimes reset what was typed
    const filled = [];
    for (const d of done) {
      if (!d.kept()) { skipped.push(why(d.label, "dropped", d.value.slice(0, 40))); continue; }
      if (d.el) d.el.style.outline = "2px solid #2f7d4f";
      filled.push({ label: d.label, value: d.value.replace(/\s+/g, " ").slice(0, 80), from: d.from });
    }
    return { filled, skipped };
  }

  function chooseOption(select, answer, label) {
    const r = pickOption([...select.options].map((o) => ({ el: o, text: o.text })), answer, label);
    if (!r.option) return r;
    select.value = r.option.el.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return r;
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
