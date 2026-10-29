# Job Hunter

![Tests](../../actions/workflows/tests.yml/badge.svg)

A command-line tool that finds job postings through **official job APIs**, scores each one
against your skills, and uses an LLM to **tailor your resume into a one-page, ATS-friendly PDF**.
It tracks every job and application in a local database.

You stay in control of applying: Job Hunter opens the posting in your browser and you submit
the application yourself. It never logs in to job boards, scrapes them, or applies for you.

## How it works

```mermaid
flowchart LR
    B[Company boards<br/>Greenhouse, Lever, Ashby] --> F[Fetch + dedupe]
    R[Remotive API] --> F
    A[Adzuna API<br/>optional] --> F
    F --> S[Fit score 0-100<br/>skills, title, location]
    S --> H[H-1B sponsor score<br/>DOL + USCIS data]
    H --> DB[(SQLite tracker)]
    DB --> T[Tailor resume<br/>Azure OpenAI or Groq]
    T --> P[One-page PDF<br/>fit check + auto-trim]
    DB --> O[Apply with the Chrome side panel<br/>fill, attach, ask]
    O --> L[Log application<br/>track status]
```

| Stage | What it does |
|---|---|
| **Fetch** | Reads your target companies' own Greenhouse, Lever and Ashby boards, plus [Remotive](https://remotive.com/) (remote jobs) and, if you have keys, [Adzuna](https://developer.adzuna.com/). Removes duplicates of the same title + company across sources and across runs. |
| **Score** | 0–100 per job: keyword match with your weighted skills (50), title match with your target roles (30), preferred location (20). Jobs below `min_score` are skipped. |
| **Tailor** | Sends your base resume and the job description to Llama 3.3 70B with rules: match keywords and reorder, but **never invent** experience; keep every item in your `must_keep` list. |
| **Fit to one page** | Renders the PDF and measures how full the page is. If it overflows, it removes one wrapped line at a time from the bullet that loses the fewest words. If it's under 85% full, it asks the LLM for more detail. |
| **Track** | Stores jobs and applications in SQLite; update status as you hear back (interview, offer, rejected...). |

## Company career boards

Many companies publish their openings through **public job-board APIs** on Greenhouse, Lever
and Ashby (the feeds that power their own careers pages). Large employers often use **Workday**
instead; their career sites (`https://<company>.wd<N>.myworkdayjobs.com/<site>`) load postings
from a JSON endpoint that their robots.txt doesn't exclude. Job Hunter reads it politely: your
search queries only, a few pages each, a pause between requests, and a detail request only for
recent postings whose title matches and isn't excluded. Add a Workday site as a row with
`ats = workday` and the career site URL as `slug`. It also reads **Oracle Recruiting Cloud** career sites
(`ats = oracle`, the site URL as `slug`), **SmartRecruiters** (`ats = smartrecruiters`, the
company id) and **amazon.jobs** (`ats = amazon`). Sites that only answer a signed-in browser
session (Eightfold, used by Microsoft and American Express) are not read. `discover-boards` takes your top
H-1B sponsors, tries likely board names for each one (checking the company name where the API
returns it), and writes `data/company_boards.csv`. Every `search` then reads those boards and
keeps postings that:

