"""Live capability probe for the configured Apollo API key.

Shows exactly which Apollo endpoints the current plan/key can reach, and dumps a
real extraction from each one that works, so you can see the shape of the data
before deciding whether to pay for a plan upgrade.

Re-run this after changing the Apollo plan -- the people-enrichment section will
start returning data instead of a 403, and the field-coverage table will show
which of the SOP's master-sheet columns Apollo can actually fill.

All person-lookup probes use a deliberately unmatchable fake name, so this
script never pulls a real individual's personal data and never spends a
person-enrichment credit.

Usage (from the backend/ directory, inside the container or the local venv):
    python -m scripts.apollo_capability_demo
"""

import json

import httpx

from app.config import get_settings

FAKE_PERSON = {"first_name": "Zzzq", "last_name": "Nonexistentperson"}

# Public company domains -- org enrichment is the one people-free endpoint the
# Basic/Trial plan allows, so this is what a trial key can actually extract.
DEMO_DOMAINS = ["apollo.io", "stripe.com"]

# SOP s20 master-sheet columns, mapped to the Apollo person field that could
# fill them. "--" means Apollo has no equivalent at any plan tier.
SOP_FIELD_MAP = [
    ("Email", "email"),
    ("Direct Number", "phone_numbers (needs reveal_phone_number + webhook)"),
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("LinkedIn URL", "linkedin_url"),
    ("City", "city"),
    ("State", "state"),
    ("Current/Target Role", "title"),
    ("University", "-- not in Apollo's B2B dataset"),
    ("Degree", "--"),
    ("Graduation Year", "--"),
    ("Skills", "--"),
    ("F-1 Signal", "-- Apollo holds no work-authorization data"),
    ("OPT Signal", "--"),
    ("STEM OPT Signal", "--"),
    ("CPT Signal", "--"),
    ("Open-to-Work Signal", "-- Apollo holds no job-seeking status"),
    ("Job Search Evidence", "--"),
    ("Evidence/Post URL", "--"),
    ("Indian Identity Publicly Stated", "-- public self-statement only (SOP s13)"),
]


