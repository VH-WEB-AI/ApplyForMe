"""Replaces truncated descriptions with the full posting text from the source's own
public API/page. Read-only against job sites, no API keys, never touches the panels.

  USAJOBS    -> the scraper only gets the search API's summary fields, so the Duties,
                Requirements and Conditions sections are often missing. Handled by
                fetch_full_descriptions.py (announcement page), run per batch.
  Greenhouse -> some boards return only the company blurb. Each row is compared with
                boards-api.greenhouse.io; when more than 2 substantive API lines are
                missing from our text, the API text replaces it. Complete rows are left
                alone, because the scrape legitimately adds pay ranges and benefits.
  SmartRecruiters, Ashby -> the scraper reads their detail APIs, but when that call fails
                (rate limit, DNS) the row keeps whatever partial text it had. Same check
                against api.smartrecruiters.com/.../postings/{id} (jobAd.sections) and
                api.ashbyhq.com/posting-api/job-board/{org} (matched on the job id).

    refetch_ats(rows) -> {"greenhouse": {"checked": n, "replaced": n, "no_api": n}, ...}
"""
import html
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import requests

from fetch_full_descriptions import html_to_text, trim

MAX_MISSING_LINES = 2     # more than this many absent API lines => the description is incomplete
MIN_LINE_CHARS = 25
SR_SECTIONS = ("jobDescription", "qualifications")   # what job_scraper.py builds a SmartRecruiters description from


def _get(session, url, timeout=20, attempts=4):
    """GET that waits out rate limits (429) and network blips; None if it never succeeds."""
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException:
            response = None
        if response is not None and response.status_code != 429 and response.status_code < 500:
            return response
        if attempt < attempts:
            time.sleep(3 * attempt)
    return response


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _board_candidates(row):
    """(board tokens to try, job id) from the row's link; careers-site links
    (?gh_jid=) fall back to guesses derived from the company name."""
    url = row.get("sourceLink") or ""
    parsed = urlparse(url)
    job_id = (parse_qs(parsed.query).get("gh_jid") or [None])[0]
    path = [p for p in parsed.path.split("/") if p]
    if "greenhouse.io" in parsed.netloc and len(path) >= 3 and path[1] == "jobs":
        return [path[0]], path[2]
    company = _norm(row.get("company")).replace(" ", "")
    tokens = [company, company + "inc", company + "hq", company + "jobs",
              parsed.netloc.replace("www.", "").split(".")[0]]
    if "careerpuck" in parsed.netloc and len(path) >= 2:
        tokens.insert(0, path[1])
    return list(dict.fromkeys(t for t in tokens if t)), job_id


def _greenhouse_text(session, row):
    tokens, job_id = _board_candidates(row)
    if not job_id:
        return None
    for token in tokens:
        response = _get(session, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
        if response is not None and response.status_code == 200:
            return html_to_text(html.unescape(response.json().get("content") or ""))
    return None


def _smartrecruiters_text(session, row):
    """jobs.smartrecruiters.com/{Company}/{id}-{slug} -> the posting's jobAd sections."""
    parsed = urlparse(row.get("sourceLink") or "")
    path = [p for p in parsed.path.split("/") if p]
    if "smartrecruiters.com" not in parsed.netloc or len(path) < 2:
        return None
    job_id = re.match(r"\d+", path[1])
    if not job_id:
        return None
    response = _get(session, f"https://api.smartrecruiters.com/v1/companies/{path[0]}"
                             f"/postings/{job_id.group()}")
    if response is None or response.status_code != 200:
        return None
    sections = (response.json().get("jobAd") or {}).get("sections") or {}
    parts = [html_to_text(html.unescape((sections.get(name) or {}).get("text") or ""))
             for name in SR_SECTIONS]
    return "\n\n".join(p for p in parts if p.strip()) or None


_ashby_boards = {}
_ashby_lock = threading.Lock()


def _ashby_text(session, row):
    """jobs.ashbyhq.com/{org}/{uuid} -> that job in the org's board (one fetch per org)."""
    parsed = urlparse(row.get("sourceLink") or "")
    path = [p for p in parsed.path.split("/") if p]
    if "ashbyhq.com" not in parsed.netloc or len(path) < 2:
        return None
    org, job_id = path[0], path[1].lower()
    with _ashby_lock:
        if org not in _ashby_boards:
            response = _get(session, f"https://api.ashbyhq.com/posting-api/job-board/{org}", timeout=30)
            try:
                jobs = response.json().get("jobs", []) if response is not None and response.status_code == 200 else []
            except ValueError:
                jobs = []
            _ashby_boards[org] = {str(j.get("id", "")).lower(): j for j in jobs}
    job = _ashby_boards[org].get(job_id)
    if not job:
        return None
    return job.get("descriptionPlain") or html_to_text(html.unescape(job.get("descriptionHtml") or "")) or None


FETCHERS = {"greenhouse": _greenhouse_text, "smartrecruiters": _smartrecruiters_text,
            "ashby": _ashby_text}


def refetch_ats(rows, workers=6):
    """Fixes incomplete Greenhouse / SmartRecruiters / Ashby descriptions in place."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    stats = {name: {"checked": 0, "replaced": 0, "no_api": 0} for name in FETCHERS}
    targets = []
    for row in rows:
        platform = (row.get("platform") or "").strip().lower()
        if platform in FETCHERS:
            stats[platform]["checked"] += 1
            targets.append((platform, row))

    def work(target):
        platform, row = target
        api = FETCHERS[platform](session, row)
        if api is None:
            return platform, "no_api"
        ours = _norm(row.get("description"))
        lines = [l for l in (_norm(x) for x in api.split("\n")) if len(l) >= MIN_LINE_CHARS]
        missing = sum(1 for l in lines if l not in ours)
        if missing > MAX_MISSING_LINES:
            row["description"] = trim(api)
            return platform, "replaced"
        return platform, "ok"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for platform, outcome in pool.map(work, targets):
            if outcome in stats[platform]:
                stats[platform][outcome] += 1
    return stats


def refetch_greenhouse(rows, workers=6):
    """Greenhouse only; kept for callers that predate refetch_ats()."""
    return refetch_ats([r for r in rows if (r.get("platform") or "").strip().lower() == "greenhouse"],
                       workers)["greenhouse"]


if __name__ == "__main__":
    sys.exit("import refetch_ats() from daily_pipeline.py")
