"""Runs the signal-first funnel over a researcher's CSV and writes the master sheet.

Order matters: qualification runs first and costs nothing, so a profile that
could never be submitted -- no Open-to-Work signal, a recruiter, a broken
LinkedIn URL -- is dropped before a single enrichment credit is spent.

Input CSV columns (only linkedin_url and evidence_text are load-bearing):
    linkedin_url, evidence_text, evidence_url, evidence_date, researcher_name,
    target_role, state

Usage (from backend/):
    python -m scripts.triage_leads researched.csv --out master_sheet.csv
    python -m scripts.triage_leads researched.csv --dry-run   # no credits spent
"""

import argparse
import csv
import pathlib
import sys
from datetime import date, datetime

from app.api.routes.leads import SHEET_COLUMNS
from app.services import pdl_client
from app.services.lead_triage import ResearchInput, grade, merge_enrichment, qualify


def _parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _read_inputs(path: pathlib.Path) -> list[ResearchInput]:
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"{path} has no rows.")
    if "linkedin_url" not in rows[0]:
        raise SystemExit(f"{path} needs a `linkedin_url` column; found {list(rows[0])}.")
    return [
        ResearchInput(
            linkedin_url=(r.get("linkedin_url") or "").strip(),
            evidence_text=(r.get("evidence_text") or "").strip(),
            evidence_url=(r.get("evidence_url") or "").strip(),
            evidence_date=_parse_date(r.get("evidence_date", "")),
            researcher_name=(r.get("researcher_name") or "").strip(),
            target_role=(r.get("target_role") or "").strip(),
            state=(r.get("state") or "").strip(),
        )
        for r in rows
    ]


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("master_sheet.csv"))
    parser.add_argument("--dry-run", action="store_true", help="Qualify only; never call PDL.")
    args = parser.parse_args()

    entries = _read_inputs(args.input)
    results = [qualify(e) for e in entries]

    qualified = [r for r in results if r.qualified]
    rejected = [r for r in results if not r.qualified]

    print(f"read {len(entries)} researched profiles")
    print(f"  qualified : {len(qualified)}")
    print(f"  rejected  : {len(rejected)}  (no credits spent on these)")
    for r in rejected:
        print(f"    - {r.linkedin_key or '<invalid url>'}: {r.rejections[0]}")

    # Deduplicate on the SOP s19 key before spending anything.
    seen: set[str] = set()
    unique = []
    for r in qualified:
        if r.linkedin_key in seen:
            print(f"  duplicate skipped: {r.linkedin_key}")
            continue
        seen.add(r.linkedin_key)
        unique.append(r)

    if args.dry_run:
        print("\n--dry-run: stopping before enrichment.")
    else:
        print(f"\nenriching {len(unique)} profiles via PDL (1 credit each on a match)...")
        for r in unique:
            try:
                record = pdl_client.enrich_person(linkedin_url=r.values["linkedin_url"])
            except pdl_client.PDLError as exc:
                print(f"  {r.linkedin_key}: enrichment failed -- {exc}")
                continue
            merge_enrichment(r, pdl_client.to_lead_fields(record) if record else None)

    for r in unique:
        grade(r)

    with args.out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for label, _ in SHEET_COLUMNS] + ["Grade Reasons", "Blocking Checklist Items"])
        for r in unique:
            writer.writerow(
                [_cell(r.values.get(field)) for _, field in SHEET_COLUMNS]
                + ["; ".join(r.grade_reasons), "; ".join(r.checklist)]
            )

    grades: dict[str, int] = {}
    for r in unique:
        grades[r.grade] = grades.get(r.grade, 0) + 1
    submittable = sum(1 for r in unique if not r.checklist)

    print(f"\nwrote {len(unique)} rows -> {args.out}")
    print(f"  grades      : {', '.join(f'{g}={n}' for g, n in sorted(grades.items()))}")
    print(f"  submittable : {submittable}/{len(unique)} (no blocking checklist items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
