"""Thin client for People Data Labs, used to build fresher shortlists.

Why this exists alongside apollo_client: Apollo is a B2B contact database keyed
to people who already work somewhere, and carries no education history at any
plan tier. The SOP's target -- a recent graduate who has *not* started work --
is close to invisible in it. PDL keeps an `education` array (school, degrees,
majors, start/end dates), which is what makes "passed out in 2025/2026"
expressible as a query at all.

Scope note: PDL supplies the *sourcing* half of the master sheet. It holds no
work-authorization data and no nationality, so F-1/OPT/STEM OPT/CPT signals,
Open-to-Work, and Indian-identity still come only from a researcher reading what
the candidate publicly stated (SOP s10/s13). See build_fresher_sql for the
guard that keeps those out of the query.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# PDL's canonical major names that map onto the SOP s3 job categories.
SOFTWARE_MAJORS = (
    "computer science",
    "computer engineering",
    "software engineering",
    "information technology",
    "information systems",
    "data science",
    "electrical engineering",
)

# SOP s2 is US-only; India is covered here because lead sourcing was extended to
# both. Note that an India-located candidate is not on F-1/OPT by definition, so
# the visa-signal columns simply do not apply to them.
SUPPORTED_COUNTRIES = ("united states", "india")

# Degree types that mean "graduated", as opposed to executive-education and
# certificate courses which also carry a recent end_date and otherwise flood the
# results with senior people (a CTO finishing a Wharton course in 2025 matched
# the first version of this query).
FRESHER_DEGREES = ("bachelors", "masters")

# PDL's seniority buckets for someone at the start of their career. This is the
# single highest-value precision filter: without it the shortlist is ~97% noise.
ENTRY_LEVELS = ("entry", "training")

# SOP s13 / s23 rule 2: nationality, ethnicity, religion and caste are never
# filter criteria. Any query containing these is a bug, not a feature.
FORBIDDEN_FILTER_TERMS = (
    "nationality",
    "ethnicity",
    "ethnic",
    "religion",
    "caste",
    "race",
    "indian_identity",
)


class PDLError(RuntimeError):
    """Any PDL call that did not return a usable result."""


class PDLNotConfigured(PDLError):
    """No PDL_API_KEY set -- sourcing via PDL is disabled, which is a valid state."""


class PDLPlanError(PDLError):
    """The key authenticates but the plan does not include this endpoint.

    Person Search in particular is not part of the free tier, so this is worth
    surfacing distinctly from a bad key -- the fix is billing, not credentials.
    """


def _headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key, "Accept": "application/json"}


def _request(method: str, path: str, *, params: dict | None = None, payload: dict | None = None) -> dict:
    settings = get_settings()
    if not settings.pdl_api_key:
        raise PDLNotConfigured("PDL_API_KEY is not set.")

    url = f"{settings.pdl_base_url}{path}"
    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(settings.pdl_api_key),
            params=params,
            json=payload,
            timeout=settings.pdl_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise PDLError(f"PDL request to {path} failed: {exc}") from exc

    # 404 is PDL's "no match" and costs no credit -- the caller decides what that
    # means, so it is not an error here.
    if response.status_code == 404:
        return {}
    if response.status_code == 401:
        raise PDLError("PDL rejected the API key (401).")
    if response.status_code == 402:
        raise PDLPlanError("PDL reports insufficient credits (402).")
    if response.status_code == 403:
        raise PDLPlanError(_error_message(response) or "PDL plan does not allow this endpoint.")
    if response.status_code == 429:
        raise PDLError("PDL rate limit reached; retry later.")
    if response.status_code >= 400:
        raise PDLError(f"PDL returned {response.status_code}: {response.text[:200]}")

    try:
        return response.json()
    except ValueError as exc:
        raise PDLError(f"PDL returned a non-JSON body for {path}.") from exc


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, list):
            return "; ".join(str(m) for m in message)
        return str(message or "")
    return str(error or "")


def _sql_str(value: str) -> str:
    """Single-quotes a literal for PDL's SQL dialect, escaping embedded quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def build_fresher_sql(
    *,
    countries: tuple[str, ...] = SUPPORTED_COUNTRIES,
    grad_year_from: int = 2025,
    grad_year_to: int = 2026,
    majors: tuple[str, ...] = SOFTWARE_MAJORS,
    degrees: tuple[str, ...] = FRESHER_DEGREES,
    entry_level_only: bool = True,
    engineering_only: bool = True,
    require_linkedin: bool = True,
) -> str:
    """Builds the PDL SQL that shortlists recent Software/IT graduates.

    Precision matters far more than reach here (SOP s28: 100 qualified leads beat
    1,000 unverified ones). Measured against live data, education filters alone
    return ~1.2M people and are mostly useless -- a recent `education.end_date`
    catches executive-education and certificate courses, so senior directors and
    CTOs dominate the results. Adding the degree-type and seniority filters cuts
    that to ~35K and the samples become junior engineers and interns.

    Uses `IN` and date comparisons rather than chained `OR ... LIKE` clauses:
    PDL's SQL parser rejects a long OR-chain of LIKE with "Unable to parse input
    SQL query", a 400 that is easy to misread as a plan restriction.

    Note `inferred_years_experience` is deliberately not used: it comes back null
    on this dataset and null still satisfies a `<= 3` comparison, so it silently
    filters nothing.

    Trade-off worth knowing: `entry_level_only` keys off the *current job title*,
    so a graduate with no job at all -- exactly the SOP's target -- has no
    seniority to match and drops out. Set it False to widen, and expect to sift.
    """
    if not countries:
        raise PDLError("At least one country is required.")
    if grad_year_to < grad_year_from:
        raise PDLError("grad_year_to must not be earlier than grad_year_from.")

    clauses = [
        f"location_country IN ({', '.join(_sql_str(c) for c in countries)})",
        # end_date is stored as 'YYYY' or 'YYYY-MM'; string comparison against a
        # full date bound orders correctly for both.
        f"education.end_date >= {_sql_str(f'{grad_year_from}-01-01')}",
        f"education.end_date <= {_sql_str(f'{grad_year_to}-12-31')}",
    ]
    if degrees:
        clauses.append(f"education.degrees IN ({', '.join(_sql_str(d) for d in degrees)})")
    if majors:
        clauses.append(f"education.majors IN ({', '.join(_sql_str(m) for m in majors)})")
    if entry_level_only:
        clauses.append(f"job_title_levels IN ({', '.join(_sql_str(l) for l in ENTRY_LEVELS)})")
    if engineering_only:
        clauses.append("job_title_role = 'engineering'")
    if require_linkedin:
        # The SOP's dedup key (s19) and every manual verification step need it.
        clauses.append("linkedin_url IS NOT NULL")

    sql = "SELECT * FROM person WHERE " + " AND ".join(clauses)
    assert_no_identity_filter(sql)
    return sql


