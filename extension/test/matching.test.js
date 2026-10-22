// node --test "extension/test/*.test.js"
const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../matching.js");

const field = (key, label, keywords, value, exclude = []) => ({ key, label, keywords, value, exclude });
const FIELDS = [
  field("needs_sponsorship", "Will you need visa sponsorship (now or later)?", ["sponsorship", "sponsor", "visa status", "require a visa"], "Yes"),
  field("work_authorized", "Authorized to work in the U.S.?", ["authorized to work", "legally authorized", "eligible to work", "work authorization"], "Yes"),
  field("work_status", "Current work status (e.g. F-1, OPT)", ["immigration status", "current visa", "work permit"], "F-1 OPT"),
  field("state", "State", ["state", "province", "region"], "New York", ["statement", "status"]),
  field("phone", "Phone", ["phone", "mobile"], "315-555-0100", ["extension", "ext", "country", "code"]),
  field("full_name", "Full name", ["full name", "your name"], "Sam Doe"),
];
const ctx = (answers = []) => ({ fields: FIELDS, answers });
const value = (label, c = ctx()) => {
  const r = M.answerFor(label, c);
  return r.status === "matched" ? r.value : r.status;
};

test("sponsorship 'now or in the future' uses the profile field", () => {
  assert.equal(value("Will you, now or in the future, require sponsorship for employment?*"), "Yes");
  assert.equal(value("Do you require visa sponsorship?"), "Yes");
  assert.equal(value("Will you now or in the future require sponsorship to work in the United States?"), "Yes");
});

test("sponsorship questions that ask something narrower are left to you", () => {
  assert.equal(value("Do you currently require visa sponsorship?"), "yours");
  assert.equal(value("Will you require visa sponsorship in the future?"), "yours");
  assert.equal(value("Will you now or in the future require sponsorship to work in the United Kingdom?"), "yours");
  assert.equal(value("Are you able to work in the U.S. without requiring sponsorship?"), "yours");
  assert.equal(value("Has an employer previously sponsored you for an H-1B?"), "yours");
});

test("work authorization: U.S. only, and never falls through to another field", () => {
  assert.equal(value("Are you legally authorized to work in the United States?"), "Yes");
  assert.equal(value("Are you legally authorized to work in Canada?"), "yours");
  // Contains "states" but must not be answered with your state.
  assert.equal(value("Are you authorized to work in the United States without sponsorship?"), "yours");
  assert.equal(value("What is your current immigration status?"), "F-1 OPT");
});

test("ordinary questions keep keyword and near-match behaviour", () => {
  assert.equal(value("State*"), "New York");
  assert.equal(value("Phone Extension"), "none");
  assert.equal(value("Name"), "Sam Doe");
  const c = ctx([{ question: "Why are you interested in this role at our company?", answer: "Because data." }]);
  assert.equal(value("Why are you interested in this role at the company?", c), "Because data.");
});

test("saved answers: equivalent wordings match, near wordings don't for fail-closed topics", () => {
  const c = ctx([{ question: "Do you currently require sponsorship?", answer: "No" }]);
  assert.equal(value("Do you require visa sponsorship at this time?", c), "No");
  // 90% word overlap, different country: not your answer.
  const uk = ctx([{ question: "Will you now or in the future require sponsorship to work in the United States?", answer: "No" }]);
  assert.equal(value("Will you now or in the future require sponsorship to work in the United Kingdom?", uk), "yours");
  const g = ctx([{ question: "What is your gender?", answer: "Prefer not to say" }]);
  assert.equal(value("Gender*", g), "Prefer not to say");
  assert.equal(value("Gender identity", g), "yours");
});

test("saved answers that disagree are not resolved by order", () => {
  const c = ctx([
    { question: "Are you willing to relocate?", answer: "Yes" },
    { question: "Are you open to relocation?", answer: "No" },
  ]);
  assert.equal(value("Would you be willing to relocate?", c), "conflict");
  const same = ctx([
    { question: "Are you willing to relocate?", answer: "Yes" },
    { question: "Are you open to relocation?", answer: "yes" },
  ]);
  assert.equal(value("Willing to relocate", same), "Yes");
});

const option = (options, answer, label = "") => {
  const r = M.resolveOption(options, answer, label);
  return r.status === "matched" ? options[r.index] : r.status;
};

