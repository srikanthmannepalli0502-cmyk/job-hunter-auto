// vault.js keeps the job-site login encrypted: chrome.storage holds only ciphertext.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Just enough IndexedDB for vault.js: one object store kept in memory.
function fakeIndexedDB() {
  const keys = new Map();
  const req = (fn) => { const r = {}; setImmediate(() => { r.result = fn(); r.onsuccess?.(); }); return r; };
  const db = { createObjectStore: () => {}, transaction: () => ({ objectStore: () => ({
    get: (k) => req(() => keys.get(k)), put: (v, k) => req(() => keys.set(k, v)) }) }) };
  return { open: () => { const r = {}; setImmediate(() => { r.result = db; r.onupgradeneeded?.(); r.onsuccess?.(); }); return r; } };
}

function load() {
  const local = {};
  const ctx = vm.createContext({
    crypto: globalThis.crypto, TextEncoder, TextDecoder, btoa, atob, indexedDB: fakeIndexedDB(),
    chrome: { storage: { local: {
      get: async (k) => ({ [k]: local[k] }), set: async (v) => Object.assign(local, v), remove: async (k) => { delete local[k]; } } } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "vault.js"), "utf8") + "\nthis.Vault = Vault;", ctx);
  return { Vault: ctx.Vault, local };
}

test("the login is stored encrypted and reads back", async () => {
  const { Vault, local } = load();
  assert.equal(await Vault.load(), null);
  await Vault.save("test.user@example.com", "Fixture-only-9x!");
  assert.ok(!JSON.stringify(local).includes("Fixture-only-9x!"));
  assert.ok(!JSON.stringify(local).includes("test.user"));
  assert.deepEqual(JSON.parse(JSON.stringify(await Vault.load())), { email: "test.user@example.com", password: "Fixture-only-9x!" });
  await Vault.clear();
  assert.equal(await Vault.load(), null);
});

test("generated passwords meet Workday's rules", () => {
  const { Vault } = load();
  for (let i = 0; i < 50; i++) {
    const p = Vault.generate();
    assert.equal(p.length, 16);
    assert.match(p, /[A-Z]/); assert.match(p, /[a-z]/); assert.match(p, /\d/); assert.match(p, /[!@#$%&*?\-_]/);
  }
});
