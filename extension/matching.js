// Deciding what to fill: which saved answer or profile field answers a question, and which of a
// question's options an answer means. No DOM here, so it runs in the page (injected before
// content.js) and under `node --test`.
// Work authorization, sponsorship, immigration and demographic questions fail closed: they get an
// answer only from a saved answer to the same question (or an equivalent wording listed below), or
// from the matching profile field when the question asks exactly what that field records. Wording
// that looks similar is not enough, and an option is never picked for them by fuzzy text.
// The equivalent-question lists and option rules follow Formwright (MIT, github.com/KshitijBharambe/Formwright).
(() => {
  const norm = (s) => (s || "").normalize("NFKC").toLowerCase()
    .replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  const PLACEHOLDER = /^(select one|select|choose|choose one|please select|none selected|no selection)?$/;

  // The question itself, without trailing "(Required)" style notes.
  const questionText = (s) => norm((s || "").replace(
    /(\s*\((required|optional|select all that apply|choose all that apply|select one|choose one)\))+\s*\**\s*$/i, ""));

  // Wordings that ask the same thing. Kept apart on purpose: "currently require" vs "in the
  // future", and questions naming the United States vs questions that don't.
  const EQUIVALENT = [
    ["Will you now or in the future require employment sponsorship?",
      "Will you now or in the future require visa sponsorship?",
      "Will you now or in the future require sponsorship for employment visa status?",
      "Will you now or at any time in the future require visa sponsorship?",
      "Do you now or in the future require sponsorship for employment visa status?",
      "Do you now or will you in the future require visa sponsorship?",
      "Will you require visa sponsorship now or in the future?",
      "Will you require sponsorship now or in the future?",
      "Do you require visa sponsorship now or in the future?",
      "Will you now or in the future require sponsorship for an employment visa?",
      "Will you now or in the future require sponsorship for employment?",
      "Will you, now or in the future, require sponsorship for employment?"],
    ["Do you now or in the future require visa sponsorship to work in the United States?",
      "Will you now or in the future require visa sponsorship to work in the United States?",
      "Will you require visa sponsorship now or in the future to work in the United States?",
      "Will you now or in the future require sponsorship to work in the United States?"],
    ["Do you currently require visa sponsorship?", "Do you currently require employment visa sponsorship?",
      "Do you require visa sponsorship at this time?", "Do you currently require sponsorship?"],
    ["Will you require visa sponsorship in the future?", "Will you need visa sponsorship in the future?",
      "Will you require sponsorship in the future?"],
    ["Are you legally authorized to work in the United States?", "Are you authorized to work in the US?",
      "Are you legally authorized to work in the U.S.?", "Are you legally authorized to work in the United States of America?",
      "Are you currently authorized to work in the United States?", "Are you legally eligible to work in the United States?",
      "Do you have authorization to work in the United States?", "Are you authorized to work in the United States?"],
    ["Are you willing to relocate?", "Are you open to relocation?", "Would you be willing to relocate?", "Willing to relocate"],
    ["Are you open to remote work?", "Are you willing to work remotely?"],
    ["How did you hear about this role?", "How did you initially hear about this job?", "How did you hear about this job?",
      "How did you hear about us?", "How did you hear about this position?"],
    ["Gender", "What is your gender?"],
    ["Highest level of education", "Highest education level", "What is your highest level of education?",
      "Highest degree earned", "What is the highest degree you have earned?"],
  ];
  const KEYS = new Map(EQUIVALENT.flatMap((group, i) => group.map((q) => [questionText(q), `equivalent:${i}`])));
  const questionKey = (q) => KEYS.get(questionText(q)) ?? questionText(q);

  // Which fail-closed topic a question is about, if any.
  const SPONSOR = /\bsponsor/;
  const AUTH = /\b(authori[sz]ed|authori[sz]ation|eligible|eligibility|permitted|allowed|able|right) to work\b|\bwork (authori[sz]ation|permit|eligibility)\b/;
  const IMMIGRATION = /\bvisa\b|\bimmigration\b|\bcitizen(ship)?\b|\bgreen card\b|\bpermanent resident\b|\bh 1 ?b\b|\bf 1\b|\bopt\b|\bcpt\b|\bstem opt\b|\bead\b/;
  const DEMOGRAPHIC = /\bgender\b|\bsex\b|\brace\b|\bethnic|\bhispanic\b|\blatin[oax]\b|\bveteran\b|\bdisabilit|\bsexual orientation\b|\btransgender\b|\bpronouns?\b/;
  function topic(label) {
    const l = norm(label);
    if (SPONSOR.test(l)) return "sponsorship";
    if (AUTH.test(l)) return "authorization";
    if (IMMIGRATION.test(l)) return "immigration";
    if (DEMOGRAPHIC.test(l)) return "demographic";
    return null;
  }

  // A country other than the U.S. ("authorized to work in Canada" is not your U.S. answer).
  const FOREIGN = /\b(canada|united kingdom|uk|england|britain|ireland|germany|france|netherlands|india|australia|new zealand|singapore|mexico|brazil|japan|china|israel|spain|poland|switzerland|sweden|uae|europe|european union|eu|emea|apac|latam)\b/;
  const NOW = /\b(now|currently|current|at this time|presently|today)\b/;
  const LATER = /\b(future|later|eventually|at any time)\b/;
  const NEED = /\b(require|requires|required|requiring|need|needs|needed|needing)\b/;
  const INVERTED = /\bwithout\b|\bnot need\b|\bnot require\b/;

  // The profile fields for fail-closed topics, and the questions each one answers exactly.
  const GATES = {
    // "Will you need visa sponsorship (now or later)?": not "currently" alone, not "in the future" alone.
    // A question naming no time ("Do you require sponsorship?") conventionally means either.
    needs_sponsorship: (l) => topic(l) === "sponsorship" && NEED.test(l) && !INVERTED.test(l) && !FOREIGN.test(l)
      && NOW.test(l) === LATER.test(l),
    work_authorized: (l) => topic(l) === "authorization" && !INVERTED.test(l) && !FOREIGN.test(l),
    work_status: (l) => ["immigration", "authorization"].includes(topic(l)) && !FOREIGN.test(l)
      && /\b(status|type|what|which|current visa)\b/.test(l),
  };

  function similarity(a, b) {
    const A = new Set(norm(a).split(" ").filter((w) => w.length > 2));
    const B = new Set(norm(b).split(" ").filter((w) => w.length > 2));
    if (!A.size || !B.size) return 0;
    let both = 0;
    for (const w of A) if (B.has(w)) both++;
    return both / Math.max(A.size, B.size);
  }

  // "Yes" / "No, I don't" -> true / false; anything else -> undefined.
  function binaryAnswer(answer) {
    const m = (answer || "").trim().match(/^(yes|no|true|false|y|n)(?:[,.!]\s|[,.!]?$)/i);
    return m ? /^(yes|true|y)$/i.test(m[1]) : undefined;
  }
  const booleanOption = (text) => /^(yes|true|y)$/.test(norm(text)) ? true : /^(no|false|n)$/.test(norm(text)) ? false : undefined;
  const agree = (values) => new Set(values.map((v) => binaryAnswer(v) ?? norm(v))).size === 1;

  // The company's name in a question ("worked at Comcast", "relatives at Citibank") replaced by
  // "the company", so an answer saved at one company answers the same question at the next. The
  // server does the same to questions it saves.
  const SUFFIX = /[,.]?\s+(inc|llc|l\.l\.c|n\.a|corp|corporation|co|ltd|lp|llp|l\.p|plc|group)\.?$/i;
  function companyNames(company) {
    let name = (company || "").trim();
    while (SUFFIX.test(name)) name = name.replace(SUFFIX, "").trim();
    const first = name.split(/\s+/)[0] || "";
    return [...new Set([name, first.length >= 4 ? first : ""].filter((n) => n.length >= 3))];
  }
  function generalize(text, company) {
    let t = text || "";
    for (const n of companyNames(company)) {
      t = t.replace(new RegExp(`\\b${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "gi"), "the company");
    }
    return t;
  }

  // A saved answer for this question: {status: "matched", value} | {status: "conflict"} | {status: "unmatched"}.
  function savedAnswer(label, answers, company = "") {
    label = generalize(label, company);
    answers = answers.map((a) => ({ ...a, question: generalize(a.question, company) }));
    const key = questionKey(label);
    const same = answers.filter((a) => (a.answer || "").trim() && questionKey(a.question) === key).map((a) => a.answer);
    if (same.length) return agree(same) ? { status: "matched", value: same[0] } : { status: "conflict" };
    if (topic(label)) return { status: "unmatched" };
    // Ordinary questions may reuse an answer to a nearly identical question, unless an equally
    // close one says something else.
    const scored = answers.filter((a) => (a.answer || "").trim() && !topic(a.question))
      .map((a) => [a.answer, similarity(a.question, label)]).filter(([, s]) => s >= 0.75).sort((x, y) => y[1] - x[1]);
    if (!scored.length) return { status: "unmatched" };
    const top = scored.filter(([, s]) => s >= scored[0][1] - 0.05).map(([v]) => v);
    return agree(top) ? { status: "matched", value: top[0] } : { status: "conflict" };
  }

  // The answer for a question, from a saved answer or a profile field.
  // {status: "matched", value, from} | {status: "conflict"} (saved answers disagree)
  // | {status: "yours"} (a fail-closed question nothing answers exactly) | {status: "none"}.
  function answerFor(label, ctx) {
    if (!label) return { status: "none" };
    const saved = savedAnswer(label, ctx.answers || [], ctx.job?.company || "");
    if (saved.status === "matched") return { status: "matched", value: saved.value, from: "saved answer" };
    if (saved.status === "conflict") return saved;
    const l = norm(label);
    const t = topic(label);
    const words = new Set(l.split(" "));
    let field = null, fieldLen = 0;
    for (const f of ctx.fields || []) {
      const gate = GATES[f.key];
      if (t ? !(gate && gate(l)) : gate) continue; // fail-closed questions get only their own field
      // "Phone Extension" is not your phone; "Country Phone Code" is not your country.
      if ((f.exclude || []).some((x) => (x.includes(" ") ? l.includes(norm(x)) : words.has(x)))) continue;
      for (const kw of f.keywords) {
        if (l.includes(norm(kw)) && kw.length > fieldLen) { field = f; fieldLen = kw.length; }
      }
    }
    if (!field && !t && /^(your )?name$/.test(l)) field = (ctx.fields || []).find((f) => f.key === "full_name");
    if (field) return { status: "matched", value: field.value, from: field.label };
    return { status: t ? "yours" : "none" };
  }

  const US_ANSWER = /^(us|usa|u s|u s a|united states|united states of america|america)$/;
  const US_OPTION = /^(us|usa|united states|united states of america)( 1)?$/;

  // Which option an answer means: {status: "matched", index} | {status: "ambiguous"} | {status: "unmatched"}.
  // Each rule must point at exactly one option; two equally good options mean the choice is yours.
  function resolveOption(options, answer, label = "") {
    const a = norm(answer);
    const texts = options.map(norm);
    if (!a) return { status: "unmatched" };
    const pick = (f) => {
      const idx = texts.flatMap((t, i) => (!PLACEHOLDER.test(t) && f(t, i) ? [i] : []));
      return idx.length === 1 ? { status: "matched", index: idx[0] } : idx.length ? { status: "ambiguous" } : null;
    };
    let r = pick((t) => t === a);
    if (r) return r;
    // Yes/No means a plain Yes/No option, never "Yes, but only ..." (that asserts something else).
    const bin = binaryAnswer(answer);
    if (bin !== undefined) return pick((t) => booleanOption(t) === bin) || { status: "unmatched" };
    if (/\bcountry\b/.test(norm(label)) && US_ANSWER.test(a)) {
      r = pick((t) => US_OPTION.test(t));
      if (r) return r;
    }
    if (topic(label)) return { status: "unmatched" };
    r = pick((t) => t.startsWith(a + " "));
    if (r) return r;
    if (a.length > 3) {
      r = pick((t) => t.includes(a));
      if (r) return r;
    }
    // "Data Science" is not "Computer Science": a field of study never matches on shared words.
    if (/\b(field of study|major|area of study|discipline)\b/.test(norm(label))) return { status: "unmatched" };
    const scores = options.map((o) => similarity(o, answer));
    const best = Math.max(0, ...scores);
    return best >= 0.5 ? pick((t, i) => scores[i] === best) : { status: "unmatched" };
  }

  // Answers that follow from what Job Hunter already knows, for questions nothing saved answers:
  // where it found the job, whether the company is in your work history, your phone type.
  // Candidates are tried in order through resolveOption; an ambiguous one is skipped, never guessed.
  // Never for fail-closed topics. {values: [...], from} | null.
  const HEARD = /\bhow did you (first |initially )?(hear|learn|find out)\b|\breferral source\b|^(source|candidate source|how did you hear)$/;
  const BEFORE = /\b(currently|previously|ever|before|formerly|in the past)\b/;
  const EMPLOYED = /\b(work|worked|working|employed|employee|employment|contractor|consultant|intern)\b/;
  const RELATED = /\b(relative|relatives|related|spouse|partner|family member|referred|referral|know anyone)\b/;
  function derived(label, ctx) {
    if (!label || topic(label)) return null;
    const job = ctx.job || {};
    const l = norm(generalize(label, job.company));
    // The follow-up to "How did you hear": which careers site (amazon.jobs: 'If "Amazon Career Site"
    // please specify' -> Amazon.jobs). The site the posting is on, by its address.
    if (/\b(please specify|specify which|which (career |careers )?(site|website))\b/.test(l) &&
        /\b(career|careers|site|website|web site)\b/.test(l) && job.found_via?.kind === "company_site" && job.url) {
      let host = "";
      try { host = new URL(job.url).hostname.replace(/^www\./, ""); } catch { /* no address */ }
      const site = host.split(".").slice(-2).join(".");
      return host ? { values: [...new Set([host, site])], from: "the site the job is posted on" } : null;
    }
    if (HEARD.test(l) || questionKey(label) === questionKey("How did you hear about us?")) {
      const via = job.found_via || {};
      const names = companyNames(job.company);  // "Comcast Cable Communications", "Comcast"
      const values = via.kind === "company_site"
        ? ["Company Website", "Company Career Site", "Career Site", "Careers Website", "Careers Page",
           ...names.flatMap((n) => [`${n} Careers`, `${n} Careers Website`, `${n} Jobs Career Site`, `${n} Website`]),
           "Corporate Website", "Website", "Internet",
           // no careers-site option: the company's own job alerts, then a generic choice
           // (Comcast: Email > Comcast Job Alert; Job Sites > Other Job Site)
           ...names.map((n) => `${n} Job Alert`), "Job Alert", "Other Job Site", "Other Website", "Other"]
        : via.name ? [via.name, "Job Board", "Online Job Board", "Internet"] : [];
      return values.length ? { values, from: "where Job Hunter found this job" } : null;
    }
    if (BEFORE.test(l) && EMPLOYED.test(l) && !RELATED.test(l) && /\bthe company\b|\bus\b/.test(l)) {
      const mine = (ctx.history?.jobs || []).map((j) => norm(generalize(j.company, job.company)));
      return { values: [mine.some((c) => c.includes("the company")) ? "Yes" : "No"], from: "your work history" };
    }
    if (/\b(field of study|major|area of study|discipline)\b/.test(l)) {
      const major = (ctx.fields || []).find((f) => f.key === "major")?.value || ctx.history?.education?.[0]?.major;
      if (!major) return null;
      // Most lists lack "Applied Data Science": the plain field, then the nearest broader one.
      const values = [major, major.replace(/^(applied|general)\s+/i, "")];
      if (/data science|analytics/i.test(major)) values.push("Data Science", "Information Systems", "Information Science");
      return { values: [...new Set(values)], from: "your degree (closest option)" };
    }
    // A degree list without "Master's Degree": the other names lists use for the same level.
    if (/^(degree|highest degree|degree type|level of education|education level|highest (level of )?education)\b/.test(l)) {
      const degree = (ctx.fields || []).find((f) => f.key === "degree")?.value || ctx.history?.education?.[0]?.degree || "";
      const values = /master/i.test(degree)
        ? [degree, "Master's Degree", "Masters Degree", "Master's", "Masters", "Master", "Master of Science", "MS", "M.S.", "Graduate Degree"]
        : /bachelor/i.test(degree)
          ? [degree, "Bachelor's Degree", "Bachelors Degree", "Bachelor's", "Bachelors", "Bachelor", "Bachelor of Science", "BS", "B.S."]
          : [];
      return values.length ? { values: [...new Set(values)], from: "your degree (closest option)" } : null;
    }
    if (/\bphone (device )?type\b|\btype of phone\b/.test(l)) {
      return { values: ["Mobile", "Cell", "Cell Phone", "Mobile Phone"], from: "your phone (mobile)" };
    }
    return null;
  }

  const api = { norm, questionKey, topic, savedAnswer, answerFor, resolveOption, binaryAnswer, PLACEHOLDER,
                generalize, derived };
  globalThis.__jobHunterMatch = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})();
