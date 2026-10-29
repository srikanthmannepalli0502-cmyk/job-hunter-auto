// The confirmation email a job site sends after you sign up: Job Hunter opens Gmail (signed in in
// this Chrome, as the saved job-site login's address) in a background tab, searches for a recent
// email from that site, and reads only that email: its confirmation link (opened in the
// application's tab) or its code (typed into the site's code box). The link must point to the
// same site. Nothing else in your mail is read, and the Gmail tab is closed again.

const GMAIL = /@(gmail|googlemail)\.com$/i;
// Sites whose emails come from another domain than their pages.
const MAIL_FROM = [[/(^|\.)(myworkdayjobs|myworkdaysite|workday)\.com$/i, ["myworkday.com", "workday.com", "myworkdayjobs.com"]],
                   [/(^|\.)amazon\.jobs$/i, ["amazon.jobs", "amazon.com"]]];

// "comcast.wd5.myworkdayjobs.com" -> "myworkdayjobs.com" (two labels; three for co.uk-style names).
function baseDomain(host) {
  const parts = (host || "").toLowerCase().split(".").filter(Boolean);
  const n = parts.length > 2 && /^(co|com|org|net|ac|gov)$/.test(parts[parts.length - 2]) ? 3 : 2;
  return parts.slice(-n).join(".");
}
// The domains a site's emails come from, and its links may point to.
function mailDomains(hosts) {
  const out = new Set();
  for (const h of hosts.filter(Boolean)) {
    out.add(baseDomain(h));
    for (const [re, extra] of MAIL_FROM) if (re.test(h)) extra.forEach((d) => out.add(d));
  }
  return [...out];
}

// {link} or {code} from the site's email sent after `since`, {none: true} if it hasn't come yet,
// or {error} when the mailbox can't be read (not Gmail, or not signed in to it in this Chrome).
async function fromMail({ email, kind, since, hosts }) {
  if (!GMAIL.test(email || "")) return { error: "the saved login isn't a Gmail address, so Job Hunter can't read its confirmation email" };
  const domains = mailDomains(hosts);
  const words = kind === "code" ? "code" : "(verify OR verification OR confirm OR activate OR validate)";
  const query = `newer_than:1d ${words} {${domains.map((d) => `from:${d}`).join(" ")}}`;
  const tab = await chrome.tabs.create({ url: "about:blank", active: false });
  try {
    for (let u = 0; u < 5; u++) { // the Google accounts signed in to this Chrome, in order
      await chrome.tabs.update(tab.id, { url: `https://mail.google.com/mail/u/${u}/#search/${encodeURIComponent(query)}` });
      await loaded(tab.id);
      const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readGmail, args: [email, kind, since, domains] });
      const res = r?.result || { error: "Gmail didn't open" };
      if (res.otherAccount) continue;
      return res;
    }
    return { error: `${email} isn't signed in to Gmail in this Chrome` };
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function loaded(tabId) {
  for (let i = 0; i < 40; i++) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete" && t.url && t.url !== "about:blank") return t;
    await pause(500);
  }
  return null;
}

// Runs in the Gmail tab. Self-contained (executeScript passes only the function's source).
async function readGmail(email, kind, since, domains) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const shown = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  if (location.hostname !== "mail.google.com") return { error: "not signed in to Gmail in this Chrome" };
  const fromSite = (addr) => domains.some((d) => (addr || "").toLowerCase().endsWith("@" + d) || (addr || "").toLowerCase().endsWith("." + d));
  let rows = [];
  for (let i = 0; i < 40; i++) { // Gmail draws the search results after the page loads
    await wait(500);
    if (!document.title.includes("@")) continue;
    if (!document.title.toLowerCase().includes(email.toLowerCase())) return { otherAccount: true };
    rows = [...document.querySelectorAll('div[role="main"] tr.zA')].filter(shown);
    if (rows.length || /no messages matched/i.test(document.querySelector('div[role="main"]')?.innerText || "")) break;
  }
  const when = (row) => {
    const t = row.querySelector("td.xW span[title], td.xW [title]")?.getAttribute("title") || "";
    return Date.parse(t.replace(/ /g, " ").replace(/ at /, " "));
  };
  const row = rows.find((r) => [...r.querySelectorAll("[email]")].some((s) => fromSite(s.getAttribute("email"))) &&
                               !(when(r) < since - 2 * 60 * 1000));
  if (!row) return { none: true };
  row.click();
  let body = null;
  for (let i = 0; i < 30 && !body; i++) {
    await wait(500);
    body = [...document.querySelectorAll("div.a3s")].filter(shown).pop() || null;
  }
  if (!body) return { error: "the email didn't open in Gmail" };
  if (kind === "code") {
    const text = body.innerText.replace(/\s+/g, " ");
    const m = text.match(/(?:code|passcode|pin|otp)\D{0,40}?\b(\d{4,8})\b/i) || text.match(/\b(\d{4,8})\b\D{0,40}(?:is your|code)/i);
    return m ? { code: m[1] } : { error: "no code found in the site's email" };
  }
  const real = (a) => { // Gmail wraps links as google.com/url?q=<the link>
    try {
      const u = new URL(a.href);
      return /(^|\.)google\.com$/.test(u.hostname) && u.searchParams.get("q") ? new URL(u.searchParams.get("q")) : u;
    } catch { return null; }
  };
  const onSite = (u) => u && /^https:$/.test(u.protocol) && domains.some((d) => u.hostname === d || u.hostname.endsWith("." + d));
  const links = [...body.querySelectorAll("a[href]")].map((a) => ({ a, u: real(a) })).filter((x) => onSite(x.u));
  const pick = links.find((x) => /verif|confirm|activat|validat/i.test(x.a.innerText + " " + x.u.href)) || null;
  return pick ? { link: pick.u.href } : { error: "no confirmation link to the site in its email" };
}
