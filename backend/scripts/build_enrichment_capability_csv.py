"""Writes the enrichment capability matrix as CSV.

Answers "which master-sheet column can a vendor actually fill, at what cost, and
can we reach it today" -- the question that decides whether an Apollo plan
upgrade is worth paying for.

The Apollo/PDL columns are kept in step with the code that does the filling:
lead_enrichment._apply_match for Apollo, pdl_client.to_lead_fields for PDL. If
either changes, update the rows here too.

Usage (from backend/):
    python -m scripts.build_enrichment_capability_csv
"""

import csv
import pathlib

from app.api.routes.leads import SHEET_COLUMNS

OUT_DIR = pathlib.Path(__file__).resolve().parents[2]

# sheet column -> (apollo field, pdl field, who fills it, notes)
NONE = "--"

FIELD_CAPABILITY = {
    "Email": ("email", "work_email / recommended_personal_email", "vendor",
              "Apollo returns email_not_unlocked@domain.com for non-contacts; guarded in _professional_email"),
    "First Name": ("first_name", "first_name", "vendor", "Search endpoint obfuscates surnames as 'Do***e'; enrich does not"),
    "Last Name": ("last_name", "last_name", "vendor", "Required by verify_identity when no LinkedIn URL is present"),
    "LinkedIn URL": ("linkedin_url", "linkedin_url", "researcher",
                     "The dedup key (SOP s14/s19). Researcher supplies it; vendors confirm it"),
    "Direct Number": ("phone_numbers", NONE, "vendor (not wired)",
                      "+8 credits/person and needs a webhook_url this service has no endpoint for"),
    "City": ("city", "location_locality", "vendor", ""),
    "State": ("state", "location_region", "vendor", "Required by the s29 checklist"),
    "University": (NONE, "education[].school.name", "PDL only",
                   "Apollo carries no education history at any plan tier"),
    "Degree": (NONE, "education[].degrees", "PDL only", "Newest completed degree"),
    "Graduation Year": (NONE, "education[].end_date", "PDL only",
                        "The 'passed out' year -- the whole reason PDL is in the stack"),
    "Current/Target Role": ("title", "job_title", "vendor", ""),
    "Skills": (NONE, "skills (first 25)", "PDL only", ""),
    "F-1 Signal": (NONE, NONE, "researcher", "SOP s10: read only from the candidate's stated words"),
    "OPT Signal": (NONE, NONE, "researcher", "SOP s10. Drives the A/A+ grade"),
    "STEM OPT Signal": (NONE, NONE, "researcher", "SOP s10"),
    "CPT Signal": (NONE, NONE, "researcher", "SOP s10"),
    "Open-to-Work Signal": (NONE, NONE, "researcher",
                            "SOP s11. No lead can score above C without it"),
    "Indian Identity Publicly Stated": (NONE, NONE, "researcher",
                                        "SOP s13: public self-statement only, never inferred, never a filter"),
    "Job Search Evidence": (NONE, NONE, "researcher", "SOP s12"),
    "Evidence/Post URL": (NONE, NONE, "researcher", "SOP s12; dates the evidence for the 30-day A+ window"),
    "Email Source": (NONE, NONE, "system", "Set to 'Apollo' automatically when Apollo fills the email"),
    "Phone Source": (NONE, NONE, "researcher", "s29 requires it whenever a number is recorded"),
    "Lead Priority": (NONE, NONE, "system", "Computed by score_lead (A+/A/B/C)"),
    "Date Researched": (NONE, NONE, "researcher", "s29 required field"),
    "Researcher Name": (NONE, NONE, "researcher", "s29 required field"),
    "Contact Status": (NONE, NONE, "researcher", "Updated via PATCH /leads/{id}"),
    "Notes": (NONE, NONE, "researcher", ""),
}

# Reachability today, from the live probes run against the configured keys.
APOLLO_STATUS = "plan_blocked (Basic Trial); available on any paid plan"
PDL_STATUS = "available (key configured and verified against both endpoints)"


