"""Deterministic resume analysis: everything that must NOT depend on the LLM
(see design principle: LLMs reason/explain, they don't own critical calculations)."""

import re
from dataclasses import dataclass, field

from app.services.experience_estimator import estimate_total_experience_years
from app.services.skill_extractor import extract_skills

ACTION_VERBS = {
    "led", "built", "designed", "developed", "implemented", "launched", "created",
    "managed", "architected", "optimized", "improved", "increased", "reduced",
    "delivered", "drove", "spearheaded", "established", "automated", "streamlined",
    "orchestrated", "engineered", "deployed", "migrated", "scaled", "mentored",
    "coordinated", "negotiated", "analyzed", "researched", "presented", "authored",
}

DEGREE_KEYWORDS = [
    "bachelor", "b.s.", "bs ", "b.a.", "ba ", "master", "m.s.", "ms ", "m.a.",
    "ma ", "mba", "phd", "ph.d.", "doctorate", "associate",
]

CERTIFICATION_KEYWORDS = [
    "certified", "certification", "certificate", "pmp", "cpa", "cfa", "scrum master",
    "aws certified", "azure certified", "google cloud certified", "cissp", "comptia",
]

_BULLET_RE = re.compile(r"^[\s]*[•\-\*•]\s*(.+)$", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(\+?\d{1,3}[\s.-]?)?(\(?\d{3}\)?[\s.-]?){2}\d{4}")
_BULLET_GLYPH_RE = re.compile(r"^[ \t]*[•\-\*]\s+", re.MULTILINE)
_DATE_RANGE_RE = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}"
    r"|\b\d{1,2}/\d{4}\b"
    r"|\b\d{4}\s*[-–—]\s*(?:\d{4}|present|current)\b",
    re.IGNORECASE,
)

REQUIRED_SECTIONS = ["summary", "experience", "education", "skills"]


@dataclass
class ResumeAnalysis:
    section_scores: dict[str, int] = field(default_factory=dict)
    weak_sections: list[str] = field(default_factory=list)
    ats_score: int = 0
    resume_score: int = 0
    total_experience_years: float = 0.0
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    action_verb_ratio: float = 0.0
    achievement_ratio: float = 0.0
    has_contact_info: bool = False


def _bullets(text: str) -> list[str]:
    bullets = _BULLET_RE.findall(text)
    if bullets:
        return bullets
    # fall back to non-empty lines when the resume doesn't use bullet glyphs
    return [line.strip() for line in text.splitlines() if line.strip()]


def extract_education(education_text: str) -> list[str]:
    lines = [line.strip() for line in education_text.splitlines() if line.strip()]
    return [line for line in lines if any(kw in line.lower() for kw in DEGREE_KEYWORDS)]


def detect_certifications(sections: dict[str, str]) -> list[str]:
    found: list[str] = []
    haystacks = [sections.get("certifications", "")] + list(sections.values())
    seen_lines: set[str] = set()
    for text in haystacks:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped in seen_lines:
                continue
            if any(kw in stripped.lower() for kw in CERTIFICATION_KEYWORDS):
                found.append(stripped)
                seen_lines.add(stripped)
    return found


def action_verb_ratio(experience_text: str) -> float:
    bullets = _bullets(experience_text)
    if not bullets:
        return 0.0
    starting_with_verb = sum(
        1 for b in bullets if b.split(" ")[0].strip(".,:;").lower() in ACTION_VERBS
    )
    return starting_with_verb / len(bullets)


def achievement_ratio(experience_text: str) -> float:
    bullets = _bullets(experience_text)
    if not bullets:
        return 0.0
    quantified = sum(1 for b in bullets if _NUMBER_RE.search(b))
    return quantified / len(bullets)


def has_email_info(header_text: str) -> bool:
    return bool(_EMAIL_RE.search(header_text))


def has_phone_info(header_text: str) -> bool:
    return bool(_PHONE_RE.search(header_text))


def has_contact_info(header_text: str) -> bool:
    return has_email_info(header_text) or has_phone_info(header_text)


def has_bullet_formatting(experience_text: str) -> bool:
    """ATS parsers and recruiters both favor bulleted achievements over dense
    paragraphs — a resume with no bullet glyphs at all is a real formatting risk."""
    return bool(_BULLET_GLYPH_RE.search(experience_text))


def date_formatting_score(experience_text: str) -> float:
    """Most ATS systems parse work history by locating date ranges per entry;
    resumes with no recognizable date pattern often fail that extraction step."""
    matches = len(_DATE_RANGE_RE.findall(experience_text))
    return min(1.0, matches / 2)


def length_score(raw_text: str) -> float:
    """Word count as a proxy for two real ATS failure modes: a scanned/image-based
    PDF that yields almost no extractable text, and a resume too long to be
    fully indexed/skimmed by parsers and recruiters alike."""
    words = len(raw_text.split())
    if words < 120:
        return 0.0
    if words < 250:
        return 0.5
    if words <= 1200:
        return 1.0
    if words <= 1800:
        return 0.7
    return 0.4


