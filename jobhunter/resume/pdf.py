import os
import re
from fpdf import FPDF
from loguru import logger


PAGE_W = 215.9
PAGE_H = 279.4
MARGIN_TOP = 5
MARGIN_BOTTOM = 8
MARGIN_LEFT = 10
MARGIN_RIGHT = 10
BODY_FONT_SIZE = 10
HEADING_FONT_SIZE = 10
NAME_FONT_SIZE = 14
CONTACT_FONT_SIZE = 9
LINE_HEIGHT = 4.2

USABLE_HEIGHT = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM
MIN_FULLNESS = 0.85
MAX_FULLNESS = 1.0


class ResumePDF(FPDF):
    def __init__(self):
        super().__init__(format='letter')
        self.set_auto_page_break(auto=False)
        self.add_page()
        self.set_margins(MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT)
        self.set_y(MARGIN_TOP)


def sanitize_text(text: str) -> str:
    """Force ALL text to latin-1 safe characters."""
    replacements = {
        '\u2019': "'", '\u2018': "'", '\u201C': '"', '\u201D': '"',
        '\u2013': '-', '\u2014': '-', '\u2026': '...', '\u2022': '-',
        '\u00a0': ' ', '\u00b7': '-', '\u2032': "'", '\u2033': '"',
        '\u201a': ',', '\u201e': '"', '\u2039': '<', '\u203a': '>',
        '\u2010': '-', '\u2011': '-', '\u2012': '-',
        '\u0092': "'", '\u0093': '"', '\u0094': '"', '\u0091': "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.encode('latin-1', errors='replace').decode('latin-1')
    return text


def split_date(line):
    m = re.search(
        r'^(.*?)\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\s*[-]\s*(?:Present|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}))$',
        line, re.IGNORECASE
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.search(
        r'^(.*?)\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})$',
        line, re.IGNORECASE
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return line.strip(), None


def preprocess_lines(raw_lines):
    lines = [l.strip() for l in raw_lines]
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]; i += 1
        if not line:
            merged.append(""); continue
        if i < len(lines) and re.match(
            r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}', lines[i], re.I
        ) and not lines[i].startswith("-"):
            merged.append(f"{line}  {lines[i]}"); i += 1
        else:
            merged.append(line)
    return merged


def _build_pdf(resume_text: str, linkedin_url: str = "") -> ResumePDF:
    resume_text = sanitize_text(resume_text)
    pdf = ResumePDF()
    content_width = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT

    raw_lines = resume_text.strip().split("\n")
    lines = preprocess_lines(raw_lines)

    headings = {
        "SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS",
        "CERTIFICATIONS", "OBJECTIVE", "ACHIEVEMENTS", "LANGUAGES",
        "PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE", "TECHNICAL SKILLS",
        "CONTACT", "PROFILE", "AWARDS", "VOLUNTEER", "PUBLICATIONS"
    }

    found_name = False
    found_contact = False

    for idx, line in enumerate(lines):
        if not line:
            continue
        cl = line.upper().rstrip(":").strip()

        # NAME
        if not found_name:
            pdf.set_font("Helvetica", "B", NAME_FONT_SIZE)
            pdf.cell(content_width, 6, line.upper(), align="C", new_x="LMARGIN", new_y="NEXT")
            found_name = True
            continue

        # CONTACT LINE
        if not found_contact and any(c in line for c in ["|", "@", "linkedin", "+1"]):
            parts = [x.strip() for x in line.split("|")]
            pdf.set_font("Helvetica", "", CONTACT_FONT_SIZE)
            contact_parts = []
            for part in parts:
                if "linkedin" in part.lower():
                    contact_parts.append("LinkedIn")
                else:
                    contact_parts.append(part)
            contact_str = " | ".join(contact_parts)
            w = pdf.get_string_width(contact_str)
            x_start = (PAGE_W - w) / 2
            pdf.set_x(x_start)
            for j, part in enumerate(parts):
                if j > 0:
                    pdf.set_font("Helvetica", "", CONTACT_FONT_SIZE)
                    pdf.cell(pdf.get_string_width(" | "), LINE_HEIGHT, " | ")
                if "@" in part and "linkedin" not in part.lower():
                    pdf.set_text_color(5, 99, 193)
                    pdf.set_font("Helvetica", "U", CONTACT_FONT_SIZE)
                    pdf.cell(pdf.get_string_width(part), LINE_HEIGHT, part, link=f"mailto:{part}")
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("Helvetica", "", CONTACT_FONT_SIZE)
                elif "linkedin" in part.lower():
                    pdf.set_text_color(5, 99, 193)
                    pdf.set_font("Helvetica", "U", CONTACT_FONT_SIZE)
                    pdf.cell(pdf.get_string_width("LinkedIn"), LINE_HEIGHT, "LinkedIn",
                             link=linkedin_url if linkedin_url else "")
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("Helvetica", "", CONTACT_FONT_SIZE)
                else:
                    pdf.cell(pdf.get_string_width(part), LINE_HEIGHT, part)
            pdf.ln(LINE_HEIGHT + 1)
            found_contact = True
            continue

        # SECTION HEADINGS
        if cl in headings:
            pdf.ln(1.5)
            pdf.set_font("Helvetica", "B", HEADING_FONT_SIZE)
            pdf.cell(content_width, LINE_HEIGHT + 1, line.upper(), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(0, 0, 0)
            pdf.line(MARGIN_LEFT, pdf.get_y(), PAGE_W - MARGIN_RIGHT, pdf.get_y())
            pdf.ln(0.5)
            continue

        # BULLETS
        if line.startswith(("- ", "* ")) and len(line) > 2:
            txt = line[2:].strip()
            pdf.set_font("Helvetica", "", BODY_FONT_SIZE)
            bullet_x = MARGIN_LEFT + 4
            text_x = MARGIN_LEFT + 7
            pdf.set_x(bullet_x)
            pdf.cell(3, LINE_HEIGHT, "-")
            pdf.set_x(text_x)
            text_width = content_width - 7
            pdf.multi_cell(text_width, LINE_HEIGHT, txt)
            continue

        # DATE LINES
        left, date = split_date(line)
        if date:
            pdf.ln(0.8)
            pdf.set_font("Helvetica", "B", BODY_FONT_SIZE)
            pdf.cell(0, LINE_HEIGHT, left)
            pdf.set_font("Helvetica", "", BODY_FONT_SIZE)
            date_w = pdf.get_string_width(date)
            pdf.set_x(PAGE_W - MARGIN_RIGHT - date_w)
            pdf.cell(date_w, LINE_HEIGHT, date)
            pdf.ln(LINE_HEIGHT)
            continue

        # SUB-HEADERS
        next_l = lines[idx + 1] if idx + 1 < len(lines) else ""
        if next_l.startswith(("- ", "* ")):
            pdf.ln(0.3)
            pdf.set_font("Helvetica", "B", BODY_FONT_SIZE)
            pdf.cell(content_width, LINE_HEIGHT, line, new_x="LMARGIN", new_y="NEXT")
            continue

        # REGULAR TEXT
        pdf.set_font("Helvetica", "", BODY_FONT_SIZE)
        pdf.multi_cell(content_width, LINE_HEIGHT, line)

    return pdf


def get_page_count(resume_text: str, linkedin_url: str = "") -> int:
    pdf = _build_pdf(resume_text, linkedin_url)
    return pdf.pages_count


def get_page_fullness(resume_text: str, linkedin_url: str = "") -> float:
    pdf = _build_pdf(resume_text, linkedin_url)
    final_y = pdf.get_y()
    return (final_y - MARGIN_TOP) / USABLE_HEIGHT


def check_resume_fit(resume_text: str, linkedin_url: str = "") -> dict:
    pdf = _build_pdf(resume_text, linkedin_url)
    pages = pdf.pages_count
    final_y = pdf.get_y()
    fullness = (final_y - MARGIN_TOP) / USABLE_HEIGHT

    if pages > 1 or fullness > MAX_FULLNESS:
        return {"status": "too_long", "pages": pages, "fullness": fullness,
                "fill_percent": round(fullness * 100, 1)}
    elif fullness < MIN_FULLNESS:
        return {"status": "too_short", "pages": pages, "fullness": fullness,
                "fill_percent": round(fullness * 100, 1)}
    else:
        return {"status": "perfect", "pages": pages, "fullness": fullness,
                "fill_percent": round(fullness * 100, 1)}


def validate_content(resume_text: str, required_sections: list = None) -> dict:
    if required_sections is None:
        required_sections = ["SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS", "CERTIFICATIONS"]
    text_upper = resume_text.upper()
    missing = [s for s in required_sections if s not in text_upper]
    return {"valid": len(missing) == 0, "missing_sections": missing}


def format_to_pdf(resume_text: str, output_path: str, linkedin_url: str = "") -> str:
    pdf = _build_pdf(resume_text, linkedin_url)
    fullness = (pdf.get_y() - MARGIN_TOP) / USABLE_HEIGHT

    if pdf.pages_count > 1 or fullness > MAX_FULLNESS:
        logger.warning(f"Resume overflows ({round(fullness * 100, 1)}% full) - NOT saving.")
        return ""

    logger.info(f"Resume: 1 page, {round(fullness * 100, 1)}% full")

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    pdf.output(output_path)
    logger.info(f"ATS resume saved to: {output_path}")
    return output_path