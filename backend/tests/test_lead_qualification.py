from datetime import date

import pytest

from app.services.lead_qualification import (
    LeadDraft,
    checklist_failures,
    extract_visa_signals,
    has_open_to_work_signal,
    is_recent_graduate,
    is_software_role,
    normalize_linkedin_url,
    recruiter_rejection_reason,
    score_lead,
)

TODAY = date(2026, 8, 21)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.linkedin.com/in/example-name", "linkedin.com/in/example-name"),
        ("https://www.linkedin.com/in/Example-Name/", "linkedin.com/in/example-name"),
        ("https://www.linkedin.com/in/example-name?utm_source=share", "linkedin.com/in/example-name"),
        ("https://in.linkedin.com/in/example-name?trk=public_profile", "linkedin.com/in/example-name"),
        ("linkedin.com/in/example-name", "linkedin.com/in/example-name"),
        ("https://www.linkedin.com/company/acme", ""),  # SOP s22: not a person
        ("https://example.com/in/example-name", ""),
        ("", ""),
    ],
)
def test_normalize_linkedin_url(raw, expected):
    assert normalize_linkedin_url(raw) == expected


def test_is_software_role():
    assert is_software_role("Software Engineer")
    assert is_software_role("Machine Learning Engineer")
    assert is_software_role("SDE Intern")
    assert not is_software_role("Marketing Associate")


def test_recruiter_rejection_reason_flags_sop_s22_profiles():
    assert recruiter_rejection_reason("Bench Sales Recruiter")
    assert recruiter_rejection_reason("Hiring for OPT candidates")
    assert recruiter_rejection_reason("Technical Recruiter at Acme Staffing")
    assert recruiter_rejection_reason("Software Engineer") == ""


def test_extract_visa_signals_reads_only_stated_text():
    stem = extract_visa_signals("Currently on STEM OPT and looking for roles")
    assert stem == {
        "f1_signal": True,
        "opt_signal": True,
        "stem_opt_signal": True,
        "cpt_signal": False,
    }

    cpt = extract_visa_signals("Available for CPT internships")
    assert cpt["cpt_signal"] is True
    assert cpt["opt_signal"] is False

    # No stated status -> no signal. An Indian name or U.S. university must
    # never produce one (SOP s23 rules 2 and 3).
    assert extract_visa_signals("MS Computer Science, Arizona State University") == {
        "f1_signal": False,
        "opt_signal": False,
        "stem_opt_signal": False,
        "cpt_signal": False,
    }


def test_has_open_to_work_signal():
    assert has_open_to_work_signal("#OpenToWork - actively seeking SDE roles")
    assert has_open_to_work_signal("Available immediately")
    assert not has_open_to_work_signal("Software Engineer at Acme")


def test_is_recent_graduate():
    assert is_recent_graduate(2026, today=TODAY)
    assert is_recent_graduate(2025, today=TODAY)
    assert is_recent_graduate(2027, today=TODAY)  # graduating soon
    assert not is_recent_graduate(2019, today=TODAY)
    assert not is_recent_graduate(None, today=TODAY)


def _draft(**overrides) -> LeadDraft:
    values = dict(
        is_software_role=True,
        opt_signal=True,
        stem_opt_signal=False,
        cpt_signal=False,
        open_to_work_signal=True,
        graduation_year=2026,
        state="Texas",
        evidence_date=date(2026, 8, 10),
        job_search_evidence="Actively seeking Software Engineer roles.",
        linkedin_url="https://www.linkedin.com/in/example-name",
        target_role="Software Engineer",
        researcher_name="R. Executive",
        date_researched=TODAY,
        indian_identity="yes",
        visa_statement="Currently on F-1 OPT.",
        email="candidate@example.com",
        email_source="Apollo",
        direct_number=None,
        phone_source=None,
    )
    values.update(overrides)
    return LeadDraft(values)


def test_score_lead_a_plus_needs_a_fresh_job_search_post():
    priority, reasons = score_lead(_draft(), today=TODAY)
    assert priority == "A+"
    assert "Job-search post within 30 days" in reasons


def test_score_lead_a_when_evidence_is_stale():
    # Same lead, but the job-search post is from four months ago (SOP s12).
    priority, _ = score_lead(_draft(evidence_date=date(2026, 4, 1)), today=TODAY)
    assert priority == "A"


def test_score_lead_b_when_visa_status_unverified():
    priority, reasons = score_lead(_draft(opt_signal=False, stem_opt_signal=False), today=TODAY)
    assert priority == "B"
    assert "Work authorization still to verify" in reasons


def test_score_lead_c_when_open_to_work_unclear():
    priority, reasons = score_lead(
        _draft(open_to_work_signal=False, opt_signal=False, graduation_year=None), today=TODAY
    )
    assert priority == "C"
    assert "Open-to-Work status unclear" in reasons
    assert "Graduation information missing" in reasons


def test_checklist_passes_for_a_complete_lead():
    assert checklist_failures(_draft()) == []


def test_checklist_flags_unsourced_contact_details():
    failures = checklist_failures(_draft(email_source=None, direct_number="555-0100"))
    assert "Email recorded without a source" in failures
    assert "Direct number recorded without a source" in failures


def test_checklist_flags_visa_signal_without_stated_wording():
    failures = checklist_failures(_draft(visa_statement=""))
    assert "Visa signal recorded without the candidate's stated wording" in failures


def test_checklist_flags_identity_claim_without_evidence():
    failures = checklist_failures(_draft(job_search_evidence="", evidence_url=None))
    assert "Indian identity marked yes without recorded public evidence" in failures