def skills_section_quality(sections: dict[str, str]) -> float:
    """Rewards an explicit, taxonomy-recognizable skills list — the section ATS
    keyword scanners weight most heavily — over a missing or vague one."""
    skills_text = sections.get("skills", "")
    if not skills_text:
        return 0.0
    return min(1.0, len(extract_skills(skills_text)) / 8)


def compute_section_scores(
    sections: dict[str, str],
    *,
    action_verbs: float,
    achievements: float,
) -> dict[str, int]:
    scores: dict[str, int] = {}

    summary = sections.get("summary", "")
    scores["summary"] = min(100, 40 + len(summary.split()) * 2) if summary else 0

    experience = sections.get("experience", "")
    if experience:
        scores["experience"] = round(30 + action_verbs * 40 + achievements * 30)
    else:
        scores["experience"] = 0

    education = sections.get("education", "")
    scores["education"] = 80 if education else 0

    skills = sections.get("skills", "")
    scores["skills"] = min(100, 20 + len(skills.split(","))) if skills else 0

    return scores


def compute_ats_score(
    sections: dict[str, str],
    raw_text: str,
    *,
    has_email: bool,
    has_phone: bool,
    keyword_overlap: float,
    has_target_role: bool,
) -> int:
    """Approximates how a real ATS parser + recruiter keyword scan would treat this
    resume. Weighted components (100 pts total), each independently deterministic:

    - Contact parseability (15): email/phone found in a machine-readable format.
    - Section structure (25): the 4 required headings present (20) + a bonus
      section like Projects/Certifications (5) — ATS parsers key off headings,
      not just content quality.
    - Keyword/skill match (30): overlap with the target role/industry when given;
      otherwise the count of taxonomy-recognized skills anywhere in the resume
      (a real signal, unlike the previous flat 0.5 placeholder with no target).
    - Formatting/parseability (20): resume length (catches near-empty extraction
      from scanned/image PDFs and overlong resumes), bulleted vs. paragraph
      experience, and recognizable date ranges per role (ATS parses work history
      by date pattern).
    - Skills section quality (10): how many taxonomy-recognized skills sit in an
      explicit Skills section specifically, not just anywhere in the resume.
    """
    contact = (8 if has_email else 0) + (7 if has_phone else 0)

    present_required = sum(1 for s in REQUIRED_SECTIONS if sections.get(s))
    structure = (present_required / len(REQUIRED_SECTIONS)) * 20
    if sections.get("projects") or sections.get("certifications"):
        structure += 5

    if has_target_role:
        keyword_component = keyword_overlap * 30
    else:
        keyword_component = min(1.0, len(extract_skills(raw_text)) / 10) * 30

    experience_text = sections.get("experience", "")
    formatting = (
        length_score(raw_text) * 10
        + (5 if has_bullet_formatting(experience_text) else 0)
        + date_formatting_score(experience_text) * 5
    )

    skills_quality = skills_section_quality(sections) * 10

    total = contact + structure + keyword_component + formatting + skills_quality
    return round(min(100, total))


def compute_resume_score(section_scores: dict[str, int], ats_score: int) -> int:
    if not section_scores:
        return 0
    avg_section = sum(section_scores.values()) / len(section_scores)
    return round(avg_section * 0.7 + ats_score * 0.3)


def weak_sections(section_scores: dict[str, int], threshold: int = 60) -> list[str]:
    return [name for name, score in section_scores.items() if score < threshold]


def analyze(sections: dict[str, str], *, raw_text: str = "", target_role_text: str = "") -> ResumeAnalysis:
    from app.services.keyword_extractor import keyword_overlap_score

    if not raw_text:
        raw_text = "\n".join(sections.values())

    experience_text = sections.get("experience", "")
    header_text = sections.get("header", "")
    contact_text = header_text + "\n" + sections.get("summary", "")

    verbs = action_verb_ratio(experience_text)
    achievements = achievement_ratio(experience_text)
    contact = has_contact_info(contact_text)
    keyword_overlap = (
        keyword_overlap_score(raw_text, target_role_text) if target_role_text else 0.0
    )

    section_scores = compute_section_scores(sections, action_verbs=verbs, achievements=achievements)
    ats = compute_ats_score(
        sections,
        raw_text,
        has_email=has_email_info(contact_text),
        has_phone=has_phone_info(contact_text),
        keyword_overlap=keyword_overlap,
        has_target_role=bool(target_role_text),
    )
    resume_score = compute_resume_score(section_scores, ats)

    return ResumeAnalysis(
        section_scores=section_scores,
        weak_sections=weak_sections(section_scores),
        ats_score=ats,
        resume_score=resume_score,
        total_experience_years=estimate_total_experience_years(experience_text),
        education=extract_education(sections.get("education", "")),
        certifications=detect_certifications(sections),
        action_verb_ratio=verbs,
        achievement_ratio=achievements,
        has_contact_info=contact,
    )
