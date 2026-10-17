from pathlib import Path

from jobhunter.resume.pdf import check_resume_fit
from jobhunter.resume.tailor import ResumeTailor, build_prompt, drop_unsupported_skills, missing_items, trim_to_one_page

HEADER = "JANE DOE\nNew York, NY | jane@example.com | LinkedIn\n"


def resume(bullets: int, words: int = 25) -> str:
    bullet = "- " + " ".join(["Analyzed"] + ["data"] * (words - 1)) + "."
    body = "\n".join([bullet] * bullets)
    return f"{HEADER}SUMMARY\nAnalyst.\nEXPERIENCE\nAcme | Analyst  Jan 2022 - Dec 2024\n{body}\nPROJECTS\nProject Alpha\n- Built it."


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else ""


def test_prompt_lists_must_keep_items():
    prompt = build_prompt("base", "jd", ["Project Alpha", "Cert Beta"])
    assert "Project Alpha" in prompt and "Cert Beta" in prompt
    assert "never invent" in prompt


def test_missing_items_is_case_insensitive():
    assert missing_items("built PROJECT ALPHA", ["Project Alpha", "Cert Beta"]) == ["Cert Beta"]


def test_trim_makes_overflowing_resume_fit():
    # Slightly over a page, the realistic case when the LLM writes a bit too much.
    long_text = resume(bullets=28, words=40)
    assert check_resume_fit(long_text)["status"] == "too_long"
    trimmed = trim_to_one_page(long_text)
    assert check_resume_fit(trimmed)["status"] != "too_long"
    # Only body lines were shortened; headings and the contact line are untouched.
    assert trimmed.startswith(HEADER.strip())
    assert "EXPERIENCE" in trimmed and "Project Alpha" in trimmed


def test_trim_drops_whole_bullets_never_part_of_a_sentence():
    original = resume(bullets=28, words=40)
    full_bullets = {line for line in original.splitlines() if line.startswith("- ")}
    trimmed = trim_to_one_page(original)
    kept = [line for line in trimmed.splitlines() if line.startswith("- ")]
    assert kept and all(line in full_bullets for line in kept)


def test_trim_drops_least_relevant_projects_but_not_must_keep():
    text = (f"{HEADER}SUMMARY\nAnalyst.\nEXPERIENCE\nAcme | Analyst  Jan 2022 - Dec 2024\n"
            + "\n".join([resume(1, 40).splitlines()[-4]] * 2)
            + "\nPROJECTS\nProject Alpha\n- Built it.\n"
            + "\n".join(f"Project {i}\n- " + "Analyzed data " * 30 for i in range(12)))
    trimmed = trim_to_one_page(text, must_keep=["Project Alpha"])
    assert check_resume_fit(trimmed)["status"] != "too_long"
    assert "Project Alpha" in trimmed and "Project 11" not in trimmed


def test_unsupported_skills_are_removed():
    base = "SKILLS\n- BI: Tableau, Excel, SQL"
    text = "SKILLS\n- BI & Visualization: Tableau (dashboards), Power BI familiarity, Excel\nEXPERIENCE\nUses Power BI."
    out = drop_unsupported_skills(text, base)
    assert "Tableau (dashboards)" in out and "Excel" in out
    assert "Power BI familiarity" not in out.split("EXPERIENCE")[0]


def test_cover_note_sign_off_on_its_own_lines():
    from jobhunter.resume.cover import clean_note

    note = clean_note("Dear Hiring Team,\n\nI'd love to help. Best regards, Jane Doe")
    assert note.endswith("I'd love to help.\n\nBest regards,\nJane Doe")


def test_retries_when_must_keep_item_dropped():
    good = resume(bullets=38)
    llm = FakeLLM(good.replace("Project Alpha", "Something"), good)
    text = ResumeTailor("base", llm, must_keep=["Project Alpha"]).tailor_text("jd")
    assert len(llm.prompts) >= 2
    assert "Project Alpha" in text


def test_tailor_writes_one_page_pdf(tmp_path: Path):
    llm = FakeLLM(*[resume(bullets=38)] * 3)
    path = ResumeTailor("base", llm, output_dir=str(tmp_path)).tailor("jd", "Acme Corp", "Data Analyst")
    assert path and Path(path).exists()
    assert Path(path).name.startswith("Acme_Corp_Data_Analyst_")


def test_empty_llm_response_returns_nothing(tmp_path: Path):
    assert ResumeTailor("base", FakeLLM(""), output_dir=str(tmp_path)).tailor("jd", "A", "B") == ""


BASE = """JANE DOE
New York, NY | jane@example.com
EXPERIENCE
Acme Bank | Analyst  Jan 2022 - Dec 2024
- Processed 2M+ daily transactions and cut reporting time by 60%.
PROJECTS
Jobs Tracker | Python
- Built a warehouse with 32 data tests."""


def test_header_is_restored():
    from jobhunter.resume.tailor import ensure_header

    out = ensure_header("SUMMARY\nAnalyst.", BASE)
    assert out.splitlines()[:2] == ["JANE DOE", "New York, NY | jane@example.com"]
    assert ensure_header(BASE, BASE) == BASE


def test_misplaced_claims():
    from jobhunter.resume.tailor import misplaced_claims

    assert misplaced_claims(BASE, BASE) == []
    moved = BASE.replace("cut reporting time by 60%.", "added 32 data tests.")
    [problem] = misplaced_claims(moved, BASE)
    assert "Acme Bank" in problem and "32" in problem
