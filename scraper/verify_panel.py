"""Audits every job in the admin panel against the rules we committed to.

Run counts prove what a script did, not what is actually stored — a PATCH that
returned 200 on a field the DTO silently dropped looks identical to success. So
this re-reads the live panel and checks the data itself.

Five invariants, each pass/fail with the offending ids listed:

  1. every job carries industry, category and subcategory
  2. industry is one of the 48 agreed industries
  3. category + subcategory is a pair that exists in the master taxonomy
  4. every job has at least MIN_TAGS tags
  5. no duplicates among active jobs — no repeated sourceLink, and among URL-less
     rows no repeated company + role + location (repair_panel.py deactivates the
     extra copies, so inactive ones no longer count)

Exits non-zero if anything fails, so it can gate a cron.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict

import requests

import admin_auth
from prepare_jobs import parse_tags, MIN_TAGS
from taxonomy_master import CATEGORY_TREE, INDUSTRIES

API_BASE = admin_auth.API_ROOT + "/jobs"
VALID_PAIRS = {(c, s) for c, subs in CATEGORY_TREE.items() for s in subs}
SUB_FIELD = "subcategory"


def get_with_retry(headers, params, attempts=6):
    """One page request that survives short network drops (DNS/connection errors)."""
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(API_BASE, headers=headers, params=params, timeout=60)
        except requests.ConnectionError as e:
            if attempt == attempts:
                raise
            wait = 15 * attempt
            print(f"  [retry] {type(e).__name__}; attempt {attempt}/{attempts}, waiting {wait}s", flush=True)
            time.sleep(wait)


def fetch_all(page_size=200):
    jobs, cursor, page = [], None, 0
    headers = admin_auth.auth_headers()
    while True:
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        response = get_with_retry(headers, params)
        if response.status_code == 401:
            headers = admin_auth.auth_headers(force=True)
            continue
        response.raise_for_status()
        body = response.json()
        batch = body if isinstance(body, list) else (
            body.get("items") or body.get("data") or body.get("jobs") or [])
        if not batch:
            break
        jobs.extend(batch)
        page += 1
        if page % 20 == 0:
            print(f"  ...{len(jobs)} fetched", flush=True)
        cursor = body.get("nextCursor") if isinstance(body, dict) else None
        if not cursor:
            break
    return jobs


def audit(jobs):
    failures = defaultdict(list)

    for job in jobs:
        industry = (job.get("industry") or "").strip()
        category = (job.get("category") or "").strip()
        sub = (job.get(SUB_FIELD) or "").strip()

        if not industry or not category or not sub:
            failures["1. missing one of the three columns"].append(job["id"])
            continue
        if industry not in INDUSTRIES:
            failures["2. industry not in the 48"].append(f"{job['id']} ({industry})")
        if (category, sub) not in VALID_PAIRS:
            failures["3. category/subcategory not a valid pair"].append(
                f"{job['id']} ({category} / {sub})")

    for job in jobs:
        if len(parse_tags(job.get("tags"))) < MIN_TAGS:
            failures[f"4. fewer than {MIN_TAGS} tags"].append(job["id"])

    urls = Counter()
    triples = Counter()
    for job in jobs:
        if str(job.get("active", "")).lower() not in ("true", "1"):
            continue
        url = (job.get("sourceLink") or "").strip().lower()
        if url:
            urls[url] += 1
        else:
            triples[((job.get("company") or "").strip().lower(),
                     (job.get("role") or "").strip().lower(),
                     (job.get("location") or "").strip().lower())] += 1
    dup_url = sum(v - 1 for v in urls.values() if v > 1)
    dup_triple = sum(v - 1 for v in triples.values() if v > 1)
    if dup_url:
        failures["5a. duplicate sourceLink (active)"].append(f"{dup_url} redundant rows")
    if dup_triple:
        failures["5b. duplicate company+role+location (no URL, active)"].append(
            f"{dup_triple} redundant rows")

    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-file", help="audit a saved snapshot instead of fetching")
    parser.add_argument("--save", help="write the fetched snapshot here")
    parser.add_argument("--show", type=int, default=5, help="ids to list per failure")
    parser.add_argument("--panel", choices=("primary", "mirror"), default="primary",
                        help="which admin panel to audit (default: primary)")
    args = parser.parse_args()

    global API_BASE
    admin_auth.use_panel(args.panel)
    API_BASE = admin_auth.API_ROOT + "/jobs"

    if args.jobs_file:
        jobs = json.load(open(args.jobs_file))
        print(f"auditing snapshot {args.jobs_file}: {len(jobs)} jobs")
    else:
        print(f"fetching live {args.panel} panel ({API_BASE})...")
        try:
            jobs = fetch_all()
        except requests.RequestException as e:
            print(f"CANNOT VERIFY: panel unreachable ({type(e).__name__})")
            sys.exit(2)      # 2 = could not check; 1 = checked and found problems
        print(f"fetched {len(jobs)} jobs")
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(jobs, f)

    failures = audit(jobs)
    print()
    checks = [
        "1. missing one of the three columns",
        "2. industry not in the 48",
        "3. category/subcategory not a valid pair",
        f"4. fewer than {MIN_TAGS} tags",
        "5a. duplicate sourceLink (active)",
        "5b. duplicate company+role+location (no URL, active)",
    ]
    for check in checks:
        bad = failures.get(check, [])
        mark = "PASS" if not bad else "FAIL"
        detail = "" if not bad else f"  ({len(bad)})"
        print(f"  [{mark}] {check}{detail}")
        for item in bad[:args.show]:
            print(f"          {item}")
        if len(bad) > args.show:
            print(f"          ... and {len(bad) - args.show} more")

    total = sum(len(v) for v in failures.values())
    print()
    print(f"{len(jobs)} jobs audited — {'ALL CHECKS PASS' if not total else f'{total} problems'}")
    sys.exit(0 if not total else 1)


if __name__ == "__main__":
    main()
