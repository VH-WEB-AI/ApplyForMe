from app.engines.career_copilot.engine import CareerCopilotEngine


def test_weak_area_instructions_require_two_markdown_sections():
    instructions = CareerCopilotEngine()._response_instructions(
        "What are weak areas in my resume?", detailed_response=False
    )

    assert "Resume Weak Areas\n\n" in instructions
    assert "Recommendations\n\n" in instructions
    assert "Use plain section titles without # or ## characters" in instructions
    assert "Never put a section title and the first bullet on the same line" in instructions


def test_normalizes_inline_headings_and_removes_hash_markers():
    reply = (
        "## Areas for Improvement - The resume lacks a professional summary. "
        "- Contact information is incomplete. - Certifications are missing. "
        "## Recommendations - Add a professional summary. - Add certifications."
    )

    normalized = CareerCopilotEngine()._normalize_markdown_reply(reply)

    assert normalized == (
        "Areas for Improvement\n\n"
        "- The resume lacks a professional summary.\n"
        "- Contact information is incomplete.\n"
        "- Certifications are missing.\n\n"
        "Recommendations\n\n"
        "- Add a professional summary.\n"
        "- Add certifications."
    )
