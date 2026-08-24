"""Covers batched Apollo enrichment: the client's 10-per-call contract and the
positional pairing that keeps one lead's contact details off another lead."""

from unittest.mock import patch

import pytest

from app.db.models.leads import ResearchLead
from app.services.apollo_client import BULK_MATCH_MAX, ApolloError, ApolloPlanError, bulk_match
from app.services.lead_enrichment import enrich_leads


def _lead(db, **overrides) -> ResearchLead:
    defaults = dict(
        first_name="Rahul",
        last_name="Example",
        linkedin_url="https://www.linkedin.com/in/rahul-example",
        linkedin_key="linkedin.com/in/rahul-example",
        state="Texas",
        target_role="Software Engineer",
    )
    defaults.update(overrides)
    lead = ResearchLead(**defaults)
    db.add(lead)
    db.flush()
    return lead


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


# --- client contract --------------------------------------------------------


def test_empty_details_makes_no_request():
    with patch("app.services.apollo_client._request") as request:
        assert bulk_match([]) == []
    request.assert_not_called()


def test_more_than_ten_people_is_refused_before_spending_credits():
    details = [{"first_name": "A", "last_name": f"B{i}"} for i in range(BULK_MATCH_MAX + 1)]
    with patch("app.services.apollo_client._request") as request:
        with pytest.raises(ApolloError, match="at most 10"):
            bulk_match(details)
    request.assert_not_called()


def test_an_unmatchable_entry_is_refused_with_its_position():
    details = [{"first_name": "A", "last_name": "B"}, {"first_name": "OnlyFirst"}]
    with patch("app.services.apollo_client._request") as request:
        with pytest.raises(ApolloError, match=r"details\[1\]"):
            bulk_match(details)
    request.assert_not_called()


def test_phone_reveal_is_never_requested():
    """8 credits per person and it needs a webhook this service does not have."""
    with patch("app.services.apollo_client._request", return_value={"matches": [{}]}) as request:
        bulk_match([{"linkedin_url": "https://linkedin.com/in/a"}])
    payload = request.call_args[0][2]
    assert "reveal_phone_number" not in payload
    assert payload["reveal_personal_emails"] is False


def test_a_short_matches_array_is_padded_so_pairing_stays_aligned():
    details = [{"first_name": "A", "last_name": "One"}, {"first_name": "B", "last_name": "Two"}]
    with patch("app.services.apollo_client._request", return_value={"matches": [{"id": "x"}]}):
        assert bulk_match(details) == [{"id": "x"}, None]


def test_nulls_in_the_matches_array_become_none():
    details = [{"first_name": "A", "last_name": "One"}, {"first_name": "B", "last_name": "Two"}]
    with patch("app.services.apollo_client._request", return_value={"matches": [None, {"id": "y"}]}):
        assert bulk_match(details) == [None, {"id": "y"}]


def test_a_missing_matches_array_is_an_error():
    with patch("app.services.apollo_client._request", return_value={}):
        with pytest.raises(ApolloError, match="no `matches` array"):
            bulk_match([{"first_name": "A", "last_name": "B"}])


# --- batching ---------------------------------------------------------------


def test_leads_are_sent_in_batches_of_ten(db):
    leads = [
        _lead(db, first_name=f"P{i}", last_name=f"L{i}", linkedin_url=f"https://linkedin.com/in/p{i}",
              linkedin_key=f"linkedin.com/in/p{i}")
        for i in range(23)
    ]
    with patch("app.services.lead_enrichment.bulk_match", side_effect=lambda d: [None] * len(d)) as bm:
        reports = enrich_leads(db, leads)

    assert [len(call.args[0]) for call in bm.call_args_list] == [10, 10, 3]
    assert len(reports) == 23
    assert all(r["status"] == "no_match" for r in reports.values())


def test_a_failed_batch_does_not_abandon_the_rest(db):
    leads = [
        _lead(db, first_name=f"P{i}", last_name=f"L{i}", linkedin_url=f"https://linkedin.com/in/p{i}",
              linkedin_key=f"linkedin.com/in/p{i}")
        for i in range(12)
    ]
    calls = {"n": 0}

    def flaky(details):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApolloPlanError("not included in your Basic (Trial) plan")
        return [None] * len(details)

    with patch("app.services.lead_enrichment.bulk_match", side_effect=flaky):
        reports = enrich_leads(db, leads)

    first_batch = [reports[lead.id]["status"] for lead in leads[:10]]
    second_batch = [reports[lead.id]["status"] for lead in leads[10:]]
    assert set(first_batch) == {"error"}
    assert set(second_batch) == {"no_match"}


def test_a_lead_with_nothing_to_match_on_is_skipped_not_sent(db):
    good = _lead(db)
    bare = _lead(db, first_name="", last_name="", linkedin_url="", linkedin_key="")

    with patch("app.services.lead_enrichment.bulk_match", return_value=[_person()]) as bm:
        reports = enrich_leads(db, [good, bare])

    assert len(bm.call_args_list[0].args[0]) == 1
    assert reports[bare.id]["status"] == "skipped"
    assert reports[good.id]["status"] == "enriched"


def test_each_result_is_identity_checked_so_a_shifted_row_writes_nothing(db):
    """The safety property: even if Apollo mis-orders its response, a lead only
    ever receives contact data that passes verify_identity against itself."""
    alice = _lead(db, first_name="Alice", last_name="Alpha",
                  linkedin_url="https://linkedin.com/in/alice-alpha",
                  linkedin_key="linkedin.com/in/alice-alpha", email=None)
    bob = _lead(db, first_name="Bob", last_name="Beta",
                linkedin_url="https://linkedin.com/in/bob-beta",
                linkedin_key="linkedin.com/in/bob-beta", email=None)

    # Deliberately swapped relative to the request order.
    swapped = [
        _person(first_name="Bob", last_name="Beta", linkedin_url="https://linkedin.com/in/bob-beta",
                email="bob@acme.com"),
        _person(first_name="Alice", last_name="Alpha", linkedin_url="https://linkedin.com/in/alice-alpha",
                email="alice@acme.com"),
    ]

    with patch("app.services.lead_enrichment.bulk_match", return_value=swapped):
        reports = enrich_leads(db, [alice, bob])

    assert reports[alice.id]["status"] == "identity_mismatch"
    assert reports[bob.id]["status"] == "identity_mismatch"
    assert alice.email is None and bob.email is None


def test_a_batch_commits_once_for_the_whole_run(db):
    leads = [
        _lead(db, first_name=f"P{i}", last_name="Example",
              linkedin_url=f"https://linkedin.com/in/p{i}", linkedin_key=f"linkedin.com/in/p{i}",
              email=None)
        for i in range(3)
    ]
    people = [
        _person(first_name=f"P{i}", linkedin_url=f"https://linkedin.com/in/p{i}", email=f"p{i}@acme.com")
        for i in range(3)
    ]

    with patch("app.services.lead_enrichment.bulk_match", return_value=people):
        with patch.object(db, "commit", wraps=db.commit) as commit:
            reports = enrich_leads(db, leads)

    assert commit.call_count == 1
    assert all(reports[lead.id]["status"] == "enriched" for lead in leads)
    assert [lead.email for lead in leads] == ["p0@acme.com", "p1@acme.com", "p2@acme.com"]


def test_nothing_is_committed_when_no_field_was_filled(db):
    lead = _lead(db)
    with patch("app.services.lead_enrichment.bulk_match", return_value=[None]):
        with patch.object(db, "commit", wraps=db.commit) as commit:
            enrich_leads(db, [lead])
    commit.assert_not_called()
