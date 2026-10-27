"""Adds `category` and `subCategory` columns to a payload-shaped jobs CSV.

Input is the same shape send_csv_to_admin_api.py posts (company/role/tags/
description/...); output is that file plus the two new columns, written to a new
path so the source CSV is never modified in place.

    python3 categorize_jobs.py --csv jobs_api_ready.csv --out jobs_with_category.csv
    python3 categorize_jobs.py --csv jobs_api_ready.csv --out demo.csv --limit 10 --unique-roles
"""
import argparse
import csv
import sys
from collections import Counter

from build_taxonomy_csv import build_rows
from job_categories import classify
from job_industries import classify_industry, industry_id

csv.field_size_limit(10 ** 7)

INDUSTRY_FIELD = "industry"
CATEGORY_FIELD = "category"
SUBCATEGORY_FIELD = "subCategory"
NEW_FIELDS = [INDUSTRY_FIELD, CATEGORY_FIELD, SUBCATEGORY_FIELD]

# Every (industry, category, sub-category) triple the master taxonomy allows.
# A job whose triple is absent isn't wrong — Walmart really does employ
# pharmacists — it just means the master map doesn't list that function under
# that industry yet, which is worth reviewing.
VALID_TRIPLES = {
    (r["industry"], r["category"], r["sub_category"]) for r in build_rows()
}


def _clean_header(name):
    return (name or "").lstrip("﻿").strip().strip('"')


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [_clean_header(n) for n in (reader.fieldnames or [])]
        rows = [{_clean_header(k): v for k, v in row.items()} for row in reader]
    return fieldnames, rows


def categorize(row):
    category, sub_category, score = classify(
        role=row.get("role"),
        tags=row.get("tags"),
        description=row.get("description"),
        company=row.get("company"),
        employment_type=row.get("employmentType"),
        remote=row.get("remote"),
    )
    industry, source = classify_industry(
        company=row.get("company"),
        description=row.get("description"),
        category=category,
    )
    return industry, category, sub_category, score, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="payload-shaped input CSV")
    parser.add_argument("--out", required=True, help="new CSV to write (never overwrites --csv)")
    parser.add_argument("--limit", type=int, default=0, help="stop after N rows (0 = all)")
    parser.add_argument("--unique-roles", action="store_true",
                        help="keep only the first row per role — useful for demo samples")
    parser.add_argument("--show-score", action="store_true",
                        help="also write a categoryScore column (0 = no rule matched)")
    args = parser.parse_args()

    if args.out == args.csv:
        sys.exit("--out must differ from --csv")

    fieldnames, rows = read_rows(args.csv)
    out_fields = [f for f in fieldnames if f not in NEW_FIELDS]
    out_fields += NEW_FIELDS
    if args.show_score:
        out_fields += ["industryId", "industrySource", "categoryScore", "taxonomyMatch"]

    seen_roles, written = set(), []
    counts, industry_counts, source_counts, unmatched = Counter(), Counter(), Counter(), 0
    for row in rows:
        if args.unique_roles:
            key = (row.get("role") or "").strip().lower()
            if not key or key in seen_roles:
                continue
            seen_roles.add(key)
        industry, category, sub_category, score, source = categorize(row)
        row[INDUSTRY_FIELD] = industry
        row[CATEGORY_FIELD] = category
        row[SUBCATEGORY_FIELD] = sub_category
        if args.show_score:
            row["industryId"] = industry_id(industry)
            row["industrySource"] = source
            row["categoryScore"] = score
            row["taxonomyMatch"] = (
                "yes" if (industry, category, sub_category) in VALID_TRIPLES else "no"
            )
        counts[category] += 1
        industry_counts[industry] += 1
        source_counts[source] += 1
        if not score:
            unmatched += 1
        written.append({k: row.get(k, "") for k in out_fields})
        if args.limit and len(written) >= args.limit:
            break

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(written)

    print(f"wrote {len(written)} rows -> {args.out}")
    print(f"unmatched category (fell back to Other / General): {unmatched}")
    print("industry resolved by: " + ", ".join(
        f"{source}={count}" for source, count in source_counts.most_common()))
    print("industries:")
    for industry, count in industry_counts.most_common():
        print(f"  {count:>6}  {industry}")
    print("categories:")
    for category, count in counts.most_common():
        print(f"  {count:>6}  {category}")


if __name__ == "__main__":
    main()
