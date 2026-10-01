from pathlib import Path

from jobhunter.resume.pdf import check_resume_fit
from jobhunter.resume.tailor import ResumeTailor, build_prompt, missing_items, trim_to_one_page

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


def test_trim_cuts_at_word_boundary():
    trimmed = trim_to_one_page(resume(bullets=28, words=40))
    for line in trimmed.splitlines():
        if line.startswith("- "):
            assert line.endswith(".") and "  " not in line


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
