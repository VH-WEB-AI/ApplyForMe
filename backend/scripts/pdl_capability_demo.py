"""Live capability probe for the configured People Data Labs key.

Same job as scripts/apollo_capability_demo.py, and written for the same reason:
that script is what proved Apollo could never fill University / Degree /
Graduation Year, which saved paying for a plan upgrade that would not have
helped. Run this before buying a PDL plan.

It reports which endpoints the key can reach, runs one real search if the plan
allows it, and prints a coverage table of the SOP s20 master-sheet columns split
into "an API can fill this" and "only a researcher can fill this".

Person Search is a paid-tier endpoint. On the free tier this script still tells
you something useful: it confirms the key works, shows the shortlist SQL it
would run, and prints the coverage table from PDL's published schema.

Usage (from the backend/ directory):
    python -m scripts.pdl_capability_demo
"""

import json

from app.services import pdl_client
from app.services.pdl_client import (
    PDLError,
    PDLNotConfigured,
    PDLPlanError,
    build_fresher_sql,
    to_lead_fields,
)

# SOP s20 columns, split by who can actually supply them.
API_FILLABLE = [
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("LinkedIn URL", "linkedin_url"),
    ("Email", "work_email / recommended_personal_email"),
    ("Direct Number", "mobile_phone (separate paid dataset)"),
    ("City", "location_locality"),
    ("State", "location_region"),
    ("University", "education[].school.name"),
    ("Degree", "education[].degrees"),
    ("Graduation Year", "education[].end_date"),
    ("Current/Target Role", "job_title"),
    ("Skills", "skills"),
]

RESEARCHER_ONLY = [
    ("F-1 Signal", "SOP s10 -- public self-statement only"),
    ("OPT Signal", "PDL holds no work-authorization data"),
    ("STEM OPT Signal", "PDL holds no work-authorization data"),
    ("CPT Signal", "PDL holds no work-authorization data"),
    ("Open-to-Work Signal", "SOP s11 -- LinkedIn badge / recent post"),
    ("Job Search Evidence", "SOP s11 -- quoted from the candidate"),
    ("Evidence/Post URL", "SOP s12 -- link to the post"),
    ("Indian Identity Publicly Stated", "SOP s13 -- NEVER inferred, never a filter"),
    ("Lead Priority", "derived by lead_qualification.score_lead"),
    ("Date Researched", "recorded by the researcher"),
    ("Researcher Name", "recorded by the researcher"),
    ("Contact Status", "outreach workflow"),
    ("Notes", "researcher"),
    ("Email Source", "recorded when the email is taken"),
    ("Phone Source", "recorded when the phone is taken"),
]


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# A syntactically valid profile URL that cannot match anyone: confirms the
# endpoint answers without spending a credit, because PDL bills per match.
UNMATCHABLE_PROFILE = "https://www.linkedin.com/in/zzzq-nonexistentperson-000000"


def probe_access() -> dict[str, bool]:
    _rule("1. ENDPOINT ACCESS")
    access = {"enrich": False, "search": False}

    print(f"{'':4}{'ENDPOINT':<24}{'RESULT'}")
    print("-" * 78)
    try:
        pdl_client.enrich_person(linkedin_url=UNMATCHABLE_PROFILE)
        access["enrich"] = True
        print(f"{'OK  '}{'person/enrich':<24}reachable (no match, no credit spent)")
    except PDLNotConfigured as exc:
        raise SystemExit(f"{exc} Set it in backend/.env, then re-run.")
    except PDLPlanError as exc:
        print(f"{'BLK '}{'person/enrich':<24}PLAN -- {exc}")
    except PDLError as exc:
        # Not a plan limit: a bad request is our bug, and saying "upgrade your
        # plan" here would send you to buy something you already have.
        print(f"{'ERR '}{'person/enrich':<24}REQUEST -- {exc}")

    try:
        result = pdl_client.search_people(build_fresher_sql(), size=1)
        access["search"] = True
        print(f"{'OK  '}{'person/search':<24}reachable -- {result['total']:,} people match the shortlist")
    except PDLPlanError as exc:
        print(f"{'BLK '}{'person/search':<24}PLAN -- {exc}")
    except PDLError as exc:
        print(f"{'ERR '}{'person/search':<24}REQUEST -- {exc}")

    return access


def show_query() -> None:
    _rule("2. THE SHORTLIST QUERY")
    print("US-based and India-based Software/IT graduates of 2025-2026:\n")
    print(build_fresher_sql())
    print("\nNote what is NOT in this query: no visa filter (PDL has none) and no")
    print("nationality filter (SOP s13 forbids it -- pdl_client.assert_no_identity_filter")
    print("raises if one is ever introduced). This is a shortlist, not a lead list.")


def demo_search(available: bool) -> None:
    _rule("3. SAMPLE SHORTLIST RECORD")
    if not available:
        print("Person Search is not on this plan, so no live sample.")
        print("On the free tier you can still enrich a known LinkedIn URL one at a time:")
        print('  pdl_client.enrich_person(linkedin_url="https://www.linkedin.com/in/...")')
        return

    result = pdl_client.search_people(build_fresher_sql(), size=3)
    for record in result["records"]:
        fields = to_lead_fields(record)
        print(f"\n--- {fields['first_name']} {fields['last_name']} ---")
        for key in ("university", "degree", "major", "graduation_year", "city", "state",
                    "country", "target_role", "linkedin_url"):
            if fields.get(key):
                print(f"  {key:<18}{fields[key]}")
        print(f"  {'skills':<18}{', '.join(fields['skills'][:8]) or '-'}")


def coverage_table() -> None:
    _rule("4. SOP MASTER-SHEET COVERAGE")
    print(f"\nPDL CAN supply ({len(API_FILLABLE)} columns):")
    for column, field in API_FILLABLE:
        print(f"  + {column:<34}<- {field}")

    print(f"\nONLY a researcher can supply ({len(RESEARCHER_ONLY)} columns):")
    for column, why in RESEARCHER_ONLY:
        print(f"  - {column:<34}{why}")

    total = len(API_FILLABLE) + len(RESEARCHER_ONLY)
    pct = round(100 * len(API_FILLABLE) / total)
    print(f"\nSo PDL automates roughly {pct}% of the sheet by column count -- the sourcing")
    print("half. Every qualification signal in SOP s10-s13 still requires reading the")
    print("profile. Buying data does not remove the manual verification step.")


def main() -> None:
    access = probe_access()
    show_query()
    demo_search(access["search"])
    coverage_table()

    _rule("VERDICT")
    if access["search"]:
        print("Person Search is AVAILABLE. You can generate shortlists in bulk; route")
        print("each one through the SOP s3-s13 manual checks before it becomes a lead.")
    elif access["enrich"]:
        print("Enrichment works but Person Search did not answer. Check whether the")
        print("line above says PLAN (a billing limit) or REQUEST (a bug in the query)")
        print("before concluding you need to upgrade anything.")
    else:
        print("Neither endpoint is reachable. Check the key before spending anything.")


if __name__ == "__main__":
    main()
