// Injected into an application page when you use the side panel. It reads the form's questions,
// fills the empty ones you have answers for, attaches files, and collects what you typed so you
// can save it. It never submits the form, and never touches fields that look sensitive; the only
// password it types is your saved job-site login, into the sign-in / sign-up form of the site the
// application is on (account(), which checks the site; never a Google or LinkedIn sign-in).
// Besides plain inputs it handles Workday's widgets: button dropdowns, search-and-pick boxes,
// Yes/No questions labelled by their field wrapper, and numbered Work Experience / Websites sections.
// Which answer and option to use comes from matching.js (injected first). Every value is read back
// after filling: one the page didn't keep is reported as skipped, not filled.
(() => {
  const VERSION = 17;
  if (window.__jobHunterApi && window.__jobHunterApi.version >= VERSION) return;
  const M = window.__jobHunterMatch;
  if (!M) throw new Error("Job Hunter: matching.js must be injected before content.js");

  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  // Applications fill in background tabs, where Chrome stretches a chain of timers to one a minute
  // after five minutes. Ending each wait through a message (a new task) keeps the chain from
  // forming, so a wait stays about a second at most.
  const sleep = (ms) => new Promise((r) => setTimeout(() => {
    const c = new MessageChannel();
    c.port1.onmessage = () => { c.port1.close(); r(); };
    c.port2.postMessage(0);
  }, ms));
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
  // Workday's search boxes search only on a full Enter (keydown, keypress, keyup).
  function key(el, k) {
    const code = k === "Enter" ? 13 : 27;
    for (const t of k === "Enter" ? ["keydown", "keypress", "keyup"] : ["keydown", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(t, { key: k, code: k, keyCode: code, which: code,
                                              charCode: t === "keypress" ? code : 0, bubbles: true, cancelable: true }));
    }
  }
  // Text typed into a search box (no change/blur, which would close it).
  function typeSearch(el, text) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, text);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: text ? "insertText" : "deleteContentBackward", data: text || null }));
  }
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

  // A search-and-pick box with candidate answers: browse the list (and its categories); if no option
  // fits, search by typing. Long lists (Field of Study) render only their first few options, so
  // the first candidates are searched whole ("Applied Data Science"), then a few short words.
  async function chooseFromPromptAny(input, values, label) {
    const browsed = await browsePrompt(input, values, label);
    if (browsed.text || browsed.reason === "ambiguous") return browsed;
    const words = [...new Set([...values.slice(0, 4).map(M.norm),
      ...(browsed.saw ? [] : values.flatMap((v) => M.norm(v).split(" ")).filter((w) => w.length >= 4))])].slice(0, 8);
    let reason = browsed.reason || "unmatched";
    for (const word of words) {
      const before = new Set(optionEls());
      input.focus();
      typeSearch(input, word);
      key(input, "Enter");
      await sleep(400);
      const opts = await waitForOptions(2500, before);
      // A search with a single result is chosen by Workday itself: keep it only if it's a candidate.
      const auto = [...new Set(selectedItems(input).map((li) => clean(li.innerText)).filter(Boolean))];
      if (auto.length) {
        const ok = pickAny(auto.map((text) => ({ text })), values, label);
        if (ok.option) return { text: ok.option.text, value: ok.value };
        await unselect(input);
        continue;
      }
      const r = pickAny(opts, values, label);
      if (r.option) {
        press(leafNode(r.option.el));
        await sleep(500);
        if (hasSelection(input)) return { text: r.option.text, value: r.value };
      } else if (r.reason === "ambiguous") reason = "ambiguous";
      typeSearch(input, "");
      key(input, "Escape");
      await sleep(200);
    }
    return { reason };
  }

  // A skills picker (Workday "Type to Add Skills"): search each of your resume skills and keep only
  // an option that is that skill: "Python (Programming Language)", "Structured Query Language (SQL)",
  // never a lookalike ("Python IDLE", "U-SQL").
  const sameSkill = (option, name) => {
    const n = M.norm(name), m = option.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
    return !!n && [option, ...(m ? [m[1], m[2]] : [])].some((t) => M.norm(t) === n);
  };
  const chosenTexts = (input) => [...new Set(selectedItems(input).map((li) => clean(li.innerText)).filter(Boolean))];
  async function addSkills(input, names) {
    const added = [];
    for (const name of names.slice(0, 10)) {
      const before = chosenTexts(input);
      const shown = new Set(optionEls());
      input.focus();
      typeSearch(input, name);
      key(input, "Enter");
      await sleep(400);
      const opts = await waitForOptions(2500, shown);
      const fresh = chosenTexts(input).filter((t) => !before.includes(t));
      if (fresh.length) { // Workday chose the only result itself
        const keep = fresh.filter((t) => sameSkill(t, name));
        added.push(...keep);
        removeChosen(input, fresh.filter((t) => !keep.includes(t)));
      } else {
        const hit = opts.filter((o) => sameSkill(o.text, name));
        if (hit.length === 1) {
          press(leafNode(hit[0].el));
          await sleep(500);
          if (chosenTexts(input).length > before.length) added.push(hit[0].text);
        }
      }
      typeSearch(input, "");
      key(input, "Escape");
      await sleep(250);
    }
    return added;
  }
  function removeChosen(input, texts) {
    for (const li of formField(input)?.querySelectorAll('[data-automation-id="selectedItemList"] [data-automation-id="selectedItem"]') || []) {
      const x = li.querySelector('[data-automation-id="DELETE_charm"]');
      if (x && texts.includes(clean(li.innerText))) press(x);
    }
  }

  // Remove what a search box chose (Workday's "x" on the chosen item).
  async function unselect(input) {
    const ff = formField(input) || input.parentElement;
    for (const x of ff.querySelectorAll('[data-automation-id="selectedItemList"] [data-automation-id="DELETE_charm"]')) press(x);
    typeSearch(input, "");
    key(input, "Escape");
    await sleep(400);
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
  // Reading a value back: Workday re-renders a field after it's filled (the old element is gone,
  // the new one has the same id) and reformats some values ("+1-315-289-0183" -> "315-289-0183").
  const live = (el) => (el.isConnected ? el : (el.id && document.getElementById(el.id)) || el);
  const digits = (x) => String(x || "").replace(/\D/g, "");
  function kept(actual, wanted) {
    if (sameText(actual, wanted) || (M.norm(actual) && M.norm(actual) === M.norm(wanted))) return true;
    const a = digits(actual), w = digits(wanted);
    return w.length >= 7 && a.length >= 7 && (a.endsWith(w) || w.endsWith(a)) && !/[a-z]/i.test(String(wanted));
  }
  // A <select> still on "Select One" is empty even when that option has no value="" of its own,
  // and so is a phone box holding only the country code the site typed in ("+1", as on amazon.jobs).
  const hasValue = (el) => el.tagName === "SELECT"
    ? el.selectedIndex >= 0 && !!el.value.trim() && !PLACEHOLDER.test(clean(el.options[el.selectedIndex].text))
    : !!(el.value || "").trim() && !/^\+\d{1,3}$/.test(el.value.trim());

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
            if (v === "check" && !el.checked) { el.click(); note(where, "checked", "master resume", () => live(el).checked); }
          } else if (!(el.value || "").trim()) {
            setValue(el, v);
            note(where, v, "master resume", () => kept(live(el).value, v));
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
        if (r.text) note(label, r.text, d ? d.from : ans.from, () => kept(live(el).innerText, r.text));
        else skipped.push(why(label, r.reason, d ? d.values[0] : ans.value));
        continue;
      }
      if (hasSelection(el) || hasValue(el)) continue; // never overwrite what's there
      if (isPrompt(el) && /\bskills\b/i.test(label) && ctx.history?.skills?.length) {
        const added = await addSkills(el, ctx.history.skills);
        if (added.length) note(label, added.join(", "), "your resume skills", () => hasSelection(live(el)));
        continue;
      }
      const ans = answer(label);
      const d = ans.status === "none" && isPrompt(el) ? M.derived(label, ctx) : null; // "how did you hear"
      if (d) {
        const r = await chooseFromPromptAny(el, d.values, label);
        if (r.text) note(label, r.text, d.from, () => selectedItems(live(el)).some((li) => kept(li.innerText, r.text.split(" > ").pop())));
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
        if (r.text) note(label, r.text, from, () => selectedItems(live(el)).some((li) => kept(li.innerText, r.text.split(" > ").pop())));
        else skipped.push(r.reason === "ambiguous" ? why(label, r.reason, ans.value) : `${label} (pick it from the list)`);
        continue;
      }
      if (el.tagName === "SELECT") {
        const r = chooseOption(el, ans.value, label);
        if (r.option) note(label, r.option.text, ans.from, () => live(el).selectedIndex === r.option.el.index, el);
        else skipped.push(why(label, r.reason, ans.value));
        continue;
      }
      setValue(el, ans.value);
      note(label, ans.value, ans.from, () => kept(live(el).value, ans.value), el);
    }

    await sleep(600); // let the page re-render; React and Workday sometimes reset what was typed
    const filled = [];
    for (const d of done) {
      if (!d.kept()) { skipped.push(why(d.label, "dropped", d.value.slice(0, 40))); continue; }
      if (d.el) live(d.el).style.outline = "2px solid #2f7d4f";
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

  // Autopilot: the button between steps, up to Review. On Workday only the footer's next button;
  // elsewhere a button that says Next / Continue / Save and Continue. Anything that reads like
  // submit is never clicked.
  const NEXT_IDS = ["pageFooterNextButton", "bottom-navigation-next-button"];
  const NEXT_TEXT = /^(save and continue|next|continue)$/i;
  const GENERIC_NEXT = /^(next|continue|next step|proceed|save (and|&) (continue|next|proceed))$/i;
  const FINAL = /submit|apply|send|finish|confirm/i;
  // What a final button says; seeing one (and no Next) means the application is at Review.
  const SUBMIT_TEXT = /^(submit|submit (my |your |this )?application|send (my )?application|finish|complete( my)? application|apply)$/i;
  // Sign-in buttons that hand you to another company's account (Google, LinkedIn ...): never pressed.
  const SSO = /google|linkedin|apple|facebook|microsoft|github|indeed|with amazon|amazon account|sso|single sign/i;

  const BUTTONS = 'button, a, [role=button], input[type="submit"], input[type="button"]';
  const say = (b) => clean(b.getAttribute("aria-label") || b.innerText || b.value || "");
  // Matched on what the button shows or its aria-label ("Apply now" / "Apply now for Data Scientist ...").
  const buttons = (re, root = document) => [...root.querySelectorAll(BUTTONS)]
    .filter((b) => visible(b) && !b.disabled && b.getAttribute("aria-disabled") !== "true")
    .filter((b) => { const t = [clean(b.innerText || b.value || ""), say(b)]; return t.some((x) => re.test(x)) && !t.some((x) => SSO.test(x)); })
    .filter((b) => !b.closest('[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [id*="onetrust" i]'));

  // {n: 2, of: 5, name: "My Experience"} from Workday's progress bar, or null on other sites.
  function step() {
    const active = document.querySelector('[data-automation-id="progressBarActiveStep"]');
    const m = active && clean(active.innerText).match(/step (\d+) of (\d+)\s*(.*)$/i);
    return m ? { n: +m[1], of: +m[2], name: m[3].trim() } : null;
  }
  const atReview = (s) => !!s && (/^review$/i.test(s.name) || s.n === s.of);
  function nextButton() {
    if (step()) {
      const b = NEXT_IDS.map((id) => document.querySelector(`[data-automation-id="${id}"]`)).find((x) => x && visible(x));
      const text = b ? clean(b.innerText) : "";
      return b && !b.disabled && NEXT_TEXT.test(text) && !FINAL.test(text) ? b : null;
    }
    if (loginForm()) return null;
    return buttons(GENERIC_NEXT).filter((b) => !FINAL.test(say(b))).pop() || null; // the last one: the form's footer
  }
  // Another site's application at its last page: a Submit button and no Next.
  const finalPage = () => !step() && !loginForm() && controls().length > 0 && !nextButton() && buttons(SUBMIT_TEXT).length > 0;
  const pageErrors = () => [...document.querySelectorAll('[data-automation-id*="error" i], [role=alert], [aria-live=assertive]')]
    .filter(visible).map((e) => clean(e.innerText)).filter(Boolean).slice(0, 6);
  // A resume field with nothing in it yet (Workday lists uploaded files as file-upload-item).
  const needsResume = () => !!document.querySelector('input[type="file"]') &&
    !document.querySelector('[data-automation-id="file-upload-item"]');

  const byId = (id) => [...document.querySelectorAll(`[data-automation-id="${id}"]`)].find(visible);
  const CLICKABLE = '[data-automation-id="click_filter"], ' + BUTTONS;
  // The nearest control around `el` whose label matches, e.g. the Sign In button of this form
  // (not the Sign In link in the page header). Never a Google / LinkedIn sign-in.
  function near(el, re) {
    for (let box = el?.parentElement; box; box = box.parentElement) {
      const hit = [...box.querySelectorAll(CLICKABLE)].filter(visible)
        .find((b) => { const t = say(b); return re.test(t) && !SSO.test(t); });
      if (hit) return hit;
    }
    return null;
  }
  // Workday lays a transparent click_filter over its buttons; that's what takes the click.
  const pressButton = (b) => press(b.parentElement?.querySelector(':scope > [data-automation-id="click_filter"]') || b);

  const LOGIN_PAGE = /\b(log ?in|sign ?in|sign ?up|create (an |your )?account|register|verify|verification)\b/i;
  const CREATE_TEXT = /^(create( an| my| your)? account|sign up|register|join( now)?)$/i;
  const SIGNIN_TEXT = /^(sign in|log ?in|continue|next|submit)$/i;
  const fieldText = (i) => `${labelOf(i)} ${i.name} ${i.id} ${i.autocomplete || ""} ${i.placeholder || ""}`;
  // A sign-in or sign-up form: {kind, email, password, verify, code}. kind is "signin", "create",
  // "email" (the email-first page of a two-page sign-in, as on amazon.jobs) or "code" (a code the
  // site emailed you). Never inside an application step.
  function loginForm() {
    if (step()) return null;
    const shown = (sel) => [...document.querySelectorAll(sel)].filter(visible);
    const pw = shown('input[type="password"]');
    const texts = shown('input[type="email"], input[type="text"], input[type="tel"], input:not([type])');
    const email = byId("email") || texts.find((i) => i.type === "email" || /e-?mail|user ?name|login/i.test(fieldText(i)));
    const password = byId("password") || pw[0];
    if (password) {
      const verify = byId("verifyPassword") || pw[1] || null;
      const create = !!verify || (!!near(password, CREATE_TEXT) && !near(password, /^(sign in|log ?in)$/i));
      return { kind: create ? "create" : "signin", email, password, verify };
    }
    const few = controls().length <= 3, words = LOGIN_PAGE.test((document.body?.innerText || "").slice(0, 3000));
    const code = texts.find((i) => i.autocomplete === "one-time-code" || /\b(code|otp|passcode|pin)\b/i.test(fieldText(i)));
    if (code && few && words) return { kind: "code", code };
    if (email && few && words) return { kind: "email", email };
    return null;
  }
  // The posting's own Apply button (before the application starts). On Workday its adventureButton;
  // elsewhere a button or link that says Apply, on a page that has no application form on it yet.
  // The page's own boxes, not a careers site's job search (keyword, location and its latitude/longitude).
  const fields = () => controls().filter((c) => !/search|keyword|location|where|what|latitude|longitude/i.test(fieldText(c)));
  function applyButton() {
    if (step()) return null;
    const wd = byId("adventureButton");
    if (wd) return wd;
    if (loginForm() || document.querySelector('input[type="file"]')) return null;
    if (fields().length >= 3) return null;
    return buttons(/^(apply|apply now|apply here|apply online|apply for this (job|position|role)|apply to this (job|position|role)|start (your |my )?application|begin (your )?application)$/i)[0] || null;
  }
  const CAPTCHA = 'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], [data-automation-id*="captcha" i]';
  // A taken-down posting (the app's liveness check uses the same wording), or Workday's missing-page message.
  const CLOSED = new RegExp([
    String.raw`\b(?:this|the)\s+(?:position|role|job|opening|posting|requisition)\s+(?:has\s+been|was|is)\s+(?:filled|closed|cancel+ed|removed|expired)`,
    String.raw`\bno\s+longer\s+(?:accepting|taking|available|open|being\s+accepted)`,
    String.raw`\b(?:position|role|job|posting)\s+(?:is\s+)?no\s+longer\s+(?:available|open|active)`,
    String.raw`\bapplications?\s+(?:are|is)\s+(?:now\s+)?closed\b`, String.raw`\bthis\s+job\s+(?:has\s+)?expired\b`,
    String.raw`\bpage\s+you\s+are\s+looking\s+for\s+(?:doesn't|does\s+not|no\s+longer)\s+exist`,
    // amazon.jobs: "Sorry, the job you're looking for isn't available."
    String.raw`\b(?:job|position|role|posting|opening|page)\s+(?:you're|you\s+are)\s+looking\s+for\s+(?:isn't|is\s+not|is\s+no\s+longer)\s+available`].join("|"), "i");

  function status() {
    const s = step();
    const login = loginForm();
    const text = s || login ? "" : (document.body?.innerText || "").slice(0, 4000);
    const posting = !!applyButton(), form = controls().length;
    // Only on a page with no application on it: no step, sign-in, Apply button or form (a job search box is fine).
    const closed = !s && !login && !posting && fields().length < 3 ? (text.replace(/’/g, "'").match(CLOSED) || [""])[0] : "";
    return { workday: !!s, step: s, review: atReview(s) || (!posting && finalPage()), next: !!nextButton(),
             needsResume: needsResume(), signIn: !!login, account: login ? login.kind : null,
             posting, closed, captcha: [...document.querySelectorAll(CAPTCHA)].some(visible),
             verifyEmail: /verify your (email|account)|verification (email|link)|activate your account|confirm your email/i.test(text),
             form, fields: fields().length, errors: pageErrors(), title: clean(document.querySelector("h1, h2")?.innerText || document.title).slice(0, 80) };
  }

  // On the posting: press Apply, then (Workday) "Apply Manually": Job Hunter fills the form itself,
  // so Workday's own resume parsing isn't used. Nothing here is a submit button.
  async function start() {
    const apply = applyButton();
    if (!apply || !/apply|application/i.test(clean(apply.innerText) + " " + say(apply))) return { started: false, reason: "no Apply button" };
    // A link that would open a new tab is followed in this one, where the run carries on.
    if (apply.tagName === "A" && apply.target === "_blank" && /^https?:/.test(apply.href)) {
      location.assign(apply.href);
      return { started: true };
    }
    pressButton(apply);
    for (let i = 0; i < 40; i++) {
      await sleep(250);
      const manual = byId("applyManually");
      if (manual) {
        pressButton(manual);
        for (let k = 0; k < 40 && !step() && !loginForm(); k++) await sleep(250); // the next screen drawing
        return { started: true };
      }
      if (step() || loginForm() || !applyButton()) return { started: true };
    }
    return { started: false, reason: "nothing opened after Apply" };
  }

  // Sign in to (mode "signin") or create (mode "create") this site's account with your saved
  // job-site login, switching the form between the two if needed; or type the email on an
  // email-first page (mode "email"), or the code the site emailed (mode "code"). `consent` ticks
  // the sign-up form's terms box. Only on a site the run allows (`allow`, a host pattern).
  // Returns {done} once the form is gone or has changed, else the page's errors.
  async function account({ email, password, mode, consent, code, allow }) {
    if (!allow || !new RegExp(allow, "i").test(location.hostname)) return { done: false, reason: `not typing your login on ${location.hostname}` };
    let f = loginForm();
    if (!f) return { done: false, reason: "no sign-in form" };
    const settle = async (was) => {
      for (let i = 0; i < 80; i++) {
        await sleep(250);
        const now = loginForm();
        if (!now || now.kind !== was) return { done: true };
        const errors = pageErrors();
        if (errors.length && i > 4) return { done: false, errors };
      }
      return { done: false, reason: "the form stayed" };
    };
    if (mode === "email" || mode === "code") {
      if (f.kind !== mode) return { done: false, reason: `this isn't the ${mode} page` };
      const field = mode === "email" ? f.email : f.code;
      setValue(field, mode === "email" ? email : code);
      const go = near(field, /^(continue|next|sign in|log ?in|submit|verify|confirm)$/i);
      if (!go) return { done: false, reason: "no Continue button on the form" };
      pressButton(go);
      return settle(mode);
    }
    const create = mode === "create";
    if ((f.kind === "create") !== create) {
      const link = create ? byId("createAccountLink") || near(f.password, /^(create (an |your )?account|sign up|register|new user.*)$/i)
                          : byId("signInLink") || near(f.password, /^(already have an account\?? ?)?(sign in|log ?in)$/i);
      if (!link) return { done: false, reason: `no ${create ? "Create Account" : "Sign In"} link` };
      pressButton(link);
      for (let i = 0; i < 40 && (loginForm()?.kind === "create") !== create; i++) await sleep(250);
      f = loginForm();
      if (!f || (f.kind === "create") !== create) return { done: false, reason: "the form didn't switch" };
    }
    if (f.email) setValue(f.email, email);
    else if (!create && !f.password) return { done: false, reason: "no email field" };
    setValue(f.password, password);
    if (create) {
      if (f.verify) setValue(f.verify, password);
      const box = byId("createAccountCheckbox") || [...document.querySelectorAll('input[type="checkbox"]')]
        .filter(visible).find((c) => /agree|consent|terms|privacy|acknowledge/i.test(labelOf(c)));
      if (box && !box.checked) {
        if (!consent) return { done: false, reason: "the Create Account form asks you to agree to its terms" };
        box.click();
      }
    }
    const submit = create ? byId("createAccountSubmitButton") || near(f.password, /^(create( an| my| your)? account|sign up|register|continue|next|submit)$/i)
                          : byId("signInSubmitButton") || near(f.password, SIGNIN_TEXT);
    if (!submit) return { done: false, reason: "no submit button on the form" };
    pressButton(submit);
    return settle(f.kind);
  }

  // Press the step's Next / Save and Continue once and wait for the next step. Re-checks the
  // button right before the click; reports the page's errors if it stayed put.
  async function next() {
    const s = step();
    if (s && atReview(s)) return { moved: false, reason: "review" };
    if (!s && finalPage()) return { moved: false, reason: "review" };
    const b = nextButton();
    if (!b) return { moved: false, reason: "no Next or Save and Continue button" };
    const page = () => `${location.href}|${status().title}|${controls().map((c) => c.name || c.id).join(",")}`;
    const before = page();
    b.click();
    for (let i = 0; i < 80; i++) {
      await sleep(250);
      if (s) {
        const now = step();
        if (now && now.n !== s.n) { await sleep(1500); return { moved: true, step: now }; } // let the step render
      } else if (page() !== before) {
        await sleep(1500);
        return { moved: true };
      }
      const errors = pageErrors();
      if (errors.length && i > 4) return { moved: false, errors };
    }
    return { moved: false, reason: "the page didn't move on", errors: pageErrors() };
  }

  // Called by the side panel in every frame of the page (forms are often in an embedded frame).
  window.__jobHunterApi = {
    status: () => status(),
    next: () => next(),
    start: () => start(),
    account: (a) => account(a),
    version: VERSION,
    questions: () => ({ questions: questions(), title: document.title }),
    fill: (ctx) => fill(ctx),
    attach: (a) => attach(a.base64, a.filename, a.mime, a.which),
    collect: (pattern) => ({ items: collect(pattern) }),
  };
})();
