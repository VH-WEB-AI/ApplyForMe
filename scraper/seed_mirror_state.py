"""Builds the mirror panel's dedup state file from the jobs it already holds.

The mirror (ADMIN_API_MIRROR_URL) started life as a partial copy of the primary
panel, so the primary's admin_sent_jobs.json says nothing about what the mirror
has. This reads every job on the mirror (active and inactive) and records the
same keys send_csv_to_admin_api.dedup_keys builds, so a later post never
duplicates a job that is already there.

Read-only against the panel. Merges into an existing state file rather than
replacing it, so running it again is always safe.

    python3 seed_mirror_state.py            # write the state file
    python3 seed_mirror_state.py --dry-run  # just count
"""
import argparse
import os
import sys
import time
from urllib.parse import urlsplit

import requests

from send_csv_to_admin_api import DEFAULT_MIRROR_STATE, MIRROR_URL, dedup_keys, load_state, save_state


def mirror_api_root():
    """https://admin.yorrp.com/api/jobs/intake -> https://admin.yorrp.com/api"""
    parts = urlsplit(MIRROR_URL)
    path = parts.path.split("/jobs")[0]
    return f"{parts.scheme}://{parts.netloc}{path}"


def login(api_root):
    email = os.environ.get("ADMIN_MIRROR_EMAIL", "").strip()
    password = os.environ.get("ADMIN_MIRROR_PASSWORD", "").strip()
    if not email or not password:
        sys.exit("ADMIN_MIRROR_EMAIL / ADMIN_MIRROR_PASSWORD not set in .env")
    response = requests.post(f"{api_root}/auth/login",
                             json={"email": email, "password": password}, timeout=30)
    response.raise_for_status()
    return response.json()["accessToken"]


def fetch_all(api_root, page_size=200):
    token = login(api_root)
    jobs, cursor = [], None
    while True:
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        for attempt in range(1, 6):
            try:
                response = requests.get(f"{api_root}/jobs", params=params, timeout=60,
                                        headers={"Authorization": f"Bearer {token}"})
            except requests.RequestException as exc:
                print(f"  [retry {attempt}/5] {type(exc).__name__}", flush=True)
                time.sleep(3 * attempt)
                continue
            if response.status_code == 401:
                token = login(api_root)
                continue
            if response.status_code == 429:
                time.sleep(5 * attempt)
                continue
            break
        response.raise_for_status()
        body = response.json()
        jobs.extend(body.get("items") or [])
        print(f"  fetched {len(jobs)} / {body.get('totalCount', '?')}", flush=True)
        cursor = body.get("nextCursor")
        if not cursor:
            return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", default=DEFAULT_MIRROR_STATE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not MIRROR_URL:
        sys.exit("ADMIN_API_MIRROR_URL is not set")

    api_root = mirror_api_root()
    print(f"mirror={api_root} state={args.state_path}")
    jobs = fetch_all(api_root)

    keys = load_state(args.state_path)
    before = len(keys)
    for job in jobs:
        keys.update(dedup_keys(job))
    print(f"jobs={len(jobs)} keys_before={before} keys_after={len(keys)}")

    if not args.dry_run:
        save_state(args.state_path, keys)
        print(f"wrote {args.state_path}")


if __name__ == "__main__":
    main()
