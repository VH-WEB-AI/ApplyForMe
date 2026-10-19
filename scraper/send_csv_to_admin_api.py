"""POSTs an already-payload-shaped CSV to the admin job-intake API.

Different from job_scraper.post_rows_to_admin_api: that one takes raw scraped
rows (Portal/Job Title/Company/URL/Pay/...) and re-derives skills, tags and
seniority itself. This one takes a CSV whose columns are already the API's own
field names (company/role/platform/.../sourceLink/active) and sends those
values as-is, so previously-computed tags and skills survive intact.

Dedup keys and the state file are shared with the scraper on purpose: a job
sent by either path must not be re-sent by the other.

Every row goes to each configured panel: the primary (ADMIN_API_URL) and, when
ADMIN_API_MIRROR_URL is set, the mirror. Each panel keeps its own state file,
because the two databases hold different sets of jobs — a job the primary
already has may still be missing from the mirror.
"""
import argparse
import csv
import json
import os
import sys
import time

import requests
import re


def _load_env_file(path):
    """Reads KEY=VALUE lines from .env into os.environ without pulling in
    python-dotenv, which isn't in requirements.txt — the scraper itself is run
    with the environment already loaded (docker-compose env_file / run_loop.sh).
    Existing environment variables win, so a shell override still applies."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(os.path.join(os.path.dirname(__file__), ".env"))

DEFAULT_CSV = "/home/vhits/applyForMe/jobs.csv"
DEFAULT_STATE = os.path.join(os.path.dirname(__file__), "admin_sent_jobs.json")

API_URL = os.environ.get("ADMIN_API_URL", "").strip()
API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "").strip()
API_TIMEOUT = int(os.environ.get("ADMIN_API_TIMEOUT", "30"))

MIRROR_URL = os.environ.get("ADMIN_API_MIRROR_URL", "").strip()
# Both panels run the same backend and accept the same intake key.
MIRROR_TOKEN = os.environ.get("ADMIN_API_MIRROR_TOKEN", "").strip() or API_TOKEN
DEFAULT_MIRROR_STATE = (os.environ.get("ADMIN_API_MIRROR_STATE_PATH", "").strip()
                        or os.path.join(os.path.dirname(__file__), "admin_sent_jobs.mirror.json"))

# The intake route is throttled at 60 requests / 60s (jobs.controller.ts), so
# anything faster than one per second earns a 429 and a backoff anyway.
REQUEST_DELAY = 1.1
RATE_LIMIT_DELAY = float(os.environ.get("ADMIN_API_RATE_LIMIT_DELAY", "5.0"))
MAX_RETRIES = int(os.environ.get("ADMIN_API_MAX_RETRIES", "6"))

# Limits copied from CreateJobDto. Exceeding any of them is a 400 for the whole
# request, so values are clamped here rather than discovered one failure at a time.
MAX_COMPANY = 160
MAX_ROLE = 160
MAX_LOCATION = 160
MAX_SENIORITY = 80
MAX_EMPLOYMENT_TYPE = 60
MAX_DESCRIPTION = 8000
MAX_PLATFORM = 80
MAX_SOURCE_LINK = 500
MAX_SKILLS, MAX_SKILL_LEN = 50, 80
MAX_TAGS, MAX_TAG_LEN = 100, 50
# CreateJobDto caps these at 100, not the 160 the other text fields get.
MAX_INDUSTRY = 100
MAX_CATEGORY = 100
MAX_SUBCATEGORY = 100


def _clean_header(name):
    """Strips the UTF-8 BOM and stray quoting Excel leaves on the first column."""
    return (name or "").lstrip("﻿").strip().strip('"')


def _text(value, limit):
    return (value or "").strip()[:limit]


def _boolean(value):
    return str(value or "").strip().lower() in ("true", "1", "yes", "y")


def _integer(value):
    """The DTO wants a non-negative int; anything unparseable is simply omitted."""
    raw = str(value or "").strip().replace(",", "")
    if not raw:
        return None
    try:
        number = int(float(raw))
    except ValueError:
        return None
    return number if number >= 0 else None


def _string_list(value, max_items, max_len):
    seen, out = set(), []
    for part in str(value or "").split(","):
        item = part.strip()[:max_len]
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _extract_tags_from_text(text, max_new, existing_lower):
    """Return up to `max_new` candidate tags from free text, excluding any
    tags whose lowercase form is in `existing_lower`.

    This is deliberately simple: split on non-word chars, ignore short words
    and a small stopword set, prefer longer tokens, and title-case the result.
    """
    if not text or max_new <= 0:
        return []
    STOP = {
        "the", "and", "for", "with", "from", "that", "this", "are",
        "was", "were", "have", "has", "not", "your", "you", "our",
        "its", "but", "who", "what", "where", "when", "why",
        "a", "an", "in", "on", "at", "by", "to", "of", "or",
    }
    words = re.findall(r"[A-Za-z0-9&+#\-]+", text or "")
    candidates = []
    seen = set(existing_lower or [])
    for w in words:
        lw = w.lower()
        if lw in seen:
            continue
        if lw in STOP:
            continue
        if len(lw) < 4:
            continue
        # drop mostly-numeric tokens
        if sum(c.isdigit() for c in lw) / max(1, len(lw)) > 0.6:
            continue
        seen.add(lw)
        candidates.append(w.title())
        if len(candidates) >= max_new:
            break
    return candidates


def row_to_payload(row, wrap_lists=False):
    """Maps one CSV row onto the CreateJobDto body. Returns None if the row is
    missing company or role, which the DTO requires."""
    company = _text(row.get("company"), MAX_COMPANY)
    role = _text(row.get("role"), MAX_ROLE)
    if not company or not role:
        return None
    # The description goes up whole or not at all — cutting it would post an
    # incomplete job, so an over-limit row is refused like any other invalid one.
    if len((row.get("description") or "").strip()) > MAX_DESCRIPTION:
        return None

    skills = _string_list(row.get("requiredSkills"), MAX_SKILLS, MAX_SKILL_LEN)
    tags = _string_list(row.get("tags"), MAX_TAGS, MAX_TAG_LEN)
    # Tags are built upstream (job_keywords.py); the gate holds a row that has too few. The old
    # padding here appended random Title-Cased words from the description, which is where junk
    # tags like 'Order' and 'Written' came from.
    # job_scraper wraps these as {"items": [...]}; the checked-in DTO declares
    # bare arrays. --wrap-lists exists so the run can switch without an edit.
    if wrap_lists:
        skills, tags = {"items": skills}, {"items": tags}

    payload = {
        "company": company,
        "role": role,
        "location": _text(row.get("location"), MAX_LOCATION),
        "remote": _boolean(row.get("remote")),
        "seniority": _text(row.get("seniority"), MAX_SENIORITY),
        "visaSponsorship": _boolean(row.get("visaSponsorship")),
        "employmentType": _text(row.get("employmentType"), MAX_EMPLOYMENT_TYPE),
        "description": (row.get("description") or "").strip() or role,
        "requiredSkills": skills,
        "tags": tags,
        "platform": _text(row.get("platform"), MAX_PLATFORM),
        "active": _boolean(row.get("active")),
    }

    # Taxonomy fields. The CSV column is "subCategory" but the API field is
    # "subcategory" (all lowercase) — and the DTO runs whitelist:true, so a
    # camelCase key is dropped silently rather than rejected. Getting this wrong
    # costs the sub-category on every row with no error to show for it.
    # Sent only when present, so untagged CSVs behave exactly as before.
    for csv_field, api_field, limit in (
        ("industry", "industry", MAX_INDUSTRY),
        ("category", "category", MAX_CATEGORY),
        ("subCategory", "subcategory", MAX_SUBCATEGORY),
    ):
        value = _text(row.get(csv_field), limit)
        if value:
            payload[api_field] = value

    salary_min, salary_max = _integer(row.get("salaryMin")), _integer(row.get("salaryMax"))
    if salary_min is not None:
        payload["salaryMin"] = salary_min
    if salary_max is not None:
        payload["salaryMax"] = salary_max

    # @IsUrl rejects a blank string, so the key is dropped rather than sent empty.
    link = _text(row.get("sourceLink"), MAX_SOURCE_LINK)
    if link.startswith(("http://", "https://")):
        payload["sourceLink"] = link

    return payload


def dedup_keys(row):
    """Same keys job_scraper._admin_dedup_keys builds, so the two senders share
    one state file: the source URL, plus company::role for cross-posted jobs."""
    url = (row.get("sourceLink") or "").strip().lower()
    # A URL is a job's identity, so it is the whole key when present — and it
    # already covers everything sent before, since the old scheme recorded the
    # URL too. Keying on company::role as well would block the second store's
    # "Pharmacy Technician" as a duplicate of the first, which it is not.
    if url:
        return [url]
    company = (row.get("company") or "").strip().lower()
    role = (row.get("role") or "").strip().lower()
    location = (row.get("location") or "").strip().lower()
    if company and role:
        return [f"{company}::{role}::{location}"]
    return []


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def save_state(path, keys):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f)


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [
            {_clean_header(k): v for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def post_one(target, payload, index):
    """POSTs one payload to one panel, retrying throttles and network errors.
    Returns True on success."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(target["url"], json=payload, headers=target["headers"],
                                 timeout=API_TIMEOUT)
            if resp.ok:
                return True

            if resp.status_code == 429:
                wait = RATE_LIMIT_DELAY * attempt
                print(f"[{target['name']}][throttle] row {index} attempt {attempt}/{MAX_RETRIES}: "
                      f"sleeping {wait}s")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                    continue

            print(f"[{target['name']}][fail] row {index}: {payload['role']!r} @ {payload['company']!r} "
                  f"-> {resp.status_code} {resp.text[:400]}")
            return False
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                print(f"[{target['name']}][retry] row {index} attempt {attempt}/{MAX_RETRIES}: {e}")
                time.sleep(REQUEST_DELAY * attempt)
                continue
            print(f"[{target['name']}][fail] row {index}: {e}")
            return False
    return False


