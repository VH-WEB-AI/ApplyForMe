"""Replaces summary/template descriptions with the full posting text from the
original job page. Read-only against job sites; never touches the panel.

    python3 fetch_full_descriptions.py --csv IN.csv --out OUT.csv [--limit N] [--host usajobs]

A row whose full text can't be fetched keeps its old description and is listed in
<out>_unfetched.csv so it can be held back rather than posted with a stub.
The cache (<out>_cache.json) makes a rerun only retry what failed.

Sources:
  usajobs.gov      -> the announcement page's Summary / Duties / Requirements sections
  greenhouse       -> boards-api.greenhouse.io job content
  everything else  -> the page's JSON-LD JobPosting description, else its main text
"""
import argparse
import csv
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

MAX_DESCRIPTION = 8000
MIN_FULL = 600  # below this the "full" text is no better than the stub
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0 Safari/537.36")
USAJOBS_SECTIONS = (("joa-summary", "Summary"), ("joa-duties", "Duties"),
                    ("joa-requirements", "Requirements"))


def html_to_text(fragment):
    """Block-aware HTML -> plain text: list items become "- " lines and block
    elements become paragraph breaks, so the posting keeps its structure."""
    soup = BeautifulSoup(fragment, "html.parser")
    for tag in soup(["script", "style", "svg", "button", "noscript"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "section"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")
    text = html.unescape(soup.get_text())
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n- \n+", "\n- ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def trim(text):
    """Keeps under the API's 8000-char limit, cutting on a paragraph boundary so
    the intake DTO never truncates mid-word."""
    if len(text) <= MAX_DESCRIPTION:
        return text
    cut = text.rfind("\n\n", 0, MAX_DESCRIPTION)
    if cut < MAX_DESCRIPTION * 0.6:
        cut = text.rfind(". ", 0, MAX_DESCRIPTION) + 1
    return text[:cut].rstrip()


def get(session, url, **kw):
    return session.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US"},
                       timeout=30, **kw)


def from_usajobs(session, url):
    response = get(session, url)
    if response.status_code != 200:
        return None, f"http {response.status_code}"
    soup = BeautifulSoup(response.text, "html.parser")
    parts = []
    for section_id, title in USAJOBS_SECTIONS:
        node = soup.find(id=section_id)
        if not node:
            continue
        for noise in node.select("a[href*='help.usajobs.gov'], .usajobs-joa-summary__agency-link"):
            noise.decompose()
        body = html_to_text(str(node))
        body = re.sub(rf"^{title}\s*", "", body).strip()
        body = re.sub(r"\n?Learn more about this agency", "", body)
        # Summary is rendered twice (mobile + desktop)
        half = len(body) // 2
        if half and body[:half].strip() == body[half:].strip():
            body = body[:half].strip()
        if body:
            parts.append(f"{title}\n{body}")
    if not parts:
        return None, "no sections"
    return "\n\n".join(parts), "usajobs page"


def from_greenhouse(session, url):
    parsed = urlparse(url)
    job_id = (parse_qs(parsed.query).get("gh_jid") or [None])[0]
    path = [p for p in parsed.path.split("/") if p]
    board = None
    if "greenhouse.io" in parsed.netloc and len(path) >= 3 and path[1] == "jobs":
        board, job_id = path[0], path[2]
    if not board:
        board = parsed.netloc.replace("www.", "").replace("jobs.", "").split(".")[0]
    if not job_id:
        return None, "no greenhouse id"
    for token in dict.fromkeys([board, board + "inc", board + "jobs"]):
        response = get(session, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
        if response.status_code == 200:
            content = html.unescape(response.json().get("content") or "")
            return html_to_text(content), f"greenhouse api ({token})"
    return None, "greenhouse api 404"


def from_page(session, url):
    response = get(session, url, allow_redirects=True)
    if response.status_code != 200:
        return None, f"http {response.status_code}"
    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data])):
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return html_to_text(html.unescape(item["description"])), "json-ld"
    # Board-specific containers, most specific first.
    for selector in ("#job-description", ".job-description", "section.content",
                     "[class*='jobad'] section", "[class*='job-description']",
                     "[class*='description']", "article", "main"):
        node = soup.select_one(selector)
        if node:
            text = html_to_text(str(node))
            if len(text) >= MIN_FULL:
                return text, f"page {selector}"
    return None, "no description container"


def fetch(session, url):
    host = urlparse(url).netloc
    try:
        if "usajobs.gov" in host:
            return from_usajobs(session, url)
        if "greenhouse" in host or "gh_jid=" in url:
            return from_greenhouse(session, url)
        return from_page(session, url)
    except requests.RequestException as exc:
        return None, type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--host", default="")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if os.path.abspath(args.csv) == os.path.abspath(args.out):
        sys.exit("--out must differ from --csv")

    base = os.path.splitext(args.out)[0]
    cache_path = base + "_cache.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)

    todo = [r for r in rows if r["sourceLink"] not in cache
            and (not args.host or args.host in r["sourceLink"])]
    if args.limit:
        todo = todo[:args.limit]
    print(f"rows={len(rows)} cached={len(cache)} to_fetch={len(todo)}", flush=True)

    sessions = {}

    def work(row):
        import threading
        session = sessions.setdefault(threading.get_ident(), requests.Session())
        text, source = fetch(session, row["sourceLink"])
        time.sleep(0.5)
        return row["sourceLink"], text, source

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for link, text, source in pool.map(work, todo):
            done += 1
            if text and len(text) >= MIN_FULL:
                cache[link] = {"text": text, "source": source, "raw_len": len(text)}
            else:
                print(f"  [miss] {source} len={len(text or '')} {link[:90]}", flush=True)
            if done % 25 == 0:
                json.dump(cache, open(cache_path, "w", encoding="utf-8"))
                print(f"  {done}/{len(todo)} fetched_ok={len(cache)}", flush=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"))

    unfetched = []
    for row in rows:
        hit = cache.get(row["sourceLink"])
        if hit:
            row["description"] = hit["text"]
        else:
            unfetched.append(row)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with open(base + "_unfetched.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unfetched)
    trimmed = sum(1 for v in cache.values() if v["raw_len"] > MAX_DESCRIPTION)
    print(f"\nfull={len(rows) - len(unfetched)} unfetched={len(unfetched)} "
          f"trimmed_to_8000={trimmed} -> {args.out}")


if __name__ == "__main__":
    main()
