"""Thin client for Apollo's People Enrichment API.

Only the pieces the lead pipeline needs. Kept separate from lead_enrichment so
the matching/identity rules can be tested without any HTTP involved.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ApolloError(RuntimeError):
    """Any Apollo call that did not return a usable result."""


class ApolloNotConfigured(ApolloError):
    """No APOLLO_API_KEY set -- enrichment is disabled, which is a valid state."""


class ApolloUnavailable(ApolloError):
    """A transient failure -- transport error, rate limit, or an Apollo 5xx.

    Kept distinct from the terminal errors because "we could not reach Apollo"
    must never be reported as "enrichment works": the status probe treats this
    as an unknown verdict and retries soon, rather than caching a green light.
    """


class ApolloPlanError(ApolloError):
    """The key authenticates but the Apollo plan does not include this endpoint.

    Apollo's Basic/Trial plan returns 403 API_INACCESSIBLE for every people
    endpoint, so this is worth surfacing distinctly from a bad key -- the fix is
    a billing change, not a credential change.
    """


def _headers(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    settings = get_settings()
    if not settings.apollo_api_key:
        raise ApolloNotConfigured("APOLLO_API_KEY is not set.")

    url = f"{settings.apollo_base_url}{path}"
    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(settings.apollo_api_key),
            json=payload,
            timeout=settings.apollo_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ApolloUnavailable(f"Apollo request to {path} failed: {exc}") from exc

    if response.status_code == 403:
        raise ApolloPlanError(_error_message(response) or "Apollo plan does not allow this endpoint.")
    if response.status_code in (401, 422):
        raise ApolloError(f"Apollo rejected the request ({response.status_code}): {_error_message(response)}")
    if response.status_code == 429:
        raise ApolloUnavailable("Apollo rate limit reached; retry later.")
    if response.status_code >= 500:
        raise ApolloUnavailable(f"Apollo is unavailable ({response.status_code}); retry later.")
    if response.status_code >= 400:
        raise ApolloError(f"Apollo returned {response.status_code}: {response.text[:200]}")

    try:
        return response.json()
    except ValueError as exc:
        raise ApolloError(f"Apollo returned a non-JSON body for {path}.") from exc


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return response.text[:200]


def health() -> dict:
    """Confirms the key authenticates. Cheap and consumes no credits."""
    return _request("GET", "/api/v1/auth/health")


def match_person(
    *,
    linkedin_url: str | None = None,
    first_name: str = "",
    last_name: str = "",
    organization_name: str | None = None,
    reveal_personal_emails: bool = False,
) -> dict | None:
    """Looks up one person and returns Apollo's `person` object, or None if no match.

    `reveal_personal_emails` defaults to False: the SOP asks for professional
    contact details, and personal-email reveal costs extra credits. Phone reveal
    is deliberately not wired up -- Apollo delivers it asynchronously to a
    `webhook_url`, which this service has no endpoint for.
    """
    payload: dict = {"reveal_personal_emails": reveal_personal_emails}
    if linkedin_url:
        payload["linkedin_url"] = linkedin_url
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    if organization_name:
        payload["organization_name"] = organization_name

    if not linkedin_url and not (first_name and last_name):
        raise ApolloError("A LinkedIn URL, or both a first and last name, is required to match.")

    body = _request("POST", "/api/v1/people/match", payload)
    person = body.get("person")
    return person if isinstance(person, dict) else None


# Apollo caps a single bulk_match call at 10 people.
BULK_MATCH_MAX = 10


def _validate_detail(detail: dict, index: int) -> dict:
    """Applies the same identity requirement match_person enforces, per entry."""
    linkedin_url = detail.get("linkedin_url")
    first_name = detail.get("first_name")
    last_name = detail.get("last_name")
    if not linkedin_url and not (first_name and last_name):
        raise ApolloError(
            f"details[{index}] needs a LinkedIn URL, or both a first and last name, to match."
        )
    return {k: v for k, v in detail.items() if v}


def bulk_match(details: list[dict], *, reveal_personal_emails: bool = False) -> list[dict | None]:
    """Enriches up to 10 people in one call. Returns a list aligned with `details`.

    Ten times fewer HTTP calls than looping match_person, at the same credit cost
    (1 credit per person for demographics or email, and 0 when Apollo finds
    nothing). Phone reveal is deliberately not exposed here: it costs 8 further
    credits per person and requires a `webhook_url` this service has no endpoint
    for -- the same reason match_person omits it.

    An entry Apollo could not match comes back as None, so the caller can pair
    each result with the lead it was asked about by position. Callers must still
    identity-check every returned person: alignment is Apollo's promise, not a
    guarantee, and a shifted result would otherwise attach one lead's contact
    details to another (SOP s15).
    """
    if not details:
        return []
    if len(details) > BULK_MATCH_MAX:
        raise ApolloError(
            f"bulk_match accepts at most {BULK_MATCH_MAX} people per call; got {len(details)}."
        )

    payload = {
        "details": [_validate_detail(d, i) for i, d in enumerate(details)],
        "reveal_personal_emails": reveal_personal_emails,
    }
    body = _request("POST", "/api/v1/people/bulk_match", payload)

    matches = body.get("matches")
    if not isinstance(matches, list):
        raise ApolloError("Apollo returned no `matches` array for bulk_match.")

    # Pad or trim to the requested length so positional pairing is always safe.
    results: list[dict | None] = [m if isinstance(m, dict) else None for m in matches[: len(details)]]
    results.extend([None] * (len(details) - len(results)))
    return results
