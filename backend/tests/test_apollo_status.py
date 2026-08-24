"""Covers the Apollo entitlement probe: the verdict it reports and the caching
that keeps a billable endpoint from being polled once per page render."""

import pytest

from app.api.routes import leads
from app.services.apollo_client import (
    ApolloError,
    ApolloNotConfigured,
    ApolloPlanError,
    ApolloUnavailable,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    leads.reset_apollo_status_cache()
    yield
    leads.reset_apollo_status_cache()


def _patch(monkeypatch, *, health, match=None):
    monkeypatch.setattr(leads, "apollo_health", health)
    monkeypatch.setattr(leads, "match_person", match or (lambda **kw: None))


def test_missing_key_reports_not_configured(monkeypatch):
    def health():
        raise ApolloNotConfigured("APOLLO_API_KEY is not set.")

    _patch(monkeypatch, health=health)
    result = leads.apollo_status()
    assert result["status"] == "not_configured"
    assert result["configured"] is False
    assert result["enrichmentAvailable"] is False


def test_trial_plan_reports_plan_blocked(monkeypatch):
    """The live Basic (Trial) case: auth/health passes, people/match 403s."""

    def match(**kwargs):
        raise ApolloPlanError("The api/v1/people/match API is not included in your Basic (Trial) plan")

    _patch(monkeypatch, health=lambda: {"healthy": True}, match=match)
    result = leads.apollo_status()
    assert result["status"] == "plan_blocked"
    assert result["configured"] is True
    assert result["enrichmentAvailable"] is False
    assert "Basic (Trial)" in result["detail"]


def test_bad_key_reports_auth_failed(monkeypatch):
    def health():
        raise ApolloError("Apollo rejected the request (401): ")

    _patch(monkeypatch, health=health)
    assert leads.apollo_status()["status"] == "auth_failed"


def test_unreachable_apollo_is_unknown_not_available(monkeypatch):
    """The defect this guards: a transport failure must never read as green."""

    def match(**kwargs):
        raise ApolloUnavailable("Apollo request to /api/v1/people/match failed: timeout")

    _patch(monkeypatch, health=lambda: {"healthy": True}, match=match)
    result = leads.apollo_status()
    assert result["status"] == "unknown"
    assert result["enrichmentAvailable"] is False


def test_rate_limit_is_unknown_not_available(monkeypatch):
    def match(**kwargs):
        raise ApolloUnavailable("Apollo rate limit reached; retry later.")

    _patch(monkeypatch, health=lambda: {"healthy": True}, match=match)
    assert leads.apollo_status()["status"] == "unknown"


def test_no_match_still_counts_as_available(monkeypatch):
    """The probe name is meant not to match; that proves entitlement, not failure."""
    _patch(monkeypatch, health=lambda: {"healthy": True}, match=lambda **kw: None)
    result = leads.apollo_status()
    assert result["status"] == "available"
    assert result["enrichmentAvailable"] is True


def test_verdict_is_cached_so_the_billable_probe_runs_once(monkeypatch):
    calls = {"health": 0, "match": 0}

    def health():
        calls["health"] += 1
        return {"healthy": True}

    def match(**kwargs):
        calls["match"] += 1
        return None

    _patch(monkeypatch, health=health, match=match)

    first = leads.apollo_status()
    second = leads.apollo_status()

    assert first["cached"] is False
    assert second["cached"] is True
    assert second["status"] == first["status"] == "available"
    assert calls == {"health": 1, "match": 1}


def test_unknown_verdict_is_retried_sooner_than_a_settled_one(monkeypatch):
    """A settled verdict is trusted for 10 minutes; an unknown one for 30s."""
    monkeypatch.setattr(leads, "_TRANSIENT_STATUS_TTL_SECONDS", 0.0)

    def match(**kwargs):
        raise ApolloUnavailable("timeout")

    _patch(monkeypatch, health=lambda: {"healthy": True}, match=match)
    assert leads.apollo_status()["status"] == "unknown"
    # TTL of zero has already expired, so the next call re-probes rather than
    # serving the cached unknown.
    assert leads.apollo_status()["cached"] is False