def write_field_matrix(path: pathlib.Path) -> int:
    rows = []
    for sheet_column, db_field in SHEET_COLUMNS:
        apollo, pdl, filled_by, notes = FIELD_CAPABILITY[sheet_column]
        rows.append({
            "Sheet Column": sheet_column,
            "DB Field": db_field,
            "Filled By": filled_by,
            "Apollo Field": apollo,
            "Apollo Reachable Today": "no" if apollo != NONE else "n/a",
            "PDL Field": pdl,
            "PDL Reachable Today": "yes" if pdl != NONE else "n/a",
            "Notes": notes,
        })
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


ENDPOINTS = [
    {
        "Provider": "Apollo", "Endpoint": "POST /api/v1/people/match",
        "Purpose": "Enrich one person", "Credits": "1 (demographics or email); +8 if mobile phone; 0 on no match",
        "Batch Max": "1", "Returns Email": "yes", "Returns Education": "no",
        "Limits": "600 calls/hour", "Status": APOLLO_STATUS, "Used By Pipeline": "yes - enrich_lead",
    },
    {
        "Provider": "Apollo", "Endpoint": "POST /api/v1/people/bulk_match",
        "Purpose": "Enrich up to 10 people", "Credits": "1-9 per person; 0 on no match",
        "Batch Max": "10", "Returns Email": "yes", "Returns Education": "no",
        "Limits": "10 people/call; phone reveal needs webhook_url", "Status": APOLLO_STATUS,
        "Used By Pipeline": "yes - enrich_leads",
    },
    {
        "Provider": "Apollo", "Endpoint": "POST /api/v1/mixed_people/search",
        "Purpose": "Source a worklist", "Credits": "0",
        "Batch Max": "100/page", "Returns Email": "no (has_email boolean only)", "Returns Education": "no",
        "Limits": "50,000 records max (500 pages); surnames obfuscated as 'Do***e'; no education params",
        "Status": APOLLO_STATUS, "Used By Pipeline": "no - cannot reach fresh grads",
    },
    {
        "Provider": "Apollo", "Endpoint": "GET /api/v1/people/{id}",
        "Purpose": "Full person record", "Credits": "1",
        "Batch Max": "1", "Returns Email": "placeholder only for non-contacts", "Returns Education": "no",
        "Limits": "email_not_unlocked@domain.com unless saved as a contact", "Status": APOLLO_STATUS,
        "Used By Pipeline": "no - adds nothing over people/match",
    },
    {
        "Provider": "Apollo", "Endpoint": "GET /api/v1/auth/health",
        "Purpose": "Key check", "Credits": "0", "Batch Max": "n/a",
        "Returns Email": "no", "Returns Education": "no",
        "Limits": "Passes on every plan - proves nothing about entitlement",
        "Status": "available", "Used By Pipeline": "yes - apollo_status",
    },
    {
        "Provider": "PDL", "Endpoint": "GET /v5/person/enrich",
        "Purpose": "Enrich one person", "Credits": "charged per match only; 0 on no match",
        "Batch Max": "1", "Returns Email": "yes", "Returns Education": "yes",
        "Limits": "Needs LinkedIn URL, email, or name+context", "Status": PDL_STATUS,
        "Used By Pipeline": "client exists; not wired into enrich_lead",
    },
    {
        "Provider": "PDL", "Endpoint": "POST /v5/person/search",
        "Purpose": "Source recent graduates", "Credits": "paid tier",
        "Batch Max": "100/call", "Returns Email": "yes", "Returns Education": "yes",
        "Limits": "SQL dialect rejects long OR-chained LIKE clauses",
        "Status": PDL_STATUS + "; fresher query currently matches 36,441 people",
        "Used By Pipeline": "demo script only",
    },
]


def write_endpoint_matrix(path: pathlib.Path) -> int:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ENDPOINTS[0]))
        writer.writeheader()
        writer.writerows(ENDPOINTS)
    return len(ENDPOINTS)


if __name__ == "__main__":
    fields = OUT_DIR / "enrichment_field_capabilities.csv"
    endpoints = OUT_DIR / "enrichment_endpoint_capabilities.csv"
    print(f"{write_field_matrix(fields)} rows -> {fields}")
    print(f"{write_endpoint_matrix(endpoints)} rows -> {endpoints}")