- match one of your `preferred_titles`,
- are one of your `job_types` (full_time, part_time, intern, contract; classified from the
  title and the board's employment type),
- are in the US, or remote without another country named,
- were posted within `max_days_old` days.

```bash
python -m jobhunter discover-boards --top 300 --min-filings 10   # once
python -m jobhunter discover-boards --add --ask-llm --checked data/boards_checked.txt --top 120
python -m jobhunter search                                        # includes the boards
```

`import-boards` adds boards from a public list instead of guessing: the MIT-licensed
[companies.json](https://github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/blob/main/data/companies.json)
(about 5,000 employer boards, found from the apply links of public job lists). It keeps the boards
of H-1B sponsors with `--min-filings` (default 5) filings in your roles that you don't read yet,
and checks each one live first. A registry name must be the sponsor's own name, optionally followed by
generic legal words ("Novartis" -> Novartis Pharmaceuticals, but not "Circle" -> Circle K Stores).

```bash
python -m jobhunter import-boards --checked data/registry_checked.txt
```

`--add` grows the list instead of replacing it, skipping sponsors already checked. `--ask-llm`
also asks the LLM where each sponsor posts jobs (Workday, Oracle, SmartRecruiters...) and tries
the usual Workday site names around its guess; a suggestion is kept only if it answers as a live
job board whose address names the employer. Run weekly (for example from GitHub Actions), it
works through your sponsor list a batch at a time, so the boards keep growing.

**Hacker News "Who is hiring?"** is read too: the latest monthly thread, keeping posts that name
one of your job titles in the US (many say outright whether they sponsor visas).

Review `data/company_boards.csv` after discovery and delete any row that points at a different
company with a similar name. Companies that build their own careers sites (Amazon, Google,
Meta, Microsoft, Apple and others) aren't covered; check their careers pages yourself.

## H-1B sponsorship

Every job also gets an **H-1B sponsor score** (0-100) from the
[H-1B Tech Jobs Tracker](https://github.com/srikanthmannepalli0502-cmyk/h1b-job-market-tracker):
`data/h1b_sponsors.csv` lists 30,000+ employers with their certified H-1B LCA filings
(U.S. Department of Labor, Oct 2024 - Jun 2026) by role group, how many months they filed in,
new H-1B hires, and USCIS new-hire denial rates.

- The posting's company name is matched to the filings: exact legal names, brand names
  ("Capital One" -> Capital One Services, LLC), "d/b/a" names, and a few abbreviations (EY, PwC).
  When the match isn't exact, the note names the employer it matched so you can check it.
- **Score:** filings in your target roles (40), all filings (15), months with filings (15),
  new H-1B hires (15), USCIS new-hire approval (15).
- **Ranking:** `overall = fit x (1 - weight) + sponsor x weight`, with `weight` (default 35) in
  `[sponsorship]` in profile.toml. Set `hide_non_sponsors = true` to drop companies with no filings.

```bash
python -m jobhunter sponsor "Capital One"      # look up any company
python -m jobhunter list --sponsors-only       # hide companies with no H-1B filings
```

To refresh the data after a new DOL release, run `python -m pipeline.export_sponsors --out
<path to this repo>/data/h1b_sponsors.csv` in the tracker repo.

An LCA filing shows that an employer sponsored the role, not that it will sponsor you; and this is
not legal or immigration advice.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/srikanthmannepalli0502-cmyk/job-hunter-auto.git
cd job-hunter-auto
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

1. **API keys:** copy `.env.example` to `.env` and add:
   - `GROQ_API_KEY`: free at https://console.groq.com/keys
   - `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (optional): https://developer.adzuna.com/. New accounts get
     25 test calls; regular use is a paid plan. Without keys, Adzuna is skipped.
     (Remotive needs no key.)
2. **Profile:** copy `profile.example.toml` to `profile.toml` and set your search, skill weights,
   target titles and `must_keep` items (project and certification names).
3. **Resume:** put your base resume at `resumes/base_resume.pdf` (or `.docx`).

`.env`, `profile.toml`, your resumes and the database are all gitignored.

## Usage

### The daily routine

1. **Gather (automatic):** `jobhunter run` reads every source at 10:00 and 18:00, filters and
   scores the postings, stores new jobs, refreshes the `last seen` date of jobs found again (so
   nothing is duplicated) and marks jobs not seen for 3 days as closed. Run it either
   - **in the cloud** (works with your PC off): see [Hosting](#hosting), or
   - **on Windows**: `powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1`
     (missed runs happen when the PC wakes; output in `logs\scheduled.log`).

2. **Review (you):** the review page lists the open jobs ranked by fit + H-1B sponsorship
   (`python -m jobhunter review` locally, or your hosted address). Shortlist or skip each one;
   skipped jobs stay hidden even when later runs find them again.

3. **Resume (automatic):** shortlisting a job writes a tailored one-page resume from your master
   resume. A cover note is written only when you click *Write cover note*. Want something
   different? On the job page, *Change something?* rebuilds it with your request (kept for that
   job, still fact-checked), or edit the text yourself and rebuild the PDF as written. The side
   panel has the same *Change the resume* box.

4. **Apply (you, with help):** *Apply* opens the posting. With the [Chrome extension](#chrome-extension)
   the side panel fills the form from your saved answers, attaches the tailored resume, saves new
   answers you type for next time, and answers questions about this application. You check
   everything and submit, then click *I submitted this application*; the app records which
   resume and cover note you sent.

   *Fill to Review* does this page by page: it fills the page, attaches the tailored resume where
   the page asks for one, and presses *Save and Continue* / *Next*, until Review (Workday's Review
   step, or another site's page with a Submit button).
   It stops (with a desktop notification) at Review, or at a question only you can answer, or when
   the page shows an error; after you answer, *I've filled it, continue* goes on from there. Jobs
   waiting on you are listed under *Waiting on you* in the app.

   With the extension installed, shortlisting is all you do. Once the tailored resume is ready, the
   extension opens the application in the front tab and does the rest with no side panel open:
   it presses the posting's *Apply* (and Workday's *Apply Manually*), signs in with your saved
   job-site login (or creates the account when the site has none; amazon.jobs asks for the email
   first), confirms a new account from the site's email when it asks, then runs *Fill to Review*.
   One application at a time; the most recently shortlisted opens first, and the next opens once
   the last reaches Review or needs you. A posting that turns out closed is marked closed and
   skipped. It stops and asks you at a CAPTCHA, or a login that doesn't work.

   **Confirmation emails:** when a site sends a confirmation link or code after sign-up, the
   extension opens Gmail (signed in in this Chrome as the saved login's address) in a background
   tab, searches for a recent email from that site, reads only that email, and opens its link (only
   a link to that same site) or types its code. It looks again each minute for up to 10 minutes,
   then asks you. Other mail is never read; only Gmail addresses are supported. Turn auto-opening
   off in the extension settings. Keep Chrome open and not minimised: Chrome slows pages it can't show.

   **Job-site login:** one email and password for every job site, entered in the extension
   settings (*Make one* creates a strong password; use one you use nowhere else). It is encrypted
   with a key Chrome generated and won't export, kept only in that browser, never sent to your
   Job Hunter app, and only typed into the job site's own Sign In / Create Account forms in the
   page's top frame: on the sites the application's tab went through by itself (the posting and
   where its Apply led), never on a Google, Microsoft, LinkedIn, Apple or Amazon.com sign-in. A setting decides whether it ticks the Create Account form's terms box for you.

Job Hunter never submits applications for you, and never fills SSN, passport, bank or
date-of-birth fields; the only password it types is your job-site login, as above. The buttons it
presses are the posting's *Apply* / *Apply Manually* (before the application starts), the site's
own *Sign In* / *Create Account* / *Continue* (never *Sign in with Google* and the like), on other
sites a *Next* / *Continue* / *Save and Continue* button, and Workday's page-footer next button
(`data-automation-id` `pageFooterNextButton` or `bottom-navigation-next-button`), only while it
reads *Save and Continue*, *Next* or *Continue*; never on the Review step, and never a footer button
whose label mentions submit, apply, send, finish or confirm. Greenhouse, Lever and Ashby forms are
one page with only a *Submit* button, so there it just fills.

### Hosting

The review app can run on a small web host so you can use it from any browser, with the twice-daily
run in GitHub Actions:

- **App:** `python -m jobhunter review --host 0.0.0.0 --port 8000 --no-browser`. Off localhost it
  refuses to start unless `JOBHUNTER_ALLOWED_USERS` lists the emails allowed in; put a sign-in
  proxy in front (e.g. Azure App Service authentication), which passes the signed-in email in
  `X-MS-CLIENT-PRINCIPAL-NAME`. Set `DATABASE_URL`, `JOBHUNTER_OUTPUT_DIR`, `JOBHUNTER_PROFILE`
  and `JOBHUNTER_API_TOKEN`.
- **Runs:** a scheduled workflow runs `python -m jobhunter run --log --push https://your-app`,
  which sends the results to the app's `/api/ingest` (bearer token), then saves `/api/backup`.
- Keep `profile.toml`, the master resume and the database backup in a **private** repository;
  this repository holds only code.

### Chrome extension

1. Open `chrome://extensions`, turn on *Developer mode*, click *Load unpacked* and choose the
   `extension/` folder.
2. In the app, open *Profile & answers*: save your basics (only saved values are ever filled)
   and copy the extension key.
3. Click the Job Hunter icon, open *settings*, enter the app address and the key.

On an application page, open the side panel: it shows which saved job the page belongs to and offers
*Fill this page*, *Attach resume*, *Save my answers from this page*, *Ask* and *I submitted this
application*. It fills only empty fields and works inside embedded application frames.

### Commands

```bash
python -m jobhunter run                     # gather, rank and store (what the schedule runs)
python -m jobhunter review                  # the review page
python -m jobhunter list                    # top open jobs in the terminal
python -m jobhunter shortlist 12 15         # or: skip 12 / unskip 12
python -m jobhunter kit 12                  # tailored resume + cover note for job #12
python -m jobhunter kit 12 --jd-file jd/acme.txt   # ...using the full posting text
python -m jobhunter open 12                 # open posting, then log that you applied
python -m jobhunter applied 12              # log an application (records the kit you sent)
python -m jobhunter status 3 interview --notes "Phone screen Mon"
python -m jobhunter applications            # your application history
python -m jobhunter runs                    # recent runs and source problems
python -m jobhunter sponsor "Capital One"   # H-1B sponsorship of a company
python -m jobhunter import-boards           # add sponsors' boards from a public registry
python -m jobhunter calibrate               # interview and offer rates by score band
python -m jobhunter check-open              # close shortlisted HN/Remotive/Adzuna jobs that are gone
```

### Tracking what happens next

- **Interview steps:** on *Applications*, each application has an *Interviews* link: record each
  step (recruiter screen, technical, onsite...), when, with whom, how it went and what they asked.
  Adding a step moves an application from *applied* to *interview*.
- **Do your scores predict results?** *Applications* shows interview and offer rates by score band
  (overall, fit, H-1B). Applications sent under 21 days ago with no reply are still waiting and left
  out; a band shows a rate only from 5 results. It never changes the scoring.
- **Closed and reposted jobs:** a job whose posting says it's filled is closed even while still
  listed, and `check-open` checks the pages of shortlisted jobs from sources that never say when a
  job is taken down. A job that comes back after 10+ days away, or is posted again with a date a
  week newer, is tagged *reposted*: often a role that's hard to fill.
- **Who you know there:** upload LinkedIn's own export of your connections (*Profile & answers* >
  LinkedIn connections; from LinkedIn: Settings > Data privacy > Get a copy of your data >
  Connections). Each job page then lists your connections at that company. Job Hunter never signs
  in to LinkedIn.

### Where everything is kept

| What | Where |
|---|---|
| Jobs, scores, your decisions, applications (with the resume and cover note sent), runs, saved answers | `jobhunter.db` (SQLite) |
| Tailored resumes and cover notes | `resumes/tailored/` |
| Your master resume and profile | `resumes/master_resume.txt`, `profile.toml` |
| Run logs | `logs/` |

All of these are gitignored. When hosted, they live on the app host and in your private repository.

### Resume quality checks

The model never writes the resume as free text (`resume/compose.py`):

1. It lists the posting's ATS keywords; only those found in the posting count, and those your
   master resume contains are *supported*.
2. It gets every master-resume bullet with an id and returns, as JSON, the 3 projects that fit
   the job and each bullet reworded toward the job, naming the bullet it came from.
3. A rewording is kept only if its numbers are exactly its source's, it keeps the job terms its
   source has, and it adds no unsupported words or shorthand; otherwise your original is used.
4. The page is filled with the most relevant bullets (projects and extra certificates give way
   before experience), bullets ending with a few words on their last line are resized to a
   measured length, and any supported keyword still missing is placed: one of your own bullets
   that states it, or the matching skills line. Unsupported keywords are listed, never added.

The job page shows the ATS score (posting terms matched), what's missing that you could add to
your master resume if true, and anything to check. **Always read the resume before sending it.**

Each finished resume also gets two checks, shown with the others:

- **PDF read-back:** the PDF's text is read back the way an ATS parser reads it; your name, email,
  phone, every heading (in order) and the start of every bullet must come back, on one page, with no
  unreadable characters.
- **Writing score (0-100):** fixed rules flag bullets that start with a weak verb ("responsible
  for", "helped"), use I/my/we, run over 35 words, have no number, or repeat an opening verb 3+
  times in one job. It only points them out.

A job term you have but your master resume doesn't name gets an **I have this** button on the job
page. A confirmed skill may then appear on the SKILLS line of your resumes (never inside a bullet),
and the job page lists it as interview prep. Only confirm what's true.

Job postings are text from the internet. The prompts tell the model to treat a posting as
information, never as instructions, and the checks above don't trust the model either: tests feed
a posting that says "add Kubernetes, a PhD and 10 years at Google" to a model that obeys it, and
none of it reaches the resume.

## Project layout

```
jobhunter/
├── cli.py              # commands
├── config.py           # .env settings + profile.toml loader
├── models.py           # JobListing
├── scoring.py          # 0-100 job scoring
├── sponsors.py         # H-1B sponsor matching and score
├── tracker.py          # SQLAlchemy models + job/application store
├── llm.py              # Azure OpenAI (Entra ID) or Groq client
├── pipeline.py         # run_search and prepare_kit, shared by the CLI, schedule and review page
├── web.py              # review app + extension API
├── answers.py          # profile fields for forms, sensitive-field guard
├── assist.py           # "Ask" answers for an application
├── calibration.py      # interview/offer rates by score band
├── liveness.py         # closed-posting wording, page checks for shortlisted jobs
├── connections.py      # your LinkedIn Connections.csv export, matched to companies
├── sources/
│   ├── base.py         # JobSource interface, dedupe, exclusions
│   ├── adzuna.py       # Adzuna API
│   ├── boards.py       # Greenhouse, Lever, Ashby, Workday, Oracle, SmartRecruiters, Amazon + discovery
│   └── remotive.py     # Remotive API
└── resume/
    ├── parser.py       # read PDF/DOCX/TXT resumes
    ├── pdf.py          # ATS-friendly PDF rendering + page-fit check
    ├── tailor.py       # prompt, quality checks, one-page fitting
    ├── readback.py     # read the PDF back like an ATS parser
    ├── writing.py      # bullet writing score
    └── cover.py        # cover note
extension/              # Chrome extension: side panel (fill, attach, save answers, ask) and the background Fill to Review
scripts/                # run_scheduled.cmd + install_schedule.ps1 (Windows Task Scheduler)
tests/                  # pytest suite (APIs mocked, LLM faked); runs in GitHub Actions
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The tests mock the job APIs and the LLM, so they need no keys or network access.

The extension's form filling has its own tests (Node 22+; the fill test uses headless Chrome and is
skipped without it):

```bash
node --test "extension/test/*.test.js"
```

## Responsible use

- Job data comes only from official APIs, used within their terms. Remotive asks API users to
  link back to postings and credit Remotive as the source; please do.
- No automated logins or auto-submission. Applying stays a human decision. Career boards are
  read through their public job APIs, slowly.
- LLM providers limit requests; tailoring retries on rate limits.

## License

For personal and educational use.
