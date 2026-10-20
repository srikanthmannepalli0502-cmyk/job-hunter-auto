// Injected into an application page when you use the side panel. It reads the form's questions,
// fills the empty ones you have answers for, attaches files, and collects what you typed so you
// can save it. It never submits the form, and never touches fields that look sensitive.
(() => {
  if (window.__jobHunter) return;
  window.__jobHunter = true;

  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "password", "search"]);

  function textOf(id) {
    const el = id && document.getElementById(id);
    return el ? el.innerText || el.textContent || "" : "";
  }

  // The question a form control belongs to, from the ways forms label their fields.
  function labelOf(el) {
    const parts = [];
    if (el.getAttribute("aria-labelledby")) parts.push(...el.getAttribute("aria-labelledby").split(/\s+/).map(textOf));
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) parts.push(lab.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(wrap.innerText);
    if ((el.type === "radio" || el.type === "checkbox") && el.closest("fieldset")) {
      const legend = el.closest("fieldset").querySelector("legend");
      if (legend) parts.unshift(legend.innerText);
    }
    // Workday and similar: the field's wrapper holds the label text.
    const field = el.closest('[data-automation-id^="formField"], .field, .form-group, .application-question, li, td');
    if (field && !parts.join("").trim()) {
      const lab = field.querySelector("label, legend, .label, [data-automation-id*='label']");
      if (lab) parts.push(lab.innerText);
    }
    if (!parts.join("").trim() && el.placeholder) parts.push(el.placeholder);
    if (!parts.join("").trim() && el.name) parts.push(el.name.replace(/[_\-[\]]+/g, " "));
    return parts.join(" ").replace(/\s+/g, " ").replace(/\*/g, "").trim().slice(0, 300);
  }

  function controls() {
    return [...document.querySelectorAll("input, textarea, select")].filter(
      (el) => !SKIP_TYPES.has((el.type || "").toLowerCase()) && !el.disabled && visible(el)
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
      const label = labelOf(el);
      if (label) out.push(label);
    }
    return out;
  }

  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set; // works with React-controlled inputs
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function chooseOption(select, answer) {
    const a = norm(answer);
    const opt = [...select.options].find((o) => norm(o.text) === a) ||
                [...select.options].find((o) => a && norm(o.text).startsWith(a)) ||
                [...select.options].find((o) => a && norm(o.text).includes(a));
    if (!opt) return false;
    select.value = opt.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function similarity(a, b) {
    const A = new Set(norm(a).split(" ").filter((w) => w.length > 2));
    const B = new Set(norm(b).split(" ").filter((w) => w.length > 2));
    if (!A.size || !B.size) return 0;
    let both = 0;
    for (const w of A) if (B.has(w)) both++;
    return both / Math.max(A.size, B.size);
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
    let field = null, fieldLen = 0;
    for (const f of ctx.fields) {
      for (const kw of f.keywords) {
        if (l.includes(norm(kw)) && kw.length > fieldLen) { field = f; fieldLen = kw.length; }
      }
    }
    // "Name" alone means full name; "first/last name" are their own fields.
    if (!field && /^(your )?name$/.test(l)) field = ctx.fields.find((f) => f.key === "full_name");
    return field ? { value: field.value, from: field.label } : null;
  }

  function fill(ctx) {
    const sensitive = new RegExp(ctx.sensitive, "i");
    const filled = [], skipped = [];
    const doneRadio = new Set();
    for (const el of controls()) {
      const type = (el.type || "").toLowerCase();
      if (type === "file") continue;
      const label = labelOf(el);
      if (type === "radio" || type === "checkbox") {
        if (type === "radio" && doneRadio.has(el.name)) continue;
        const group = type === "radio" && el.name ? [...document.getElementsByName(el.name)] : [el];
        const question = type === "radio" ? labelOf(group[0]).replace(textOf(group[0].id), "") : label;
        const fieldset = el.closest("fieldset");
        const groupLabel = fieldset?.querySelector("legend")?.innerText || question;
        const ans = answerFor(groupLabel, ctx, sensitive);
        doneRadio.add(el.name);
        if (!ans || group.some((r) => r.checked)) continue;
        const target = group.find((r) => norm(labelOf(r)).endsWith(norm(ans.value)) || norm(r.value) === norm(ans.value));
        if (target) { target.click(); filled.push({ label: groupLabel, value: ans.value, from: ans.from }); }
        continue;
      }
      if ((el.value || "").trim()) continue; // never overwrite what's there
      const ans = answerFor(label, ctx, sensitive);
      if (!ans) { if (label) skipped.push(label); continue; }
      const ok = el.tagName === "SELECT" ? chooseOption(el, ans.value) : (setValue(el, ans.value), true);
      if (ok) {
        el.style.outline = "2px solid #2f7d4f";
        filled.push({ label, value: ans.value, from: ans.from });
      } else skipped.push(label);
    }
    return { filled, skipped };
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

  // What you typed on this page, so you can save it for next time (sensitive questions excluded).
  function collect(sensitivePattern) {
    const sensitive = new RegExp(sensitivePattern, "i");
    const out = [];
    const seen = new Set();
    for (const el of controls()) {
      const type = (el.type || "").toLowerCase();
      if (type === "file") continue;
      let label = labelOf(el), value = "";
      if (type === "radio") {
        if (seen.has(el.name) || !el.checked) continue;
        seen.add(el.name);
        const legend = el.closest("fieldset")?.querySelector("legend")?.innerText;
        value = labelOf(el).replace(legend || "", "").trim();
        label = legend || label;
      } else if (type === "checkbox") {
        continue;
      } else if (el.tagName === "SELECT") {
        value = el.selectedIndex > 0 ? el.options[el.selectedIndex].text : "";
      } else {
        value = (el.value || "").trim();
      }
      if (label && value && !sensitive.test(label) && value.length < 5000) out.push({ question: label, answer: value });
    }
    return out;
  }

  // Called by the side panel in every frame of the page (forms are often in an embedded frame).
  window.__jobHunterApi = {
    questions: () => ({ questions: questions(), title: document.title }),
    fill: (ctx) => fill(ctx),
    attach: (a) => attach(a.base64, a.filename, a.mime, a.which),
    collect: (pattern) => ({ items: collect(pattern) }),
  };
})();
