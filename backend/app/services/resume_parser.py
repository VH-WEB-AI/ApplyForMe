"""Resume Parser: converts PDF/DOCX resumes into raw text + structured sections."""

import io
import re
from dataclasses import dataclass, field

import docx
import pdfplumber

SECTION_HEADERS = {
    "summary": ["summary", "professional summary", "objective", "profile"],
    "experience": ["experience", "work experience", "professional experience", "employment history"],
    "education": ["education", "academic background"],
    "skills": ["skills", "technical skills", "core competencies"],
    "projects": ["projects", "personal projects", "key projects"],
    "certifications": ["certifications", "certificates", "licenses"],
}

_HEADER_TO_SECTION = {
    alias: section for section, aliases in SECTION_HEADERS.items() for alias in aliases
}


@dataclass
class ParsedResume:
    raw_text: str
    sections: dict[str, str] = field(default_factory=dict)


def extract_text(file_bytes: bytes, filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = _extract_pdf_text(file_bytes)
    elif lower.endswith(".docx"):
        text = _extract_docx_text(file_bytes)
    else:
        raise ValueError(f"Unsupported resume file type: {filename}")
    # pdfplumber/pdfminer occasionally emit NUL (0x00) chars for PDFs with malformed
    # embedded font glyph maps; Postgres text/jsonb columns reject NUL bytes outright,
    # so strip them here before this text reaches any downstream storage.
    return text.replace("\x00", "")


def _extract_pdf_text(file_bytes: bytes) -> str:
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _extract_docx_text(file_bytes: bytes) -> str:
    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs)


def _is_section_header(line: str) -> str | None:
    cleaned = re.sub(r"[^a-z ]", "", line.strip().lower())
    if not cleaned or len(cleaned.split()) > 4:
        return None
    return _HEADER_TO_SECTION.get(cleaned)


def identify_sections(raw_text: str) -> dict[str, str]:
    """Splits resume text into named sections using heading heuristics."""
    lines = raw_text.splitlines()
    sections: dict[str, list[str]] = {}
    current_section = "header"
    sections[current_section] = []

    for line in lines:
        header = _is_section_header(line)
        if header:
            current_section = header
            sections.setdefault(current_section, [])
            continue
        sections.setdefault(current_section, []).append(line)

    return {name: "\n".join(content).strip() for name, content in sections.items() if content}


def parse_resume(file_bytes: bytes, filename: str) -> ParsedResume:
    raw_text = extract_text(file_bytes, filename)
    sections = identify_sections(raw_text)
    return ParsedResume(raw_text=raw_text, sections=sections)
