"""Tests for the PDL sourcing client.

Everything here is offline: the SQL builder, the response mapping, and the SOP
s13 guard are all pure. HTTP behaviour is covered by patching httpx.request.
"""

from unittest.mock import patch

import httpx
import pytest

from app.services import pdl_client
from app.services.pdl_client import (
    PDLError,
    PDLNotConfigured,
    PDLPlanError,
    assert_no_identity_filter,
    build_fresher_sql,
    enrich_person,
    search_people,
    to_lead_fields,
)


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture()
def configured(monkeypatch):
    """A settings object with a PDL key, so _request gets past the config check."""
    settings = pdl_client.get_settings()
    monkeypatch.setattr(settings, "pdl_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "pdl_min_likelihood", 6, raising=False)
    return settings


# --- SQL building -----------------------------------------------------------

def test_build_fresher_sql_covers_both_countries_and_grad_years():
    sql = build_fresher_sql()
    assert "location_country IN ('united states', 'india')" in sql
    assert "education.end_date >= '2025-01-01'" in sql
    assert "education.end_date <= '2026-12-31'" in sql
    assert "linkedin_url IS NOT NULL" in sql


def test_build_fresher_sql_filters_on_software_majors():
    sql = build_fresher_sql(majors=("computer science", "data science"))
    assert "education.majors IN ('computer science', 'data science')" in sql


def test_build_fresher_sql_applies_precision_filters_by_default():
    """Education filters alone return ~1.2M mostly-senior people on live data.

    The degree-type filter drops executive-education and certificate courses,
    and the seniority filter drops everyone already established. Together they
    are what makes the shortlist usable, so they must stay on by default.
    """
    sql = build_fresher_sql()
    assert "education.degrees IN ('bachelors', 'masters')" in sql
    assert "job_title_levels IN ('entry', 'training')" in sql
    assert "job_title_role = 'engineering'" in sql


def test_precision_filters_can_be_relaxed():
    sql = build_fresher_sql(entry_level_only=False, engineering_only=False, degrees=())
    assert "job_title_levels" not in sql
    assert "job_title_role" not in sql
    assert "education.degrees" not in sql


def test_build_fresher_sql_never_uses_inferred_years_experience():
    """It returns null on this dataset and null still passes `<= 3`, so it
    silently filters nothing while looking like it works."""
    assert "inferred_years_experience" not in build_fresher_sql()


def test_build_fresher_sql_avoids_or_chained_like_clauses():
    """PDL's SQL parser rejects a long OR-chain of LIKE clauses with a 400.

    That failure looks like a parse error rather than an empty result, so it is
    easy to misread as a plan restriction. Keep the query to IN and comparisons.
    """
    sql = build_fresher_sql()
    assert " OR " not in sql
    assert "LIKE" not in sql


def test_build_fresher_sql_escapes_quotes():
    sql = build_fresher_sql(countries=("o'brienland",), majors=(), degrees=())
    assert "'o''brienland'" in sql


def test_build_fresher_sql_rejects_bad_inputs():
    with pytest.raises(PDLError):
        build_fresher_sql(countries=())
    with pytest.raises(PDLError, match="not be earlier"):
        build_fresher_sql(grad_year_from=2026, grad_year_to=2025)


def test_generated_sql_never_filters_on_identity():
    """SOP s13: sourcing must never select people by nationality or ethnicity."""
    sql = build_fresher_sql().lower()
    for term in ("nationality", "ethnicity", "caste", "religion", "race", "indian"):
        assert term not in sql


@pytest.mark.parametrize(
    "bad_sql",
    [
        "SELECT * FROM person WHERE nationality='indian'",
        "SELECT * FROM person WHERE ethnicity = 'south asian'",
        "SELECT * FROM person WHERE caste IS NOT NULL",
    ],
)
def test_assert_no_identity_filter_rejects_discriminatory_queries(bad_sql):
    with pytest.raises(PDLError, match="SOP s13"):
        assert_no_identity_filter(bad_sql)


def test_search_people_refuses_an_identity_filtered_query(configured):
    with pytest.raises(PDLError, match="SOP s13"):
        search_people("SELECT * FROM person WHERE nationality='indian'")


# --- HTTP behaviour ---------------------------------------------------------

def test_missing_key_raises_not_configured(monkeypatch):
    settings = pdl_client.get_settings()
    monkeypatch.setattr(settings, "pdl_api_key", "", raising=False)
    with pytest.raises(PDLNotConfigured):
        enrich_person(linkedin_url="https://www.linkedin.com/in/someone")


def test_no_match_returns_none_rather_than_raising(configured):
    with patch("httpx.request", return_value=_Response(404, {"status": 404})):
        assert enrich_person(linkedin_url="https://www.linkedin.com/in/nobody") is None


def test_forbidden_is_reported_as_a_plan_error(configured):
    payload = {"error": {"message": "Search is not available on your plan"}}
    with patch("httpx.request", return_value=_Response(403, payload)):
        with pytest.raises(PDLPlanError, match="not available on your plan"):
            search_people(build_fresher_sql())


def test_payment_required_is_a_plan_error(configured):
    with patch("httpx.request", return_value=_Response(402, {"error": {"message": "no credits"}})):
        with pytest.raises(PDLPlanError):
            search_people(build_fresher_sql())


def test_rate_limit_is_retryable_message(configured):
    with patch("httpx.request", return_value=_Response(429, {})):
        with pytest.raises(PDLError, match="rate limit"):
            enrich_person(linkedin_url="https://www.linkedin.com/in/someone")


def test_transport_error_is_wrapped(configured):
    with patch("httpx.request", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(PDLError, match="failed"):
            enrich_person(linkedin_url="https://www.linkedin.com/in/someone")


def test_enrich_requires_a_usable_identifier(configured):
    with pytest.raises(PDLError, match="required"):
        enrich_person(first_name="OnlyFirst")


def test_enrich_rejects_a_bare_name(configured):
    """PDL 400s on first+last name alone -- it needs context too, unlike Apollo."""
    with pytest.raises(PDLError, match="name plus context"):
        enrich_person(first_name="Priya", last_name="Sharma")


def test_enrich_accepts_a_name_with_context(configured):
    captured = {}

    def _capture(method, url, **kwargs):
        captured.update(kwargs.get("params") or {})
        return _Response(200, {"data": {"first_name": "Priya"}})

    with patch("httpx.request", side_effect=_capture):
        enrich_person(first_name="Priya", last_name="Sharma", school="UT Austin")

    assert captured["school"] == "UT Austin"
    assert captured["first_name"] == "Priya"


def test_enrich_rejects_unknown_context_parameters(configured):
    with pytest.raises(PDLError, match="Unsupported"):
        enrich_person(first_name="A", last_name="B", nationality="indian")


def test_enrich_prefers_linkedin_url_as_the_lookup_key(configured):
    captured = {}

    def _capture(method, url, **kwargs):
        captured.update(kwargs.get("params") or {})
        return _Response(200, {"data": {"first_name": "A"}})

    with patch("httpx.request", side_effect=_capture):
        enrich_person(linkedin_url="https://www.linkedin.com/in/priya", first_name="Priya", last_name="S")

    assert captured["profile"] == "https://www.linkedin.com/in/priya"
    assert "first_name" not in captured


def test_search_people_unwraps_records_and_scroll_token(configured):
    payload = {"data": [{"first_name": "A"}], "total": 42, "scroll_token": "tok"}
    with patch("httpx.request", return_value=_Response(200, payload)):
        result = search_people(build_fresher_sql(), size=5)
    assert result["total"] == 42
    assert result["scroll_token"] == "tok"
    assert len(result["records"]) == 1


def test_search_size_is_clamped_to_the_api_maximum(configured):
    captured = {}

    def _capture(method, url, **kwargs):
        captured.update(kwargs.get("json") or {})
        return _Response(200, {"data": [], "total": 0})

    with patch("httpx.request", side_effect=_capture):
        search_people(build_fresher_sql(), size=500)
    assert captured["size"] == 100


# --- Response mapping -------------------------------------------------------

_RECORD = {
    "first_name": "Priya",
    "last_name": "Sharma",
    "linkedin_url": "https://www.linkedin.com/in/priya-sharma-123",
    "work_email": "priya@example.com",
    "location_locality": "Austin",
    "location_region": "Texas",
    "location_country": "united states",
    "job_title": "Software Engineer",
    "skills": ["Python", "React"],
    "education": [
        {"school": {"name": "Some Undergrad College"}, "degrees": ["bachelors"],
         "majors": ["computer science"], "end_date": "2023-05"},
        {"school": {"name": "UT Austin"}, "degrees": ["masters"],
         "majors": ["computer science"], "end_date": "2025-05"},
    ],
}


def test_to_lead_fields_uses_the_most_recent_degree():
    fields = to_lead_fields(_RECORD)
    assert fields["university"] == "UT Austin"
    assert fields["degree"] == "masters"
    assert fields["graduation_year"] == 2025


def test_to_lead_fields_maps_location_and_contact():
    fields = to_lead_fields(_RECORD)
    assert fields["city"] == "Austin"
    assert fields["state"] == "Texas"
    assert fields["email"] == "priya@example.com"
    assert fields["target_role"] == "Software Engineer"


def test_to_lead_fields_survives_a_sparse_record():
    fields = to_lead_fields({"first_name": "A"})
    assert fields["university"] is None
    assert fields["graduation_year"] is None
    assert fields["skills"] == []


def test_to_lead_fields_never_invents_visa_or_identity_columns():
    """PDL has no such data, so the mapper must not produce these keys at all."""
    fields = to_lead_fields(_RECORD)
    for forbidden in ("f1_signal", "opt_signal", "cpt_signal", "indian_identity",
                      "open_to_work_signal"):
        assert forbidden not in fields


def test_to_lead_fields_handles_plan_gated_false_values():
    """PDL returns the boolean `false` for fields the plan excludes.

    Seen live on work_email. Treating that as a string raises AttributeError,
    so every field read must tolerate it.
    """
    record = {
        "first_name": "Priya",
        "last_name": "Sharma",
        "work_email": False,
        "recommended_personal_email": False,
        "mobile_phone": False,
        "skills": False,
        "education": False,
        "location_region": False,
    }
    fields = to_lead_fields(record)
    assert fields["email"] is None
    assert fields["state"] is None
    assert fields["skills"] == []
    assert fields["university"] is None
    assert fields["graduation_year"] is None
    assert fields["first_name"] == "Priya"
