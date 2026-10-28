// Your job-site login (one email and password used for every Workday account), kept only in this
// browser: encrypted with AES-GCM under a key Chrome generated and won't export, stored in this
// extension's IndexedDB. It is never sent to your Job Hunter app and isn't synced.
const Vault = (() => {
  const open = () => new Promise((resolve, reject) => {
    const r = indexedDB.open("jobhunter-vault", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("keys");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  const done = (req) => new Promise((resolve, reject) => { req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); });

  async function key() {
    const db = await open();
    let k = await done(db.transaction("keys").objectStore("keys").get("login"));
    if (!k) {
      k = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      await done(db.transaction("keys", "readwrite").objectStore("keys").put(k, "login"));
    }
    return k;
  }
  const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function save(email, password) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(),
                                             new TextEncoder().encode(JSON.stringify({ email, password })));
    await chrome.storage.local.set({ login: { iv: b64(iv), data: b64(data) } });
  }
  // {email, password}, or null when none is saved (or it can't be read any more).
  async function load() {
    const { login } = await chrome.storage.local.get("login");
    if (!login) return null;
    try {
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(login.iv) }, await key(), unb64(login.data));
      return JSON.parse(new TextDecoder().decode(plain));
    } catch { return null; }
  }
  const clear = () => chrome.storage.local.remove("login");

  // A password Workday accepts everywhere: 16 characters with upper and lower case, digits and symbols.
  function generate() {
    const sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789", "!@#$%&*?-_"];
    const all = sets.join("");
    const pick = (set) => set[crypto.getRandomValues(new Uint32Array(1))[0] % set.length];
    const chars = [...sets.map(pick), ...Array.from({ length: 12 }, () => pick(all))];
    for (let i = chars.length - 1; i > 0; i--) {
      const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
      [chars[i], chars[j]] = [chars[j], chars[i]];
    }
    return chars.join("");
  }
  return { save, load, clear, generate };
})();