def build_targets(args):
    targets = []
    if args.target in ("all", "primary"):
        if not args.dry_run and not API_URL:
            sys.exit("ADMIN_API_URL is not set")
        targets.append({"name": "primary", "url": API_URL, "token": API_TOKEN,
                        "state_path": args.state_path})
    if args.target in ("all", "mirror"):
        if MIRROR_URL:
            targets.append({"name": "mirror", "url": MIRROR_URL, "token": MIRROR_TOKEN,
                            "state_path": args.mirror_state_path})
        elif args.target == "mirror":
            sys.exit("ADMIN_API_MIRROR_URL is not set")

    for target in targets:
        target["headers"] = {"Content-Type": "application/json"}
        if target["token"]:
            target["headers"]["x-jobs-key"] = target["token"]
        target["sent_keys"] = load_state(target["state_path"])
        target["sent"] = target["skipped"] = target["failed"] = 0
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--state-path", default=DEFAULT_STATE)
    parser.add_argument("--mirror-state-path", default=DEFAULT_MIRROR_STATE)
    parser.add_argument("--target", choices=("all", "primary", "mirror"), default="all",
                        help="which panel(s) to post to (default: every configured one)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N rows posted to at least one panel (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and count payloads without calling the API")
    parser.add_argument("--wrap-lists", action="store_true",
                        help='send requiredSkills/tags as {"items": [...]} instead of bare arrays')
    args = parser.parse_args()

    targets = build_targets(args)
    if not targets:
        sys.exit("no panel configured")

    rows = read_rows(args.csv)
    print(f"csv={args.csv} rows={len(rows)} mode={'DRY RUN' if args.dry_run else 'LIVE POST'} "
          f"wrap_lists={args.wrap_lists}")
    for t in targets:
        print(f"  {t['name']}: url={t['url']} state={t['state_path']} known_keys={len(t['sent_keys'])}")
    print()

    invalid = rows_done = 0
    for index, row in enumerate(rows, 1):
        keys = dedup_keys(row)
        pending = []
        for t in targets:
            if any(k in t["sent_keys"] for k in keys):
                t["skipped"] += 1
            else:
                pending.append(t)
        if not pending:
            continue

        payload = row_to_payload(row, wrap_lists=args.wrap_lists)
        if payload is None:
            invalid += 1
            continue

        rows_done += 1
        if args.dry_run:
            for t in pending:
                t["sent"] += 1
            if rows_done <= 2:
                print(json.dumps(payload, indent=2)[:1500])
        else:
            results = []
            for t in pending:
                if post_one(t, payload, index):
                    t["sent"] += 1
                    t["sent_keys"].update(keys)
                    save_state(t["state_path"], t["sent_keys"])
                    results.append(f"{t['name']}=ok")
                else:
                    t["failed"] += 1
                    results.append(f"{t['name']}=FAIL")
            print(f"[row {index}] {payload['role']!r} @ {payload['company']!r} -> {' '.join(results)}")
            # Each panel throttles independently, so one pause per row keeps
            # every panel under its 60/min limit.
            time.sleep(REQUEST_DELAY)

        if args.limit and rows_done >= args.limit:
            break

    print(f"\ninvalid={invalid}")
    for t in targets:
        print(f"{t['name']}: sent={t['sent']} skipped_dup={t['skipped']} failed={t['failed']} "
              f"state now holds {len(t['sent_keys'])} keys")


if __name__ == "__main__":
    main()
