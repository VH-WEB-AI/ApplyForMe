"""Fixes, on the live panel, what verify_panel.py reports — so the daily check is
followed by a daily fix instead of the same problem count every day.

  1-3. missing / invalid industry, category, subcategory
       -> taxonomy_repair.repair_row, the same repair the daily gate runs. A job it
          can't give a valid triple is listed as unresolved, never guessed.
  4.   fewer than MIN_TAGS tags
       -> job_keywords.build_matching_fields, the gate's tag builder. Existing tags
          are kept and added to, never replaced.
  5.   duplicates among active jobs (same sourceLink; or no link and the same
       company + role + location)
       -> the best copy stays (most tags, then longest description, then oldest);
          the others are deactivated, not deleted: DELETE is a hard delete with no
          undo, and this runs unattended.

    python3 repair_panel.py                     # plan only: print what would change
    python3 repair_panel.py --apply             # PATCH the primary panel
    python3 repair_panel.py --panel mirror --apply

Only fields that are actually wrong are sent. More than --max-deactivate duplicates
in one run aborts the dedup step: that many means something upstream broke.
"""
import argparse
import json
import sys
import time
from collections import defaultdict

import requests

import admin_auth
import verify_panel
from job_keywords import build_matching_fields
from prepare_jobs import parse_tags, MIN_TAGS
from taxonomy_repair import is_good, repair_row

MAX_TAGS, MAX_TAG_LEN = 100, 50     # CreateJobDto limits (send_csv_to_admin_api.py)


def _is_active(job):
    return str(job.get("active", "")).lower() in ("true", "1")


def _taxonomy_ok(job):
    """The verify_panel checks 1-3."""
    industry = (job.get("industry") or "").strip()
    category = (job.get("category") or "").strip()
    sub = (job.get("subcategory") or "").strip()
    return (industry and category and sub and industry in verify_panel.INDUSTRIES
            and (category, sub) in verify_panel.VALID_PAIRS)


def fix_fields(job):
    """(update dict, unresolved reason or None) for one job's taxonomy and tags."""
    row = {"role": job.get("role"), "company": job.get("company"), "description": job.get("description"),
           "tags": ", ".join(parse_tags(job.get("tags"))), "platform": job.get("platform"),
           "location": job.get("location"), "remote": job.get("remote"),
           "industry": (job.get("industry") or "").strip(), "category": (job.get("category") or "").strip(),
           "subCategory": (job.get("subcategory") or "").strip()}
    update, unresolved = {}, None
    if not _taxonomy_ok(job):
        if repair_row(row) == "unresolved" or not is_good((row["industry"], row["category"], row["subCategory"])):
            unresolved = "taxonomy"
        else:
            update.update(industry=row["industry"], category=row["category"], subcategory=row["subCategory"])
    tags = parse_tags(job.get("tags"))
    if len(tags) < MIN_TAGS:
        new, _skills = build_matching_fields(row, row["subCategory"], row["category"])
        merged, seen = [], set()
        for tag in tags + new:
            tag = tag.strip()[:MAX_TAG_LEN]
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                merged.append(tag)
        if len(merged) >= MIN_TAGS:
            update["tags"] = merged[:MAX_TAGS]
        else:
            unresolved = ",".join(filter(None, [unresolved, f"tags_{len(merged)}_of_{MIN_TAGS}"]))
    return update, unresolved


def duplicates(jobs):
    """Ids to deactivate: every active copy but the best one in each duplicate group."""
    groups = defaultdict(list)
    for job in jobs:
        if not _is_active(job):
            continue
        url = (job.get("sourceLink") or "").strip().lower()
        key = ("url", url) if url else ("triple", (job.get("company") or "").strip().lower(),
                                        (job.get("role") or "").strip().lower(),
                                        (job.get("location") or "").strip().lower())
        groups[key].append(job)
    losers = []
    for group in groups.values():
        if len(group) < 2:
            continue
        # most tags, then longest description, then oldest createdAt (the original, not the re-post)
        group.sort(key=lambda j: (-len(parse_tags(j.get("tags"))), -len(j.get("description") or ""),
                                  j.get("createdAt") or "9999"))
        losers.extend(group[1:])
    return losers


def patch(job_id, body, headers):
    """PATCH with the retry pattern expire_jobs.py uses. Returns (ok, headers)."""
    for attempt in range(1, 7):
        try:
            response = requests.patch(f"{verify_panel.API_BASE}/{job_id}", json=body,
                                      headers=headers, timeout=30)
        except requests.RequestException as exc:
            print(f"  [retry {attempt}/6] {type(exc).__name__}", flush=True)
            time.sleep(10 * attempt)
            continue
        if response.status_code == 401:
            headers = admin_auth.auth_headers(force=True)
            continue
        if response.status_code == 429:
            time.sleep(5 * attempt)
            continue
        if response.ok or response.status_code == 404:     # 404: already gone from the panel
            return True, headers
        print(f"  FAILED {job_id}: {response.status_code} {response.text[:200]}", file=sys.stderr)
        return False, headers
    return False, headers


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--panel", choices=("primary", "mirror"), default="primary")
    parser.add_argument("--apply", action="store_true", help="PATCH the panel (default: plan only)")
    parser.add_argument("--jobs-file", help="plan against a saved snapshot instead of fetching")
    parser.add_argument("--max-deactivate", type=int, default=1000)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()

    admin_auth.use_panel(args.panel)
    verify_panel.API_BASE = admin_auth.API_ROOT + "/jobs"
    if args.jobs_file:
        jobs = json.load(open(args.jobs_file))
    else:
        print(f"fetching {args.panel} panel ({verify_panel.API_BASE})...", flush=True)
        jobs = verify_panel.fetch_all()
    print(f"{len(jobs)} jobs")

    fixes, unresolved = [], []
    for job in jobs:
        if _taxonomy_ok(job) and len(parse_tags(job.get("tags"))) >= MIN_TAGS:
            continue
        update, reason = fix_fields(job)
        if update:
            fixes.append((job, update))
        if reason:
            unresolved.append((job, reason))
    losers = duplicates(jobs)

    print(f"plan: {len(fixes)} to fix (taxonomy/tags), {len(losers)} duplicate(s) to deactivate, "
          f"{len(unresolved)} unresolved")
    for job, update in fixes[:5]:
        print(f"  fix  {job['id']}  {(job.get('role') or '')[:45]:<45} -> {sorted(update)}")
    for job, reason in unresolved[:10]:
        print(f"  left {job['id']}  {(job.get('role') or '')[:45]:<45} ({reason})")
    if len(losers) > args.max_deactivate:
        print(f"  {len(losers)} duplicates is over --max-deactivate {args.max_deactivate}: "
              f"skipping dedup, check the panel by hand", file=sys.stderr)
        losers = []
    if not args.apply:
        print("plan only (pass --apply to write)")
        return

    headers = admin_auth.auth_headers()
    done = {"fixed": 0, "deactivated": 0, "failed": 0}
    for job, update in fixes:
        ok, headers = patch(job["id"], update, headers)
        done["fixed" if ok else "failed"] += 1
        time.sleep(args.delay)
    for job in losers:
        ok, headers = patch(job["id"], {"active": False}, headers)
        done["deactivated" if ok else "failed"] += 1
        time.sleep(args.delay)
    print(f"applied: {done}")
    sys.exit(1 if done["failed"] else 0)


if __name__ == "__main__":
    main()
