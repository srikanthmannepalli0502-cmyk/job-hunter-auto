// Runs autopilot.js (the background Fill to Review) against a fake Chrome and a fake Workday page:
// posting -> Create Account -> steps -> Review. Timers fire at once, so it takes no real time.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = ["mail.js", "autopilot.js"].map((f) => fs.readFileSync(path.join(__dirname, "..", f), "utf8")).join("\n");
const LOGIN = { email: "test.user@example.com", password: "Fixture-only-9x!" };

// A Workday application as content.js's API would report it. `steps` are the steps after sign-in.
function fakePage(steps) {
  const page = { view: "posting", n: 0, calls: [], uploaded: false };
  const stepNow = () => ({ n: page.n + 1, of: steps.length, name: steps[page.n].name });
  page.api = {
    status: () => {
      const base = { workday: false, step: null, review: false, next: false, needsResume: false, signIn: false,
                     account: null, posting: false, captcha: false, verifyEmail: false, form: 0, errors: [] };
      if (page.view === "posting") return { ...base, posting: true, form: 1 };
      if (page.view === "create" || page.view === "signin") return { ...base, signIn: true, account: page.view, form: 3 };
      const s = stepNow();
      return { ...base, workday: true, step: s, review: page.n === steps.length - 1, next: page.n < steps.length - 1,
               needsResume: !!steps[page.n].resume && !page.uploaded, form: 5 };
    },
    start: () => { page.view = "create"; return { started: true }; },
    account: ({ mode, email, password }) => {
      if (mode === "signin") { page.view = "signin"; return { done: false, errors: ["Wrong email address or password."] }; }
      assert.deepEqual({ email, password }, LOGIN);
      page.view = "app";
      return { done: true };
    },
    fill: () => ({ filled: [{ label: "First Name", value: "Sam", from: "profile" }], skipped: steps[page.n].skipped || [] }),
    attach: () => { page.uploaded = true; return { attached: true }; },
    next: () => {
      if (page.n === steps.length - 1) throw new Error("pressed next on Review");
      page.n++;
      page.uploaded = false;
      return { moved: true, step: stepNow() };
    },
  };
  return page;
}

function load(page, { url = "https://comcast.wd5.myworkdayjobs.com/en-US/careers/job/Data-Analyst_R123", mail = null } = {}) {
  const session = {}, local = { consentOnCreate: true };
  const out = { notes: [], posts: [], targets: [], opened: [], mail: [] };
  const store = (obj) => ({
    get: async (k) => (typeof k === "string" ? { [k]: obj[k] } : { ...obj }),
    set: async (v) => { Object.assign(obj, JSON.parse(JSON.stringify(v))); },
  });
  const chrome = {
    storage: { session: store(session), local: store(local) },
    tabs: { get: async (id) => ({ id, url: page.url || url, title: "Data Analyst" }),
            update: async (id, { url: to }) => { out.opened.push(to); page.onOpen?.(to); return {}; } },
    notifications: { create: (id, n) => out.notes.push({ id, ...n }) },
    scripting: {
      executeScript: async ({ target, files, func, args }) => {
        if (files) return [];
        const [name, arg] = args;
        out.targets.push({ name, allFrames: !!target.allFrames });
        page.calls.push(name === "account" ? `account:${arg.mode}` : name);
        return [{ result: await page.api[name](arg) }];
      },
    },
  };
  const api = async (p, opts = {}) => {
    if (p.startsWith("/api/x/context")) return { job: { id: 7, title: "Data Analyst", company: "Comcast", has_resume: true } };
    if (p.startsWith("/api/x/progress/")) { out.posts.push(opts.body); return { saved: true }; }
    if (p.startsWith("/api/x/closed/")) { out.posts.push({ closed: p }); return { closed: 1 }; }
    throw new Error(`unexpected call ${p}`);
  };
  const ctx = vm.createContext({
    chrome, api, URL, URLSearchParams, console,
    Vault: { load: async () => ({ ...LOGIN }) },
    fileArg: async () => ({ base64: "", filename: "resume.pdf", mime: "application/pdf", which: "resume" }),
    setTimeout: (f) => setImmediate(f),
  });
  vm.runInContext(SRC, ctx);
  // The confirmation email, as mail.js would read it from Gmail.
  ctx.fromMail = async (q) => { out.mail.push(q); return mail ? mail(q) : { none: true }; };
  return { ctx, out, session };
}

