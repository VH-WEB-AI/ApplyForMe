from unittest.mock import patch

import pytest

from app.db.models.leads import ResearchLead
from app.services.apollo_client import ApolloError, ApolloPlanError
from app.services.lead_enrichment import enrich_lead, verify_identity


def _lead(**overrides) -> ResearchLead:
    defaults = dict(
        first_name="Rahul",
        last_name="Example",
        linkedin_url="https://www.linkedin.com/in/rahul-example",
        linkedin_key="linkedin.com/in/rahul-example",
        state="Texas",
        target_role="Software Engineer",
    )
    defaults.update(overrides)
    return ResearchLead(**defaults)


def _person(**overrides) -> dict:
    defaults = {
        "first_name": "Rahul",
        "last_name": "Example",
        "linkedin_url": "https://www.linkedin.com/in/rahul-example",
        "email": "rahul@acme.com",
        "email_status": "verified",
        "city": "Dallas",
        "state": "Texas",
        "title": "Software Engineer",
    }
    defaults.update(overrides)
    return defaults


# --- identity checks (SOP s15/s17) ------------------------------------------


def test_identity_matches_on_linkedin_url_ignoring_tracking_params():
    matched, reason = verify_identity(
        _lead(), _person(linkedin_url="https://in.linkedin.com/in/Rahul-Example/?trk=x")
    )
    assert matched is True
    assert "LinkedIn URL matches" in reason


def test_identity_rejects_a_different_linkedin_profile():
    matched, reason = verify_identity(
        _lead(), _person(linkedin_url="https://www.linkedin.com/in/someone-else")
    )
    assert matched is False
    assert "mismatch" in reason


def test_identity_rejects_same_surname_with_no_corroboration():
    # The classic SOP s15 failure: right name, wrong human.
    matched, reason = verify_identity(
        _lead(state=None), _person(linkedin_url=None, state=None)
    )
    assert matched is False
    assert "refusing to attach contact data" in reason


def test_identity_accepts_surname_plus_state_when_apollo_has_no_url():
    matched, reason = verify_identity(_lead(), _person(linkedin_url=None))
    assert matched is True
    assert "state match" in reason


def test_identity_rejects_a_different_surname():
    matched, _ = verify_identity(_lead(), _person(linkedin_url=None, last_name="Different"))
    assert matched is False


# --- enrichment behaviour ----------------------------------------------------


def test_enrich_fills_blank_email_and_records_the_source(db):
    lead = _lead(city=None)
    db.add(lead)
    db.flush()

    with patch("app.services.lead_enrichment.match_person", return_value=_person()):
        report = enrich_lead(db, lead)

    assert report["status"] == "enriched"
    assert set(report["fieldsFilled"]) == {"email", "city"}
    assert lead.email == "rahul@acme.com"
    assert lead.email_source == "Apollo"
    assert lead.city == "Dallas"


def test_enrich_never_overwrites_researched_data(db):
    lead = _lead(email="researched@example.com", email_source="LinkedIn", city="Plano")
    db.add(lead)
    db.flush()

    with patch("app.services.lead_enrichment.match_person", return_value=_person()):
        report = enrich_lead(db, lead)

    assert report["status"] == "nothing_to_fill"
    assert lead.email == "researched@example.com"
    assert lead.email_source == "LinkedIn"
    assert lead.city == "Plano"


def test_enrich_writes_nothing_when_identity_check_fails(db):
    lead = _lead()
    db.add(lead)
    db.flush()

    wrong_person = _person(linkedin_url="https://www.linkedin.com/in/someone-else")
    with patch("app.services.lead_enrichment.match_person", return_value=wrong_person):
        report = enrich_lead(db, lead)

    assert report["status"] == "identity_mismatch"
    assert report["fieldsFilled"] == []
    assert lead.email is None


def test_enrich_ignores_locked_email_placeholder(db):
    lead = _lead()
    db.add(lead)
    db.flush()

    locked = _person(email="email_not_unlocked@domain.com", city=None)
    with patch("app.services.lead_enrichment.match_person", return_value=locked):
        report = enrich_lead(db, lead)

    assert lead.email is None
    assert "email" not in report["fieldsFilled"]


def test_enrich_reports_no_match(db):
    lead = _lead()
    db.add(lead)
    db.flush()

    with patch("app.services.lead_enrichment.match_person", return_value=None):
        assert enrich_lead(db, lead)["status"] == "no_match"


@pytest.mark.parametrize(
    "error", [ApolloPlanError("plan does not include this endpoint"), ApolloError("boom")]
)
def test_enrich_surfaces_apollo_errors_without_touching_the_lead(db, error):
    lead = _lead()
    db.add(lead)
    db.flush()

    with patch("app.services.lead_enrichment.match_person", side_effect=error):
        report = enrich_lead(db, lead)

    assert report["status"] == "error"
    assert report["fieldsFilled"] == []
    assert lead.email is None