def assert_no_identity_filter(sql: str) -> None:
    """Guards SOP s13: the query must never select people by who they are.

    Nationality is recorded only from a public self-statement, so it can never
    be an input to sourcing. This raises rather than warns because a query that
    slipped such a filter through would quietly produce discriminatory targeting.
    """
    lowered = sql.lower()
    for term in FORBIDDEN_FILTER_TERMS:
        if term in lowered:
            raise PDLError(
                f"Refusing to run a query filtering on {term!r}: SOP s13 forbids "
                "selecting candidates by nationality, ethnicity, religion or caste."
            )


def search_people(sql: str, *, size: int = 10, scroll_token: str | None = None) -> dict:
    """Runs a Person Search. Returns {records, total, scroll_token}.

    Person Search is a paid-tier endpoint; a free key raises PDLPlanError here.
    """
    assert_no_identity_filter(sql)
    payload: dict = {"sql": sql, "size": max(1, min(size, 100))}
    if scroll_token:
        payload["scroll_token"] = scroll_token

    body = _request("POST", "/v5/person/search", payload=payload)
    return {
        "records": body.get("data") or [],
        "total": body.get("total", 0),
        "scroll_token": body.get("scroll_token"),
    }


# PDL rejects a bare name: it requires identity plus context. Any one of these,
# alongside a name, forms a valid request.
NAME_CONTEXT_PARAMS = ("company", "school", "location", "region", "country", "locality")