const STEPS = [{ name: "My Information" }, { name: "My Experience", resume: true }, { name: "Review" }];

test("from the posting to Review: Apply, sign in or create the account, fill, attach, Save and Continue", async () => {
  const page = fakePage(STEPS);
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.deepEqual(page.calls.filter((c) => c !== "status"),
    ["start", "account:signin", "account:create", "fill", "next", "fill", "attach", "next"]);
  const run = session.runs[5];
  assert.equal(run.state, "review");
  assert.equal(run.title, "Data Analyst at Comcast");
  assert.equal(out.notes.at(-1).title, "Ready for you to submit");
  assert.deepEqual(out.posts.map((p) => p.state), ["review"]);
  // The login only ever goes to the top frame.
  assert.ok(out.targets.filter((t) => t.name === "account").every((t) => !t.allFrames));
  assert.ok(!JSON.stringify(run).includes(LOGIN.password));
});

test("a question that's yours stops it; Continue goes on from that step", async () => {
  const steps = [{ name: "My Information" }, { name: "Application Questions", skipped: ["Are you a veteran? (answer this one yourself)"] }, { name: "Review" }];
  const page = fakePage(steps);
  page.view = "app";
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "needs_you");
  assert.equal(page.n, 1);
  assert.equal(out.notes.at(-1).title, "Job Hunter needs you");
  assert.equal(out.notes.at(-1).buttons[0].title, "I've done it, continue");
  assert.match(out.posts.at(-1).note, /veteran/);
  await ctx.startRun(5, { trust: true });
  assert.equal(session.runs[5].state, "review");
  assert.equal(page.n, 2);
});

test("no saved login: it asks you to sign in, and never types anything into the form", async () => {
  const page = fakePage(STEPS);
  page.view = "create";
  const { ctx, out, session } = load(page);
  ctx.Vault.load = async () => null;
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "needs_you");
  assert.ok(!page.calls.some((c) => c.startsWith("account")));
  assert.match(out.notes.at(-1).message, /job-site login/);
});

test("the saved login is never typed on a Google (or other identity) sign-in", async () => {
  const page = fakePage(STEPS);
  page.view = "signin";
  const { ctx, session } = load(page, { url: "https://accounts.google.com/signin" });
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "needs_you");
  assert.ok(!page.calls.some((c) => c.startsWith("account")));
});

test("a password that doesn't work and an account that exists: it stops and asks you", async () => {
  const page = fakePage(STEPS);
  page.view = "signin";
  page.api.account = ({ mode }) => ({ done: false, errors: [mode === "signin" ? "Wrong email address or password." : "There is already an account with this email."] });
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "needs_you");
  assert.deepEqual(page.calls.filter((c) => c.startsWith("account")), ["account:signin", "account:create"]);
  assert.match(out.notes.at(-1).message, /already an account/);
});

test("a posting that's been taken down is marked closed, and nothing is filled", async () => {
  const page = fakePage(STEPS);
  page.api.status = () => ({ workday: false, step: null, posting: false, account: null, closed: "no longer accepting", form: 0, errors: [] });
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "stopped");
  assert.deepEqual(out.posts, [{ closed: "/api/x/closed/7" }]);
  assert.ok(!page.calls.includes("fill"));
});

test("on a site it doesn't know, a posting page (a search box, no resume upload) isn't filled", async () => {
  const page = fakePage(STEPS);
  page.api.status = () => ({ workday: false, step: null, posting: false, account: null, closed: "", needsResume: false, form: 2, errors: [] });
  const { ctx, out, session } = load(page, { url: "https://www.amazon.jobs/en/jobs/10589280/data-scientist" });
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "needs_you");
  assert.ok(!page.calls.includes("fill"));
  assert.match(out.notes.at(-1).message, /amazon\.jobs/);
});

