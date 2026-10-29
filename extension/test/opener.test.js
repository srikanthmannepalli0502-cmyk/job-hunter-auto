// background.js's auto-open against a fake Chrome: every ready job opens in its own background
// tab, up to the "at most" setting; a closed tab or a finished run makes room for the next.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "background.js"), "utf8");

function load({ jobs, maxOpen = 10, running = 0 }) {
  const local = { autoOpen: true, maxOpen }, runs = {}, created = [], listeners = {};
  for (let i = 0; i < running; i++) runs[900 + i] = { tabId: 900 + i, state: "running", title: `Already ${i}` };
  const ready = [...jobs];
  let nextTab = 1;
  const on = (name) => ({ addListener: (f) => { listeners[name] = f; } });
  const chrome = {
    runtime: { onInstalled: on("installed"), onStartup: on("startup"), onMessage: on("message") },
    sidePanel: { setPanelBehavior: () => {} },
    notifications: { onClicked: on("nclick"), onButtonClicked: on("nbutton") },
    tabs: { onUpdated: on("updated"), onRemoved: on("removed"),
            create: async (o) => { const t = { id: nextTab++, ...o }; created.push(t); return t; }, update: async () => ({}) },
    alarms: { get: async () => ({}), create: () => {}, onAlarm: on("alarm") },
    storage: { local: { get: async (k) => Object.fromEntries([].concat(k).map((x) => [x, local[x]])), set: async (v) => Object.assign(local, v) } },
  };
  const api = async (p) => {
    if (p === "/api/x/ready") return { jobs: [...ready] };
    const m = p.match(/^\/api\/x\/opened\/(\d+)$/);
    if (m) { ready.splice(ready.findIndex((j) => j.id === +m[1]), 1); return { saved: true }; }
    throw new Error(`unexpected ${p}`);
  };
  const ctx = vm.createContext({
    chrome, api, MAX_OPEN: 10, importScripts: () => {}, console,
    activeRuns: async () => Object.values(runs).filter((r) => ["waiting", "running"].includes(r.state)),
    saveRun: async (id, run) => { if (run) runs[id] = run; else delete runs[id]; },
    tick: async () => {}, stopping: new Set(), startRun: async () => {},
  });
  vm.runInContext(SRC, ctx);
  return { ctx, local, runs, created, listeners, ready };
}

const JOBS = [1, 2, 3, 4].map((id) => ({ id, title: `Data Analyst ${id}`, company: `Co ${id}`, url: `https://co${id}.example.com/job/${id}` }));

test("every ready job opens at once, each in its own background tab", async () => {
  const { ctx, created, runs, local } = load({ jobs: JOBS });
  await ctx.checkReady();
  assert.deepEqual(created.map((t) => t.url), JOBS.map((j) => j.url));
  assert.ok(created.every((t) => t.active === false));
  assert.equal(Object.values(runs).filter((r) => r.state === "waiting").length, 4);
  assert.match(local.autoStatus.text, /^Opened 4: Data Analyst 1 at Co 1; .* 4 filling now\.$/);
});

test("up to the 'at most' setting; the rest open as places free up, without waiting for the minute", async () => {
  const { ctx, created, listeners, local } = load({ jobs: JOBS, maxOpen: 3, running: 1 });
  await ctx.checkReady();
  assert.equal(created.length, 2);
  assert.match(local.autoStatus.text, /2 more open as these finish/);
  await ctx.checkReady(); // full: nothing more
  assert.equal(created.length, 2);
  await listeners.removed(created[0].id); // you closed one
  await new Promise((r) => setImmediate(r));
  assert.equal(created.length, 3);
  assert.equal(created[2].url, JOBS[2].url);
});

test("two checks at the same moment never open a job twice", async () => {
  const { ctx, created } = load({ jobs: JOBS.slice(0, 2) });
  await Promise.all([ctx.checkReady(), ctx.checkReady(), ctx.checkReady()]);
  assert.deepEqual(created.map((t) => t.url), JOBS.slice(0, 2).map((j) => j.url));
});