def enrich_person(
    *,
    linkedin_url: str | None = None,
    email: str | None = None,
    first_name: str = "",
    last_name: str = "",
    min_likelihood: int | None = None,
    **context,
) -> dict | None:
    """Looks up one person and returns PDL's `data` object, or None if no match.

    A LinkedIn URL is the most reliable input and is what the SOP already stores,
    so prefer it. Falling back to a name requires context -- PDL returns 400
    "Does not meet minimum combination of required data points" for a bare
    first/last name, unlike Apollo which accepts one. PDL charges per match only,
    so a None costs nothing.
    """
    unknown = set(context) - set(NAME_CONTEXT_PARAMS)
    if unknown:
        raise PDLError(f"Unsupported enrichment parameter(s): {', '.join(sorted(unknown))}.")

    settings = get_settings()
    params: dict = {"min_likelihood": min_likelihood or settings.pdl_min_likelihood}

    if linkedin_url:
        params["profile"] = linkedin_url
    elif email:
        params["email"] = email
    elif first_name and last_name:
        supplied = {k: v for k, v in context.items() if v}
        if not supplied:
            raise PDLError(
                "PDL needs a name plus context: pass one of "
                f"{', '.join(NAME_CONTEXT_PARAMS)} alongside first_name/last_name."
            )
        params["first_name"] = first_name
        params["last_name"] = last_name
        params.update(supplied)
    else:
        raise PDLError("A LinkedIn URL, an email, or a name plus context is required to enrich.")

    body = _request("GET", "/v5/person/enrich", params=params)
    data = body.get("data")
    return data if isinstance(data, dict) else None


def to_lead_fields(record: dict) -> dict:
    """Maps a PDL person onto the master-sheet columns PDL can legitimately fill.

    The returned keys match ResearchLead attribute names, and the shape is also
    what lead_enrichment.verify_identity expects, so a PDL record goes through
    the same identity check as an Apollo one before anything is written.

    Every read goes through _text/_items because PDL returns the boolean `false`
    -- not null -- for fields your plan does not include, so a naive .strip()
    raises AttributeError on live data the moment a plan-gated field appears.
    """
    education = _items(record.get("education"))
    newest = _newest_education(education)
    school = newest.get("school") if isinstance(newest.get("school"), dict) else {}

    return {
        "first_name": _text(record.get("first_name")) or "",
        "last_name": _text(record.get("last_name")) or "",
        "linkedin_url": _text(record.get("linkedin_url")) or "",
        "email": _text(record.get("work_email")) or _text(record.get("recommended_personal_email")),
        "city": _text(record.get("location_locality")),
        "state": _text(record.get("location_region")),
        "country": _text(record.get("location_country")),
        "university": _text(school.get("name")),
        "degree": _first(newest.get("degrees")),
        "major": _first(newest.get("majors")),
        "graduation_year": _year(newest.get("end_date")),
        "target_role": _text(record.get("job_title")) or "",
        "skills": [str(s) for s in _items(record.get("skills"))[:25]],
        # verify_identity reads this name on the incoming record.
        "title": _text(record.get("job_title")) or "",
    }


def _text(value) -> str | None:
    """PDL uses `false` for plan-gated fields, so anything non-string is absent."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _items(value) -> list:
    return value if isinstance(value, list) else []


def _newest_education(education: list) -> dict:
    """The most recently finished degree -- that is the 'passed out' year."""
    entries = [e for e in education if isinstance(e, dict)]
    dated = [e for e in entries if _text(e.get("end_date"))]
    if not dated:
        return entries[0] if entries else {}
    return max(dated, key=lambda e: str(e.get("end_date")))


def _first(values) -> str | None:
    items = _items(values)
    return _text(str(items[0])) if items else None


def _year(end_date) -> int | None:
    text = _text(end_date)
    if not text:
        return None
    head = text[:4]
    return int(head) if head.isdigit() else None
