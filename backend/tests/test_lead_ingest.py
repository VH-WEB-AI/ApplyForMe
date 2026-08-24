from datetime import date

from app.db.models.leads import ResearchLead
from app.schemas.api import ResearchLeadRow
from app.services.lead_ingest import ingest_lead_rows, row_to_lead_kwargs

TODAY = date(2026, 8, 21)


def _row(**overrides) -> ResearchLeadRow:
    defaults = {
        "First Name": "Rahul",
        "Last Name": "Example",
        "Email": "rahul@example.com",
        "Direct Number": "+1-555-0100",
        "LinkedIn URL": "https://www.linkedin.com/in/rahul-example?utm_source=share",
        "City": "Dallas",
        "State": "Texas",
        "University": "University of Texas at Dallas",
        "Degree": "MS Computer Science",
        "Graduation Year": "2026",
        "Current/Target Role": "Software Engineer",
        "Skills": "Python, React, AWS",
        "Visa Statement": "Currently on F-1 OPT and actively looking for Software Engineer roles.",
        "Open-to-Work Signal": "Yes",
        "Job Search Evidence": "#OpenToWork post seeking full-time opportunities",
        "Evidence/Post URL": "https://www.linkedin.com/posts/rahul-example-123",
        "Evidence Date": "2026-08-12",
        "Indian Identity Publicly Stated": "Yes",
        "Email Source": "Apollo",
        "Phone Source": "Apollo",
        "Date Researched": "2026-08-21",
        "Researcher Name": "R. Executive",
    }
    defaults.update(overrides)
    return ResearchLeadRow(**defaults)


def test_row_to_lead_kwargs_derives_signals_and_priority():
    kwargs = row_to_lead_kwargs(_row(), today=TODAY)
    assert kwargs["linkedin_key"] == "linkedin.com/in/rahul-example"  # tracking param stripped
    assert kwargs["is_software_role"] is True
    assert kwargs["f1_signal"] is True
    assert kwargs["opt_signal"] is True
    assert kwargs["stem_opt_signal"] is False
    assert kwargs["skills"] == ["Python", "React", "AWS"]
    assert kwargs["lead_priority"] == "A+"
    assert kwargs["is_rejected"] is False


def test_researcher_checkbox_cannot_invent_a_visa_signal():
    # The checkbox says OPT but the candidate never stated it (SOP s23 rule 3).
    kwargs = row_to_lead_kwargs(
        _row(**{"Visa Statement": "", "OPT Signal": "Yes", "F-1 Signal": "Yes"}), today=TODAY
    )
    assert kwargs["opt_signal"] is False
    assert kwargs["f1_signal"] is False


def test_identity_defaults_to_unknown_when_not_stated():
    kwargs = row_to_lead_kwargs(_row(**{"Indian Identity Publicly Stated": ""}), today=TODAY)
    assert kwargs["indian_identity"] == "unknown"


def test_recruiter_row_is_stored_but_flagged_rejected():
    kwargs = row_to_lead_kwargs(
        _row(**{"Current/Target Role": "Bench Sales Recruiter"}), today=TODAY
    )
    assert kwargs["is_rejected"] is True
    assert "s22" in kwargs["rejection_reason"]


def test_row_without_a_profile_url_is_unusable():
    assert row_to_lead_kwargs(_row(**{"LinkedIn URL": ""}), today=TODAY) is None
    assert (
        row_to_lead_kwargs(_row(**{"LinkedIn URL": "https://www.linkedin.com/company/acme"}), today=TODAY)
        is None
    )


def test_ingest_dedupes_on_normalized_linkedin_url(db):
    # Same person, three URL spellings the SOP s14 cleanup collapses into one.
    rows = [
        _row(),
        _row(**{"LinkedIn URL": "https://www.linkedin.com/in/rahul-example/"}),
        _row(**{"LinkedIn URL": "https://in.linkedin.com/in/Rahul-Example?trk=x"}),
    ]
    result = ingest_lead_rows(db, rows, today=TODAY)
    assert result["created"] == 1
    assert result["updated"] + result["skipped"] == 2

    stored = db.query(ResearchLead).filter_by(linkedin_key="linkedin.com/in/rahul-example").one()
    assert stored.lead_priority == "A+"
    assert stored.city == "Dallas"


def test_reingest_fills_missing_fields_without_overwriting(db):
    sparse = _row(
        **{
            "Direct Number": "",
            "Phone Source": "",
            "University": "",
            "City": "Dallas",
        }
    )
    ingest_lead_rows(db, [sparse], today=TODAY)

    enriched = _row(
        **{
            "Direct Number": "+1-555-0199",
            "Phone Source": "ContactOut",
            "University": "University of Texas at Dallas",
            "City": "Plano",  # conflicting value -- must NOT overwrite
        }
    )
    result = ingest_lead_rows(db, [enriched], today=TODAY)
    assert result["created"] == 0
    assert result["updated"] == 1

    stored = db.query(ResearchLead).filter_by(linkedin_key="linkedin.com/in/rahul-example").one()
    assert stored.direct_number == "+1-555-0199"  # was blank, now filled
    assert stored.phone_source == "ContactOut"
    assert stored.university == "University of Texas at Dallas"
    assert stored.city == "Dallas"  # already populated, left alone


def test_reingesting_an_unchanged_row_is_a_no_op(db):
    ingest_lead_rows(db, [_row()], today=TODAY)
    result = ingest_lead_rows(db, [_row()], today=TODAY)
    assert result == {"created": 0, "updated": 0, "skipped": 1, "rejected": []}
    assert db.query(ResearchLead).filter_by(linkedin_key="linkedin.com/in/rahul-example").count() == 1
