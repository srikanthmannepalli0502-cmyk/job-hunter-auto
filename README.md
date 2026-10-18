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
    DB --> T[Tailor resume<br/>Llama 3.3 70B via Groq]
    T --> P[One-page PDF<br/>fit check + auto-trim]
    DB --> O[Open posting in browser]
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
python -m jobhunter discover-boards --top 300 --min-filings 10   # once, then occasionally
python -m jobhunter search                                        # includes the boards
```

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

1. **Gather (automatic):** a Windows scheduled task runs `jobhunter run` at 10:00 and 18:00. Each
   run reads every source, filters and scores the postings, stores new jobs, refreshes the
   `last seen` date of jobs found again (so nothing is duplicated) and marks jobs that haven't
   been seen for 3 days as closed. Set it up once:

   ```bash
   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
   ```

   If the PC is off or asleep at a run time, Windows runs it as soon as it's back.
   Output goes to `logs\scheduled.log`; `python -m jobhunter runs` lists recent runs and any
   source that failed.

2. **Review (you):** `python -m jobhunter review` opens a page in your browser (served only to
   this PC at http://127.0.0.1:8765) with the open jobs ranked by fit + H-1B sponsorship.
   Shortlist or skip each one; skipped jobs stay hidden even when later runs find them again.

3. **Prepare (one click):** on a job's page, *Prepare kit* writes a tailored one-page resume
   (from your master resume) and a short cover note to `resumes/tailored/`.

4. **Apply (you):** *Open posting*, apply on the company's site, then *Mark applied*. The
   application records which resume and cover note you sent; update its status later on the
   *Applications* tab.

Job Hunter never submits applications for you: career sites need accounts, screening
questions and sometimes assessments, and only you should send anything in your name.

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
```

### Where everything is kept

| What | Where |
|---|---|
| Jobs, scores, your decisions, applications (with the resume and cover note sent), runs | `jobhunter.db` (SQLite) |
| Tailored resumes and cover notes | `resumes/tailored/` |
| Your master resume and profile | `resumes/master_resume.txt`, `profile.toml` |
| Run logs | `logs/` |

All of these are gitignored and stay on your PC.

### Resume quality checks

Tailoring asks the LLM for a one-page version of your master resume, then checks it: the name
and contact lines are restored if dropped, skills not in your master resume are removed,
every number in a bullet must appear in the same job or project of the master resume, and a
page that overflows is rewritten shorter (whole bullets are dropped only as a last resort).
Anything left is printed as a CHECK line. **Always read the resume and cover note before
sending them.**

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
├── web.py              # local review page
├── sources/
│   ├── base.py         # JobSource interface, dedupe, exclusions
│   ├── adzuna.py       # Adzuna API
│   ├── boards.py       # Greenhouse, Lever, Ashby, Workday, Oracle, SmartRecruiters, Amazon + discovery
│   └── remotive.py     # Remotive API
└── resume/
    ├── parser.py       # read PDF/DOCX/TXT resumes
    ├── pdf.py          # ATS-friendly PDF rendering + page-fit check
    ├── tailor.py       # prompt, quality checks, one-page fitting
    └── cover.py        # cover note
scripts/                # run_scheduled.cmd + install_schedule.ps1 (Windows Task Scheduler)
tests/                  # pytest suite (APIs mocked, LLM faked); runs in GitHub Actions
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The tests mock the job APIs and the LLM, so they need no keys or network access.

## Responsible use

- Job data comes only from official APIs, used within their terms. Remotive asks API users to
  link back to postings and credit Remotive as the source; please do.
- No automated logins, scraping or auto-submission. Applying stays a human decision.
- Groq's free tier allows a limited number of requests per day; tailoring retries on rate limits.

## License

For personal and educational use.
