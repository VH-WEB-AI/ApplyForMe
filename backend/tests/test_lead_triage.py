"""Covers signal-first triage: what gets rejected before a credit is spent, and
what enrichment is allowed to change afterwards."""

from datetime import date

from app.services.lead_triage import ResearchInput, grade, merge_enrichment, qualify

TODAY = date(2026, 8, 24)

OPEN_TO_WORK = "#OpenToWork graduating May 2025, on F-1 STEM OPT, available immediately for SDE roles."


def _entry(**overrides) -> ResearchInput:
    defaults = dict(
        linkedin_url="https://www.linkedin.com/in/rahul-example?trk=abc",
        evidence_text=OPEN_TO_WORK,
        evidence_url="https://linkedin.com/posts/rahul-1",
        evidence_date=date(2026, 8, 10),
        researcher_name="R. Sharma",
        target_role="Software Engineer",
        state="Texas",
    )
    defaults.update(overrides)
    return ResearchInput(**defaults)


# --- the free rejection gate ------------------------------------------------


def test_a_profile_without_an_open_to_work_signal_is_rejected():
    """The whole point: this lead can never pass checklist_failures, so it must
    never reach a paid enrichment call."""
    result = qualify(_entry(evidence_text="Computer Science graduate, 2025."), today=TODAY)
    assert result.qualified is False
    assert any("Open-to-Work" in r for r in result.rejections)


def test_a_recruiter_is_rejected_even_with_an_open_to_work_phrase():
    result = qualify(
        _entry(evidence_text="Bench sales recruiter, open to work with new clients."), today=TODAY
    )
    assert result.qualified is False
    assert any("s22" in r for r in result.rejections)


def test_an_unusable_linkedin_url_is_rejected():
    result = qualify(_entry(linkedin_url="https://example.com/someone"), today=TODAY)
    assert result.qualified is False
    assert any("s14" in r for r in result.rejections)


def test_a_non_software_role_is_rejected():
    result = qualify(_entry(target_role="Marketing Associate"), today=TODAY)
    assert result.qualified is False
    assert any("s3" in r for r in result.rejections)


def test_a_blank_role_is_left_to_enrichment_rather_than_rejected():
    """PDL fills job_title reliably, so an unknown role is not a disqualifier."""
    assert qualify(_entry(target_role=""), today=TODAY).qualified is True


def test_a_qualified_entry_carries_the_signals_only_a_human_could_collect():
    result = qualify(_entry(), today=TODAY)
    assert result.qualified is True
    assert result.values["open_to_work_signal"] is True
    assert result.values["stem_opt_signal"] is True
    assert result.values["opt_signal"] is True
    assert result.values["f1_signal"] is True
    assert result.values["visa_statement"] == OPEN_TO_WORK
    assert result.linkedin_key == "linkedin.com/in/rahul-example"


def test_no_visa_statement_is_recorded_when_no_visa_signal_was_stated():
    """s29 pairs a visa flag with the candidate's wording; absent a flag there is
    nothing to quote, and inventing one would fail the checklist."""
    result = qualify(_entry(evidence_text="Actively seeking full-time software roles."), today=TODAY)
    assert result.values["opt_signal"] is False
    assert result.values["visa_statement"] is None


# --- enrichment merge -------------------------------------------------------


def test_enrichment_fills_blanks_without_overwriting_research():
    result = qualify(_entry(state="Texas"), today=TODAY)
    merge_enrichment(result, {"state": "California", "university": "UT Austin", "graduation_year": 2025})
    assert result.values["state"] == "Texas", "researched data must win"
    assert result.values["university"] == "UT Austin"
    assert result.values["graduation_year"] == 2025


def test_a_vendor_record_for_a_different_person_is_discarded_entirely():
    result = qualify(_entry(), today=TODAY)
    merge_enrichment(result, {
        "linkedin_url": "https://linkedin.com/in/someone-else",
        "email": "stranger@acme.com",
        "university": "MIT",
    })
    assert result.values.get("email") is None
    assert result.values.get("university") is None
    assert any("identity mismatch" in r for r in result.rejections)


def test_a_vendor_record_with_no_url_is_still_merged():
    result = qualify(_entry(), today=TODAY)
    merge_enrichment(result, {"university": "UT Austin"})
    assert result.values["university"] == "UT Austin"


# --- grading ----------------------------------------------------------------


def test_enrichment_lifts_a_qualified_lead_from_b_to_a():
    """Grade A needs a graduation year, which is exactly what PDL adds."""
    before = grade(qualify(_entry(), today=TODAY), today=TODAY)
    assert before.grade == "B"

    after = qualify(_entry(), today=TODAY)
    merge_enrichment(after, {"graduation_year": 2025, "university": "UT Austin"})
    grade(after, today=TODAY)
    assert after.grade in ("A", "A+")


def test_a_fresh_post_with_every_signal_reaches_a_plus():
    result = qualify(_entry(evidence_date=date(2026, 8, 20)), today=TODAY)
    merge_enrichment(result, {"graduation_year": 2025})
    grade(result, today=TODAY)
    assert result.grade == "A+"


def test_a_graded_lead_reports_what_still_blocks_submission():
    result = grade(qualify(_entry(researcher_name=""), today=TODAY), today=TODAY)
    assert any("researcher_name" in c for c in result.checklist)