test("Yes/No never picks a qualified option", () => {
  assert.equal(option(["Yes", "No"], "YES"), "Yes");
  assert.equal(option(["Yes", "No"], "No, I do not"), "No");
  assert.equal(option(["Yes, but only with H-1B transfer", "No, I will not"], "Yes", "Will you require sponsorship?"), "unmatched");
  assert.equal(option(["Yes, I am authorized", "No"], "Yes"), "unmatched");
});

test("two equally good options are refused", () => {
  assert.equal(option(["Yes", "yes", "No"], "Yes"), "ambiguous");
  assert.equal(option(["LinkedIn Job Post", "LinkedIn Referral", "Indeed"], "LinkedIn"), "ambiguous");
  assert.equal(option(["LinkedIn Job Post", "Indeed"], "LinkedIn"), "LinkedIn Job Post");
});

test("fail-closed questions get exact or listed-equivalent options only", () => {
  assert.equal(option(["Citizen", "Permanent resident", "F-1 student visa (OPT)"], "F-1 OPT", "What is your visa status?"), "unmatched");
  assert.equal(option(["Female", "Male", "Decline to self-identify"], "Decline", "Gender"), "unmatched");
});

test("country names and placeholders", () => {
  assert.equal(option(["Canada", "United States of America (+1)"], "US", "Country"), "United States of America (+1)");
  assert.equal(option(["Select One", "Mobile", "Landline"], "Mobile"), "Mobile");
  assert.equal(option(["Select One", "Master's Degree", "Bachelor's Degree"], "Master of Science", "Highest degree earned"), "Master's Degree");
});

// Derived answers and company-generalised saved answers.
const job = { company: "Comcast Cable Communications, LLC", found_via: { kind: "company_site", name: "Comcast Cable Communications" } };
const history = { jobs: [{ company: "Syracuse University iSchool" }, { company: "Tata Consultancy Services" }] };
const withJob = (answers = []) => ({ fields: FIELDS, answers, job, history });

test("'How did you hear about us?' comes from where Job Hunter found the job", () => {
  const d = M.derived("How Did You Hear About Us?*", withJob());
  assert.equal(d.from, "where Job Hunter found this job");
  const opts = ["Select One", "Comcast Careers Website", "Employee Referral", "LinkedIn"];
  const first = d.values.find((v) => M.resolveOption(opts, v).status === "matched");
  assert.equal(opts[M.resolveOption(opts, first).index], "Comcast Careers Website");
});

test("an ambiguous candidate is not settled by picking the first option", () => {
  const opts = ["Company Website - Careers", "Company Website - Blog"];
  assert.equal(M.resolveOption(opts, "Company Website").status, "ambiguous");
});

test("'worked at the company before?' is No unless the company is in your work history", () => {
  const q = "Do you currently work or have you previously worked as a full/part time employee or as a contractor of Comcast or any Company within the Comcast Family?*";
  assert.deepEqual(M.derived(q, withJob()).values, ["No"]);
  const tcs = { ...withJob(), job: { company: "Tata Consultancy Services Limited" } };
  assert.deepEqual(M.derived("Have you ever worked for Tata Consultancy Services before?", tcs).values, ["Yes"]);
  assert.equal(M.derived("Do you have relatives currently working at Comcast?", withJob()), null);
  assert.equal(M.derived("Will you now or in the future require sponsorship?", withJob()), null); // fail-closed
});

test("a saved answer about one company answers the same question at another", () => {
  const saved = [{ question: "Do you have any relatives currently working for the company?", answer: "No" }];
  const r = M.answerFor("Do you have any relatives currently working for Comcast?", withJob(saved));
  assert.equal(r.status, "matched");
  assert.equal(r.value, "No");
});

test("no careers-site option: the closest is 'Other Job Site', never a named site", () => {
  const d = M.derived("How Did You Hear About Us?*", withJob());
  const opts = ["Glassdoor", "Google", "Hackajob", "Indeed", "Other Job Site", "Zip Recruiter"];
  const first = d.values.find((v) => M.resolveOption(opts, v).status === "matched");
  assert.equal(opts[M.resolveOption(opts, first).index], "Other Job Site");
});