// Another site's application, as content.js reports it: amazon.jobs's email-first sign-in, then
// pages with Next, then the last page with Submit.
function otherSite() {
  const page = { view: "posting", n: 0, calls: [], url: "https://www.amazon.jobs/en/jobs/10589280/data-scientist" };
  const base = { workday: false, step: null, review: false, next: false, needsResume: false, signIn: false,
                 account: null, posting: false, closed: "", captcha: false, verifyEmail: false, form: 0, errors: [], title: "" };
  page.api = {
    status: () => {
      if (page.view === "posting") return { ...base, posting: true, form: 2 };
      if (["email", "signin", "create", "code"].includes(page.view)) return { ...base, signIn: true, account: page.view, form: 2 };
      if (page.view === "check-email") return { ...base, verifyEmail: true };
      return { ...base, form: 6, next: page.n < 2, review: page.n === 2, title: `Page ${page.n + 1}` };
    },
    start: () => { page.view = "email"; page.url = "https://passport.amazon.jobs/"; return { started: true }; },
    account: ({ mode, email, password, code, allow }) => {
      assert.ok(new RegExp(allow).test(new URL(page.url).hostname), `login typed on ${page.url}`);
      if (mode === "email") { assert.equal(email, LOGIN.email); page.view = page.newUser ? "create" : "signin"; return { done: true }; }
      if (mode === "code") { assert.equal(code, "482913"); page.view = "app"; page.url = "https://account.amazon.jobs/en-US/applicant/jobs/10589280/apply"; return { done: true }; }
      assert.equal(password, LOGIN.password);
      if (mode === "create") { page.view = page.confirm; return { done: true }; }
      page.view = "app";
      page.url = "https://account.amazon.jobs/en-US/applicant/jobs/10589280/apply";
      return { done: true };
    },
    fill: () => ({ filled: [{ label: "First name", value: "Sam", from: "profile" }], skipped: [] }),
    attach: () => ({ attached: true }),
    next: () => {
      if (page.n === 2) throw new Error("pressed next on the last page");
      page.n++;
      return { moved: true };
    },
  };
  page.onOpen = (to) => { if (/verify/.test(to)) { page.view = "signin"; page.url = "https://passport.amazon.jobs/"; } };
  return page;
}

test("amazon.jobs: Apply, the email-first sign-in, then each page with Next up to Submit (never pressed)", async () => {
  const page = otherSite();
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.deepEqual(page.calls.filter((c) => c !== "status"),
    ["start", "account:email", "account:signin", "fill", "next", "fill", "next", "fill"]);
  assert.equal(session.runs[5].state, "review");
  assert.equal(out.notes.at(-1).title, "Ready for you to submit");
});

test("a new account the site asks to confirm by email: it opens the link from that email, then signs in", async () => {
  const page = otherSite();
  Object.assign(page, { newUser: true, confirm: "check-email" });
  const { ctx, out, session } = load(page, { mail: ({ kind }) => (kind === "link" ? { link: "https://passport.amazon.jobs/verify?t=abc" } : { none: true }) });
  await ctx.startRun(5, { jobId: 7 });
  assert.deepEqual(page.calls.filter((c) => c.startsWith("account")), ["account:email", "account:create", "account:signin"]);
  assert.deepEqual(out.opened, ["https://passport.amazon.jobs/verify?t=abc"]);
  assert.equal(out.mail[0].email, LOGIN.email);
  assert.ok(out.mail[0].hosts.includes("amazon.jobs"));
  assert.equal(session.runs[5].state, "review");
});

test("a code sent by email is typed into the site's code box", async () => {
  const page = otherSite();
  Object.assign(page, { newUser: true, confirm: "code" });
  const { ctx, session } = load(page, { mail: ({ kind }) => (kind === "code" ? { code: "482913" } : { none: true }) });
  await ctx.startRun(5, { jobId: 7 });
  assert.deepEqual(page.calls.filter((c) => c.startsWith("account")), ["account:email", "account:create", "account:code"]);
  assert.equal(session.runs[5].state, "review");
});

test("while the confirmation email hasn't come it waits and looks again; it never guesses", async () => {
  const page = otherSite();
  Object.assign(page, { newUser: true, confirm: "check-email" });
  const { ctx, out, session } = load(page);
  await ctx.startRun(5, { jobId: 7 });
  assert.equal(session.runs[5].state, "waiting", JSON.stringify(session.runs[5].lines));
  assert.deepEqual(out.opened, []);
  assert.equal(out.mail.length, 1);
});
