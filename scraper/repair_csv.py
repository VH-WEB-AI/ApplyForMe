#!/usr/bin/env python3
"""
One-off repair tool for a jobs CSV that already has mojibake / Excel-encoding
problems (e.g. an older jobs_output.csv). Re-cleans every cell and re-writes
it with a UTF-8 BOM so Excel opens it correctly, without needing to re-scrape
anything.

Usage:
    python3 repair_csv.py jobs_output.csv jobs_output_fixed.csv
"""
import csv
import sys
from job_scraper import fix_mojibake, clean_text, one_line, is_junk_title, FIELDNAMES

SINGLE_LINE_FIELDS = {"Portal", "Job Title", "Company", "URL", "Scraped At"}


def repair(in_path, out_path):
    with open(in_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    kept, dropped = 0, 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n"
        )
        writer.writeheader()
        for row in rows:
            cleaned = {}
            for field in FIELDNAMES:
                val = row.get(field, "") or ""
                cleaned[field] = one_line(val) if field in SINGLE_LINE_FIELDS else clean_text(val)
            # A title that's already garbage (e.g. it's literally the portal
            # name, like "Jobspresso" -> "Jobspresso") can't be repaired after
            # the fact - the real title was never captured. Drop those rows
            # instead of keeping obviously-wrong data.
            if is_junk_title(cleaned["Job Title"], cleaned["Portal"]):
                dropped += 1
                continue
            writer.writerow(cleaned)
            kept += 1

    print(f"Repaired {kept} rows -> {out_path}")
    if dropped:
        print(f"Dropped {dropped} row(s) with unrecoverable junk titles "
              f"(re-run job_scraper.py to get these right from source)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 repair_csv.py <input.csv> <output.csv>")
        sys.exit(1)
    repair(sys.argv[1], sys.argv[2])