"""Replaces truncated descriptions with the full posting text from the source's own
public API/page. Read-only against job sites, no API keys, never touches the panels.

  USAJOBS    -> the scraper only gets the search API's summary fields, so the Duties,
                Requirements and Conditions sections are often missing. Handled by
                fetch_full_descriptions.py (announcement page), run per batch.
  Greenhouse -> some boards return only the company blurb. Each row is compared with
                boards-api.greenhouse.io; when more than 2 substantive API lines are
                missing from our text, the API text replaces it. Complete rows are left
                alone, because the scrape legitimately adds pay ranges and benefits.

    refetch_greenhouse(rows) -> {"checked": n, "replaced": n, "no_api": n}
"""
import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import requests

from fetch_full_descriptions import html_to_text, trim

MAX_MISSING_LINES = 2     # more than this many absent API lines => the description is incomplete
MIN_LINE_CHARS = 25


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


def _api_text(session, row):
    tokens, job_id = _board_candidates(row)
    if not job_id:
        return None
    for token in tokens:
        try:
            response = session.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
                                   timeout=20)
        except requests.RequestException:
            continue
        if response.status_code == 200:
            return html_to_text(html.unescape(response.json().get("content") or ""))
    return None


def refetch_greenhouse(rows, workers=6):
    """Fixes incomplete Greenhouse descriptions in place."""
    targets = [r for r in rows if (r.get("platform") or "").strip().lower() == "greenhouse"]
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    def work(row):
        api = _api_text(session, row)
        if api is None:
            return "no_api"
        ours = _norm(row.get("description"))
        lines = [l for l in (_norm(x) for x in api.split("\n")) if len(l) >= MIN_LINE_CHARS]
        missing = sum(1 for l in lines if l not in ours)
        if missing > MAX_MISSING_LINES:
            row["description"] = trim(api)
            return "replaced"
        return "ok"

    stats = {"checked": len(targets), "replaced": 0, "no_api": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(work, targets):
            if outcome in stats:
                stats[outcome] += 1
    return stats


if __name__ == "__main__":
    sys.exit("import refetch_greenhouse() from daily_pipeline.py")