def _client() -> tuple[httpx.Client, str]:
    settings = get_settings()
    if not settings.apollo_api_key:
        raise SystemExit("APOLLO_API_KEY is not set -- nothing to probe.")
    headers = {
        "x-api-key": settings.apollo_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return httpx.Client(headers=headers, timeout=settings.apollo_timeout_seconds), settings.apollo_base_url


def _call(client: httpx.Client, base: str, method: str, path: str, body: dict | None = None):
    try:
        response = client.request(method, base + path, json=body)
    except httpx.HTTPError as exc:
        return None, f"transport error: {exc}"
    try:
        payload = response.json()
    except ValueError:
        return response.status_code, response.text[:200]
    return response.status_code, payload


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def probe_access(client: httpx.Client, base: str) -> dict[str, bool]:
    """Which endpoints answer, and why the blocked ones are blocked."""
    _rule("1. ENDPOINT ACCESS")
    probes = [
        ("auth/health",            "GET",  "/api/v1/auth/health",            None),
        ("people/match",           "POST", "/api/v1/people/match",           FAKE_PERSON),
        ("people/bulk_match",      "POST", "/api/v1/people/bulk_match",      {"details": [FAKE_PERSON]}),
        ("mixed_people/search",    "POST", "/api/v1/mixed_people/search",    {"per_page": 1}),
        ("contacts/search",        "POST", "/api/v1/contacts/search",        {"per_page": 1}),
        ("organizations/enrich",   "POST", "/api/v1/organizations/enrich",   {"domain": "apollo.io"}),
        ("mixed_companies/search", "POST", "/api/v1/mixed_companies/search", {"per_page": 1}),
    ]
    access: dict[str, bool] = {}
    print(f"{'':4}{'ENDPOINT':<24}{'CODE':<6}REASON")
    print("-" * 78)
    for name, method, path, body in probes:
        status, payload = _call(client, base, method, path, body)
        ok = status == 200
        access[name] = ok
        reason = "accessible"
        if not ok and isinstance(payload, dict):
            message = str(payload.get("error", ""))
            if "not included in your" in message:
                reason = "PLAN — needs a paid Apollo plan"
            elif "not authorized" in message:
                reason = "KEY SCOPE — check the key's permissions in Apollo"
            else:
                reason = message[:44] or "blocked"
        print(f"{'OK  ' if ok else 'BLK '}{name:<24}{str(status):<6}{reason}")
    return access


def demo_org_extraction(client: httpx.Client, base: str) -> None:
    """The only real extraction a Basic/Trial key can perform."""
    _rule("2. WHAT YOUR KEY CAN EXTRACT TODAY — organization enrichment")
    for domain in DEMO_DOMAINS:
        status, payload = _call(client, base, "POST", "/api/v1/organizations/enrich", {"domain": domain})
        if status != 200 or not isinstance(payload, dict):
            print(f"\n{domain}: blocked ({status})")
            continue
        org = payload.get("organization") or {}
        print(f"\n--- {domain} ---")
        for field in ("name", "website_url", "linkedin_url", "industry", "estimated_num_employees",
                      "city", "state", "country", "founded_year", "phone"):
            value = org.get(field)
            if value not in (None, "", []):
                print(f"  {field:<26}{value}")
        print(f"  {'(total fields returned)':<26}{len(org)}")
    print("\nNote: this is COMPANY data. It contains no candidate emails, no phone")
    print("numbers for individuals, and nothing about work authorization.")


def demo_person_extraction(client: httpx.Client, base: str, available: bool) -> None:
    _rule("3. WHAT YOU ACTUALLY NEED — person enrichment (people/match)")
    if not available:
        status, payload = _call(client, base, "POST", "/api/v1/people/match", FAKE_PERSON)
        message = payload.get("error") if isinstance(payload, dict) else payload
        print(f"BLOCKED ({status}): {message}\n")
        print("Once a paid plan is active this section will run a real lookup and")
        print("print the returned person object's field names. Until then, the")
        print("coverage table below is from Apollo's published API reference.")
        return

    status, payload = _call(client, base, "POST", "/api/v1/people/match", FAKE_PERSON)
    person = (payload or {}).get("person") if isinstance(payload, dict) else None
    print(f"Endpoint reachable (status {status}).")
    print(f"Unmatchable probe returned person = {person!r} (expected: None).")
    print("\nRun scripts/apollo_capability_demo.py with a REAL lead's LinkedIn URL")
    print("to inspect a populated person object:")
    print('  match_person(linkedin_url="https://www.linkedin.com/in/...")')


def coverage_table() -> None:
    _rule("4. SOP MASTER-SHEET COVERAGE — what Apollo can and cannot fill")
    fillable = [(c, f) for c, f in SOP_FIELD_MAP if not f.startswith("--")]
    unfillable = [(c, f) for c, f in SOP_FIELD_MAP if f.startswith("--")]

    print(f"\nApollo CAN supply ({len(fillable)} columns, paid plan only):")
    for column, field in fillable:
        print(f"  + {column:<34}<- person.{field}")

    print(f"\nApollo CANNOT supply ({len(unfillable)} columns — LinkedIn research only):")
    for column, field in unfillable:
        print(f"  - {column:<34}{field.lstrip('- ')}")

    print("\nCredit cost when it does work:")
    print("  1 credit  — demographics or email")
    print("  8 credits — mobile phone (async, requires a webhook_url endpoint)")


def main() -> None:
    client, base = _client()
    try:
        access = probe_access(client, base)
        demo_org_extraction(client, base)
        demo_person_extraction(client, base, available=access.get("people/match", False))
        coverage_table()
        _rule("VERDICT")
        if access.get("people/match"):
            print("Person enrichment is AVAILABLE. Run one live lookup against a real")
            print("lead to confirm the parser in app/services/lead_enrichment.py.")
        else:
            print("Person enrichment is BLOCKED. Your key can currently extract company")
            print("data only — zero candidate contact details. The lead pipeline runs")
            print("fully without it; only Email and Direct Number stay manual.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
