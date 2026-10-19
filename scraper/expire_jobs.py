"""Deactivates jobs in the admin panel whose original posting is gone.

A job listing outlives the posting it was scraped from: the employer fills the
role and takes the page down, but the panel keeps showing it and applicants keep
applying into nothing. This re-checks each job's sourceLink and flips `active`
to false on the ones that no longer exist.

Three phases, for the same reason backfill_taxonomy.py has them — an admin access
token lasts about 15 minutes and a full sweep is far longer, so every phase
writes its results and a rerun resumes instead of starting over:

    python3 expire_jobs.py --fetch     # pull the panel's active jobs
    python3 expire_jobs.py --check     # visit each sourceLink, classify, touch nothing
    python3 expire_jobs.py --report    # what --apply would deactivate, and why
    python3 expire_jobs.py --apply     # PATCH active=false on the expired ones

The rule that matters: a job is only deactivated on positive evidence that the
posting is gone — a 404, or the board's own "no longer accepting applications"
text. A timeout, a bot-block (403/429), a login wall or any unrecognised page is
UNKNOWN and is left active, because wrongly hiding a live job costs a real
applicant a real opportunity, while leaving a dead one up for another week costs
one more sweep. UNKNOWN results are not cached, so the next run retries them.

Two platforms need more than a plain GET:

  * Indeed (and Glassdoor/ZipRecruiter) answer every scripted request with a
    Cloudflare 403, so --browser renders those in headless Chromium and reads the
    real page, where an expired posting says so outright. A datacentre IP gets
    challenged even in a real browser, so --proxy (or EXPIRE_PROXY_URL in .env)
    sends both the plain GETs and Chromium through a residential proxy:

        --proxy http://user:pass@gate.provider.com:7000

    Rotating-gateway proxies hand out a fresh IP per connection, which is the
    point; the per-host delay still applies so one sweep does not look like a
    burst from a hundred addresses at once.
  * anything still UNKNOWN after that can fall back to age: --max-age-days N
    retires a posting the panel has been carrying for longer than N days. This is
    a policy, not evidence, so it is off unless asked for, and it is recorded with
    its own evidence string so a mistaken sweep can be found and undone later.
"""
import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

import admin_auth

API_BASE = admin_auth.API_ROOT + "/jobs"
PRIMARY_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expire_jobs_state")
STATE_DIR = PRIMARY_STATE_DIR
JOBS_PATH = os.path.join(STATE_DIR, "jobs.json")
CHECKS_PATH = os.path.join(STATE_DIR, "checks.jsonl")
DONE_PATH = os.path.join(STATE_DIR, "deactivated.json")
CURSOR_PATH = os.path.join(STATE_DIR, "cursor.json")


def use_panel(name):
    """Switches the API and the state directory to the given panel. The mirror
    holds different job ids, so its fetch/check/apply state lives apart."""
    global API_BASE, STATE_DIR, JOBS_PATH, CHECKS_PATH, DONE_PATH, CURSOR_PATH
    admin_auth.use_panel(name)
    API_BASE = admin_auth.API_ROOT + "/jobs"
    if name == "mirror":
        STATE_DIR = PRIMARY_STATE_DIR + "_mirror"
        JOBS_PATH = os.path.join(STATE_DIR, "jobs.json")
        CHECKS_PATH = os.path.join(STATE_DIR, "checks.jsonl")
        DONE_PATH = os.path.join(STATE_DIR, "deactivated.json")
        CURSOR_PATH = os.path.join(STATE_DIR, "cursor.json")


def _fresh(row, max_days):
    """An ALIVE verdict only holds for max_days (default 0: not at all), because
    postings close every day and every active job must be re-checked each run.
    EXPIRED is final."""
    if not row:
        return False
    if row.get("verdict") == EXPIRED:
        return True
    if row.get("verdict") != ALIVE:
        return False
    days = age_days(row.get("checkedAt"))
    return days is not None and days < max_days


def other_panel_verdicts_by_link(max_days):
    """Recent conclusions the OTHER panel's sweep reached, keyed by sourceLink.

    The two panels carry mostly the same postings, so whichever is swept second
    reuses the first one's fresh verdicts instead of visiting every link again;
    a verdict is about the posting, not the panel, so it carries over as-is."""
    other = PRIMARY_STATE_DIR + "_mirror" if STATE_DIR == PRIMARY_STATE_DIR else PRIMARY_STATE_DIR
    by_link = {}
    for row in load_jsonl(os.path.join(other, "checks.jsonl")).values():
        link = (row.get("sourceLink") or "").strip().lower()
        if link and _fresh(row, max_days):
            by_link[link] = row
    return by_link

EXPIRED, ALIVE, UNKNOWN = "expired", "alive", "unknown"

# One request per host at a time is too slow for 25k jobs and all-out is a
# bot-block, so each host gets its own minimum gap between requests.
HOST_DELAY = 1.5
REQUEST_TIMEOUT = 25
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/125.0 Safari/537.36")

# What a job board says when the posting is gone. Matched against the page text,
# so each phrase has to be one no live posting would contain.
GONE_PHRASES = (
    "no longer accepting applications",
    "no longer accepting application",
    "this job is no longer available",
    "this job posting is no longer available",
    "job is no longer available",
    "this position has been filled",
    "position is no longer available",
    "posting has expired",
    "this job has expired",
    "job posting has expired",
    "this job ad has expired",
    "the job you are looking for is no longer",
    "job not found",
    "position no longer available",
    "this opening is closed",
    "applications are closed",
    "no longer open",
    "job has been closed",
    "sorry, this job is no longer",
)
GONE_RE = re.compile("|".join(re.escape(p) for p in GONE_PHRASES), re.I)

# A board that redirects a dead posting to its own search or home page instead of
# returning a 404. Keyed by host substring; the value matches the landing path.
REDIRECT_TO_LISTING = (
    ("indeed.com", re.compile(r"^/(jobs|q-|$)")),
    ("linkedin.com", re.compile(r"^/jobs/?$|^/jobs/search|^/jobs/collections")),
    ("glassdoor.com", re.compile(r"^/Job/?$|^/Job/jobs\.htm")),
    ("ziprecruiter.com", re.compile(r"^/jobs-search|^/candidate/search|^/$")),
    ("lever.co", re.compile(r"^/[^/]+/?$")),
    ("greenhouse.io", re.compile(r"^/[^/]+/?$")),
)
# A login wall is not evidence either way.
AUTHWALL_RE = re.compile(r"/authwall|/login|/signin|/checkpoint|captcha|/uas/login", re.I)

# USAJOBS renders its dates server-side but the "closed" banner client-side, so a
# closed federal announcement still answers 200 with a full page and no gone-text.
# Both of these markers are in the served HTML, and a close date in the past is
# the announcement itself saying it is over.
USAJOBS_VALID_THROUGH_RE = re.compile(r'"validThrough"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
USAJOBS_DATE_DISPLAY_RE = re.compile(
    r'"DateDisplay"\s*:\s*"Open\s+\d{2}/\d{2}/\d{4}\s+to\s+(\d{2})/(\d{2})/(\d{4})"')


def usajobs_verdict(body):
    """(verdict, evidence) for a usajobs.gov announcement, or None if the page
    carries neither marker — an unreadable page is not evidence of anything."""
    today = dt.date.today()
    match = USAJOBS_VALID_THROUGH_RE.search(body)
    if match:
        try:
            closes = dt.date.fromisoformat(match.group(1))
        except ValueError:
            closes = None
        if closes:
            if closes < today:
                return EXPIRED, f"announcement closed {closes.isoformat()}"
            return ALIVE, f"announcement open through {closes.isoformat()}"
    match = USAJOBS_DATE_DISPLAY_RE.search(body)
    if match:
        month, day, year = (int(g) for g in match.groups())
        try:
            closes = dt.date(year, month, day)
        except ValueError:
            return None
        if closes < today:
            return EXPIRED, f"announcement closed {closes.isoformat()}"
        return ALIVE, f"announcement open through {closes.isoformat()}"
    return None


# Hosts that answer scripted requests with a Cloudflare challenge no matter the
# headers. A GET here proves nothing, so --browser routes them to Chromium.
BROWSER_HOSTS = ("indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com")
# What Indeed itself puts on a dead posting once the page actually renders.
BROWSER_GONE_EXTRA = re.compile(
    r"this job has expired on indeed|job has expired|no longer available|"
    r"posting is expired|this job is expired", re.I)
BROWSER_TIMEOUT_MS = 30_000

# Set from --proxy / EXPIRE_PROXY_URL. Kept module-level because both the
# requests session and the Chromium launch need it.
PROXY_URL = ""
# Every proxy available this run, from --proxy-file / EXPIRE_PROXY_LIST. One is
# drawn per run (or per request with --rotate request), and a proxy that comes
# back blocked is dropped so the rest of the run does not keep using it.
PROXY_POOL = []
ROTATE = "run"
_proxy_lock = threading.Lock()
# Set from --cdp: attach to an already-running Chrome instead of launching one.
CDP_URL = ""

_browser_lock = threading.Lock()
_browser_state = {}

_host_locks = {}
_host_next = {}
_lock = threading.Lock()


def _throttle(host):
    """Spaces requests per host without serialising the whole run."""
    with _lock:
        host_lock = _host_locks.setdefault(host, threading.Lock())
    with host_lock:
        now = time.time()
        wait = _host_next.get(host, 0) - now
        if wait > 0:
            time.sleep(wait)
        _host_next[host] = time.time() + HOST_DELAY + random.uniform(0, 0.4)


def _host(url):
    match = re.match(r"https?://([^/]+)", url or "")
    return (match.group(1) if match else "").lower()


def classify(session, url):
    """Returns (verdict, evidence). Only a 404/410 or the board's own gone-text
    counts as expired; everything unclear stays UNKNOWN and is retried next run."""
    _throttle(_host(url))
    proxy = current_proxy()
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                               proxies=_proxy_for_requests(proxy),
                               headers={"User-Agent": USER_AGENT,
                                        "Accept-Language": "en-US,en;q=0.9"})
    except requests.RequestException as exc:
        # A proxy that cannot connect is the proxy's fault, not the job's.
        if proxy:
            drop_proxy(proxy)
        return UNKNOWN, f"request-error: {type(exc).__name__}"

    status = response.status_code
    if status in (404, 410):
        return EXPIRED, f"http {status}"
    # 403/429/5xx are the site refusing us, not the job being gone.
    if status in (401, 403, 429) or status >= 500:
        if proxy and status in (401, 403, 429):
            drop_proxy(proxy)
        return UNKNOWN, f"http {status} (blocked or unavailable)"
    if status != 200:
        return UNKNOWN, f"http {status}"

    final = response.url or ""
    if AUTHWALL_RE.search(final):
        return UNKNOWN, "login wall"

    final_host, path = _host(final), re.sub(r"^https?://[^/]+", "", final) or "/"
    for host_fragment, listing_re in REDIRECT_TO_LISTING:
        if host_fragment in final_host and listing_re.search(path):
            return EXPIRED, f"redirected to listing page: {final[:120]}"

    # Only the first stretch of the page is searched: boilerplate further down
    # ("jobs no longer available near you") would otherwise read as gone-text.
    body = response.text[:200_000]

    if "usajobs.gov" in final_host:
        verdict = usajobs_verdict(body)
        if verdict:
            return verdict

    match = GONE_RE.search(body)
    if match:
        return EXPIRED, f"page says: {match.group(0)!r}"
    return ALIVE, f"http 200 ({len(body)} bytes)"


def load_proxy_pool(path_or_list):
    """Reads proxies from a file (one per line, # comments allowed) or a
    comma-separated string. Bare host:port is assumed to be http://."""
    if not path_or_list:
        return []
    if os.path.exists(path_or_list):
        lines = open(path_or_list, encoding="utf-8").read().splitlines()
    else:
        lines = path_or_list.split(",")
    pool = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pool.append(line if "://" in line else f"http://{line}")
    return pool


def current_proxy():
    """The proxy this request should use. With --rotate run the whole sweep goes
    out through one address; with --rotate request each check draws its own."""
    if ROTATE == "request" and PROXY_POOL:
        with _proxy_lock:
            return random.choice(PROXY_POOL) if PROXY_POOL else ""
    return PROXY_URL


def drop_proxy(proxy):
    """Retires a proxy that answered with a block or died, and promotes another
    so one bad exit node does not poison the rest of the run."""
    global PROXY_URL
    if not proxy:
        return
    with _proxy_lock:
        if proxy in PROXY_POOL:
            PROXY_POOL.remove(proxy)
        if PROXY_URL == proxy:
            PROXY_URL = random.choice(PROXY_POOL) if PROXY_POOL else ""
            shown = re.sub(r"//[^@/]+@", "//***@", PROXY_URL) or "(none left)"
            print(f"  [proxy] dropped a blocked exit; now using {shown}", flush=True)


def _proxy_for_requests(proxy=None):
    proxy = PROXY_URL if proxy is None else proxy
    return {"http": proxy, "https": proxy} if proxy else None


def _proxy_for_playwright():
    """Chromium wants the credentials split out of the URL, not inline."""
    if not PROXY_URL:
        return None
    match = re.match(r"^(?P<scheme>https?)://(?:(?P<user>[^:@/]+):(?P<password>[^@/]*)@)?"
                     r"(?P<host>[^/]+)/?$", PROXY_URL)
    if not match:
        raise SystemExit(f"could not parse proxy URL: {PROXY_URL!r}")
    proxy = {"server": f"{match.group('scheme')}://{match.group('host')}"}
    if match.group("user"):
        proxy["username"] = match.group("user")
        proxy["password"] = match.group("password") or ""
    return proxy


def _browser_page():
    """One Chromium for the whole run, started on first use. Images and fonts are
    dropped — this reads text, and a job page pulls megabytes of them.

    With --cdp it attaches to a Chrome you already have open instead of launching
    its own. That matters for Indeed: a fresh headless profile on a datacentre IP
    gets a Cloudflare challenge, while your everyday browser — real IP, real
    profile, cf_clearance cookie already earned — just loads the page. Start
    Chrome once with:

        google-chrome --remote-debugging-port=9222 \
                      --user-data-dir="$HOME/.config/google-chrome"

    clear any challenge yourself in that window, then run with --cdp
    http://localhost:9222 and the sweep reads pages through that same session."""
    if "page" in _browser_state:
        return _browser_state["page"]
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    if CDP_URL:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.route(re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|woff2?|ttf|mp4)$"),
                      lambda route: route.abort())
        page = context.new_page()
        _browser_state.update(playwright=playwright, browser=browser,
                              context=context, page=page, attached=True)
        return page
    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"],
                                         proxy=_proxy_for_playwright())
    context = browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                  viewport={"width": 1280, "height": 900})
    context.route(re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|woff2?|ttf|mp4)$"),
                  lambda route: route.abort())
    page = context.new_page()
    _browser_state.update(playwright=playwright, browser=browser, context=context, page=page)
    return page


def close_browser():
    # An attached browser belongs to the user; close our tab, not their window.
    if _browser_state.pop("attached", False):
        page = _browser_state.pop("page", None)
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        playwright = _browser_state.pop("playwright", None)
        _browser_state.pop("browser", None)
        _browser_state.pop("context", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        return
    for key in ("browser", "playwright"):
        target = _browser_state.pop(key, None)
        if target is not None:
            try:
                target.close() if key == "browser" else target.stop()
            except Exception:
                pass
    _browser_state.pop("page", None)
    _browser_state.pop("context", None)


def classify_in_browser(url):
    """Renders a page the plain GET could not read. Serialised behind a lock:
    one Chromium tab is the point, and hammering Indeed from ten of them is how
    the 403 started."""
    _throttle(_host(url))
    if True:
        try:
            page = _browser_page()
            response = page.goto(url, timeout=BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as exc:
            return UNKNOWN, f"browser-error: {type(exc).__name__}"

        status = response.status if response is not None else 0
        if status in (404, 410):
            return EXPIRED, f"browser: http {status}"
        try:
            page.wait_for_timeout(1200)
            body = page.inner_text("body")[:200_000]
            final = page.url
        except Exception as exc:
            return UNKNOWN, f"browser-read-error: {type(exc).__name__}"

    if status in (401, 403, 429) or status >= 500:
        return UNKNOWN, f"browser: http {status} (still blocked)"
    if AUTHWALL_RE.search(final):
        return UNKNOWN, "browser: login wall"

    final_host, path = _host(final), re.sub(r"^https?://[^/]+", "", final) or "/"
    for host_fragment, listing_re in REDIRECT_TO_LISTING:
        if host_fragment in final_host and listing_re.search(path):
            return EXPIRED, f"browser: redirected to listing page: {final[:100]}"

    match = GONE_RE.search(body) or BROWSER_GONE_EXTRA.search(body)
    if match:
        return EXPIRED, f"browser: page says {match.group(0)!r}"
    # A rendered page with no posting text at all is a challenge screen, not a job.
    if len(body.strip()) < 400:
        return UNKNOWN, "browser: page rendered nearly empty (challenge?)"
    return ALIVE, f"browser: http {status} ({len(body)} chars)"


def age_days(created_at):
    """Days since the panel first stored this job. createdAt is when we recorded
    it, not when the employer posted it, so it is a floor on the real age."""
    if not created_at:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            stamp = time.mktime(time.strptime(created_at, fmt))
            return (time.time() - stamp) / 86400
        except ValueError:
            continue
    return None


def fetch_jobs(page_size=200, active_only=True, on_page=None, start_cursor=None):
    """Pages through the panel. A 500 in the middle of 80 pages used to throw the
    whole run away, so a failed page is retried and every page is handed to
    on_page as it lands — the caller checkpoints, and a rerun resumes from the
    last cursor instead of refetching from the top."""
    jobs, cursor = [], start_cursor
    headers = admin_auth.auth_headers()
    while True:
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        for attempt in range(1, 6):
            try:
                response = requests.get(API_BASE, headers=headers, params=params, timeout=60)
            except requests.RequestException as exc:
                print(f"  [retry {attempt}/5] {type(exc).__name__}", flush=True)
                time.sleep(3 * attempt)
                continue
            if response.status_code == 401:
                headers = admin_auth.auth_headers(force=True)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                print(f"  [retry {attempt}/5] http {response.status_code} at cursor={cursor}",
                      flush=True)
                time.sleep(3 * attempt)
                continue
            break
        else:
            print(f"  giving up at cursor={cursor}; keeping the {len(jobs)} pulled so far",
                  file=sys.stderr)
            break
        if not response.ok:
            print(f"  stopping: http {response.status_code} at cursor={cursor}",
                  file=sys.stderr)
            break

        body = response.json()
        batch = body if isinstance(body, list) else (
            body.get("items") or body.get("data") or body.get("jobs") or [])
        if not batch:
            break
        jobs.extend(batch)
        cursor = body.get("nextCursor") if isinstance(body, dict) else None
        if on_page:
            on_page(jobs, cursor)
        print(f"  ...{len(jobs)} fetched", flush=True)
        if not cursor:
            break
    if active_only:
        jobs = [j for j in jobs if str(j.get("active", "")).lower() in ("true", "1")]
    return jobs


def load_jsonl(path):
    rows = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                rows[row["id"]] = row
    except FileNotFoundError:
        pass
    return rows


def append_jsonl(path, row, write_lock):
    with write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def phase_fetch(args):
    os.makedirs(STATE_DIR, exist_ok=True)

    def slim(jobs):
        # location is carried because the ingest diff scopes by it: without it the
        # diff cannot tell a Colorado export from an Arizona one.
        return [{"id": j["id"], "company": j.get("company"), "role": j.get("role"),
                 "sourceLink": j.get("sourceLink"), "platform": j.get("platform"),
                 "location": j.get("location"), "active": j.get("active"),
                 "createdAt": j.get("createdAt")}
                for j in jobs]

    # Checkpoint after every page: 15k jobs is 80 requests and losing them all to
    # one 500 on page 71 is the failure this exists to prevent.
    def checkpoint(jobs, cursor):
        with open(JOBS_PATH, "w", encoding="utf-8") as f:
            json.dump(slim(jobs), f)
        with open(CURSOR_PATH, "w", encoding="utf-8") as f:
            json.dump({"cursor": cursor}, f)

    jobs = fetch_jobs(active_only=not args.include_inactive, on_page=checkpoint)
    with open(JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(slim(jobs), f)
    print(f"fetched {len(jobs)} job(s) -> {JOBS_PATH}")


def phase_check(args):
    with open(JOBS_PATH, encoding="utf-8") as f:
        jobs = json.load(f)
    checked = load_jsonl(CHECKS_PATH)
    # UNKNOWN is not a conclusion, so those are re-checked on the next run.
    pending = [j for j in jobs
               if (j.get("sourceLink") or "").startswith("http")
               and not _fresh(checked.get(j["id"]), args.recheck_alive_days)]
    if args.created_before:
        pending = [j for j in pending
                   if (j.get("createdAt") or "") < args.created_before]
    if args.created_after:
        pending = [j for j in pending
                   if (j.get("createdAt") or "") > args.created_after]
    if args.host:
        pending = [j for j in pending if args.host.lower() in _host(j["sourceLink"])]

    write_lock = threading.Lock()
    reused = other_panel_verdicts_by_link(args.reuse_primary_hours / 24)
    if reused:
        still_pending = []
        carried = Counter()
        for job in pending:
            prior = reused.get(job["sourceLink"].strip().lower())
            if not prior:
                still_pending.append(job)
                continue
            append_jsonl(CHECKS_PATH, {
                "id": job["id"], "verdict": prior["verdict"],
                "evidence": f"same link on other panel: {prior['evidence']}",
                "company": job.get("company"), "role": job.get("role"),
                "sourceLink": job["sourceLink"],
                "checkedAt": prior.get("checkedAt")}, write_lock)
            carried[prior["verdict"]] += 1
        print(f"reused other-panel verdicts: expired={carried[EXPIRED]} "
              f"alive={carried[ALIVE]}")
        pending = still_pending

    if args.limit:
        pending = pending[:args.limit]
    print(f"jobs={len(jobs)} already-checked={len(checked)} to-check={len(pending)} "
          f"workers={args.workers}")

    counts = Counter()
    session = requests.Session()

    def record(job, verdict, evidence):
        if verdict == UNKNOWN and args.max_age_days:
            days = age_days(job.get("createdAt"))
            if days is not None and days >= args.max_age_days:
                verdict = EXPIRED
                evidence = (f"aged out: in the panel {int(days)} days and the link "
                            f"could not be verified ({evidence})")
        row = {"id": job["id"], "verdict": verdict, "evidence": evidence,
               "company": job.get("company"), "role": job.get("role"),
               "sourceLink": job["sourceLink"],
               "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
        # Only conclusions are cached; an UNKNOWN would otherwise freeze a live
        # job's status on one bad afternoon for the site.
        if verdict in (EXPIRED, ALIVE):
            append_jsonl(CHECKS_PATH, row, write_lock)
        counts[verdict] += 1
        done = sum(counts.values())
        if done % 25 == 0:
            print(f"  {done}/{len(pending)} expired={counts[EXPIRED]} "
                  f"alive={counts[ALIVE]} unknown={counts[UNKNOWN]}", flush=True)
        return row

    needs_browser = []

    def worker(job):
        url = job["sourceLink"]
        verdict, evidence = classify(session, url)
        # A blocked host proves nothing over plain HTTP. Playwright's sync API is
        # thread-affine, so those are collected here and rendered afterwards on
        # this thread rather than driven from inside the pool.
        if (verdict == UNKNOWN and args.browser
                and any(h in _host(url) for h in BROWSER_HOSTS)):
            needs_browser.append(job)
            return None
        record(job, verdict, evidence)
        return None

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(worker, pending))

        if needs_browser:
            print(f"\nrendering {len(needs_browser)} blocked page(s) in Chromium "
                  f"(one at a time)", flush=True)
            for job in needs_browser:
                verdict, evidence = classify_in_browser(job["sourceLink"])
                record(job, verdict, evidence)
    finally:
        close_browser()

    print(f"\nchecked={sum(counts.values())} expired={counts[EXPIRED]} "
          f"alive={counts[ALIVE]} unknown={counts[UNKNOWN]} (unknown will be retried)")


def expired_entries():
    checks = load_jsonl(CHECKS_PATH)
    done = set(json.load(open(DONE_PATH)) if os.path.exists(DONE_PATH) else [])
    return [row for row in checks.values()
            if row["verdict"] == EXPIRED and row["id"] not in done], done


def _location_key(location):
    """Normalises a location to the granularity the diff can defend: city+state
    where both are present, otherwise whatever is there. "Boulder, CO, US" and
    "boulder, co" both come back as "boulder|co"."""
    parts = [p.strip().lower() for p in (location or "").split(",") if p.strip()]
    parts = [p for p in parts if p not in ("us", "usa", "united states")]
    return "|".join(parts[:2])


def phase_expire_missing(args):
    """Expires panel jobs that a fresh scrape of the same ground no longer lists.

    Indeed answers every request from this machine with a bot-detection stub, so
    its postings cannot be checked one by one. But the scrape that produced them
    can be re-run: a job that was in last week's export of a location and is
    absent from this week's has dropped out of the board, which is what expiry
    looks like from outside.

    The scoping is the whole safety story, and one weak version of it already
    tried to retire 4,143 live jobs here: a Colorado export was allowed to judge
    every Indeed job on the panel, including Arizona and Delaware. So the diff
    now only ever judges a job whose own location appears in the fresh export.
    A location the export does not cover is not evidence about that location.

      * --scope keeps the comparison to one host (indeed.com by default).
      * locations are taken FROM the export, not from a flag, and only panel jobs
        in those locations are eligible.
      * a location must carry at least --min-per-location fresh rows before it is
        trusted at all, so a handful of stray rows cannot condemn a city.
      * the export must still cover at least --min-ratio of the eligible jobs.
      * --dry-run reports the diff and records nothing.
    """
    with open(JOBS_PATH, encoding="utf-8") as f:
        jobs = json.load(f)

    with open(args.expire_missing_from, newline="", encoding="utf-8-sig") as f:
        fresh_rows = list(csv.DictReader(f))
    fresh_links = {(r.get("sourceLink") or "").strip().lower()
                   for r in fresh_rows if (r.get("sourceLink") or "").strip()}

    covered = Counter(_location_key(r.get("location")) for r in fresh_rows
                      if _location_key(r.get("location")))
    trusted = {loc for loc, n in covered.items() if n >= args.min_per_location}

    on_host = [j for j in jobs if args.scope in _host(j.get("sourceLink") or "")]
    eligible = [j for j in on_host if _location_key(j.get("location")) in trusted]

    print(f"fresh export: {len(fresh_rows)} rows, {len(fresh_links)} distinct links")
    print(f"locations covered: {len(covered)} ({len(trusted)} with "
          f">= {args.min_per_location} rows)")
    print(f"panel on {args.scope}: {len(on_host)}  |  in a covered location: {len(eligible)}")

    if len(fresh_links) < args.min_fresh:
        sys.exit(f"refusing: the export has {len(fresh_links)} links, below --min-fresh "
                 f"{args.min_fresh}. A truncated scrape would expire live jobs.")
    if not eligible:
        sys.exit("refusing: no panel job sits in a location this export covers.")
    ratio = len(fresh_links) / max(1, len(eligible))
    if ratio < args.min_ratio:
        sys.exit(f"refusing: the export holds {len(fresh_links)} links against "
                 f"{len(eligible)} eligible panel jobs ({ratio:.0%}), below "
                 f"--min-ratio {args.min_ratio:.0%}. The scrape looks partial.")

    missing = [j for j in eligible
               if (j.get("sourceLink") or "").strip().lower() not in fresh_links]
    print(f"missing from the fresh export: {len(missing)} "
          f"({len(missing) * 100 / max(1, len(eligible)):.1f}% of eligible)")
    by_location = Counter(_location_key(j.get("location")) for j in missing)
    for loc, n in by_location.most_common(10):
        print(f"    {loc or '(blank)':28} {n:5} of {sum(1 for j in eligible if _location_key(j.get('location')) == loc)}")

    if args.dry_run:
        print("\ndry run: nothing recorded")
        return

    stamp = time.strftime("%Y-%m-%d")
    write_lock = threading.Lock()
    for job in missing:
        append_jsonl(CHECKS_PATH, {
            "id": job["id"], "verdict": EXPIRED,
            "evidence": (f"absent from the {stamp} {args.scope} export "
                         f"({os.path.basename(args.expire_missing_from)}, "
                         f"{len(fresh_links)} links, location "
                         f"{_location_key(job.get('location'))})"),
            "company": job.get("company"), "role": job.get("role"),
            "sourceLink": job.get("sourceLink"),
            "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ")}, write_lock)
    print(f"recorded {len(missing)} expiry verdict(s); run --report to review, "
          f"--apply to deactivate")


def phase_report(args):
    pending, done = expired_entries()
    print(f"already deactivated: {len(done)}")
    print(f"to deactivate: {len(pending)}\n")
    for row in pending[:args.limit or 25]:
        print(f"  {row['company']} — {row['role'][:60]}\n"
              f"    {row['evidence']}\n    {row['sourceLink'][:110]}")
    reasons = Counter(re.sub(r"[:(].*", "", r["evidence"]).strip() for r in pending)
    print("\nby evidence:", dict(reasons))


def phase_apply(args):
    pending, done = expired_entries()
    if args.limit:
        pending = pending[:args.limit]
    print(f"deactivating {len(pending)} job(s)")
    headers = admin_auth.auth_headers()
    ok = failed = 0
    for index, row in enumerate(pending, 1):
        for attempt in range(1, 7):
            try:
                response = requests.patch(f"{API_BASE}/{row['id']}", json={"active": False},
                                          headers=headers, timeout=30)
            except requests.RequestException as exc:
                # DNS / network blips: wait them out rather than abandon the run.
                print(f"  [retry {attempt}/6] {type(exc).__name__}", flush=True)
                time.sleep(10 * attempt)
                continue
            if response.status_code == 401:
                headers = admin_auth.auth_headers(force=True)
                continue
            if response.status_code == 429:
                time.sleep(5 * attempt)
                continue
            if response.ok:
                ok += 1
                done.add(row["id"])
                with open(DONE_PATH, "w", encoding="utf-8") as f:
                    json.dump(sorted(done), f)
                print(f"[{index}/{len(pending)}] deactivated {row['company']} — "
                      f"{row['role'][:50]}")
                break
            # A job deleted from the panel is already off the board.
            if response.status_code == 404:
                done.add(row["id"])
                with open(DONE_PATH, "w", encoding="utf-8") as f:
                    json.dump(sorted(done), f)
                break
            failed += 1
            print(f"[{index}] FAILED {row['id']}: {response.status_code} "
                  f"{response.text[:200]}", file=sys.stderr)
            break
        time.sleep(args.delay)
    print(f"\ndeactivated={ok} failed={failed}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--created-before", default="",
                        help="only check jobs the panel stored before this ISO "
                             "timestamp, e.g. 2026-08-27 (compares the raw string)")
    parser.add_argument("--created-after", default="",
                        help="only check jobs stored after this ISO timestamp")
    parser.add_argument("--host", default="",
                        help="only check links on this host, e.g. linkedin.com")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="seconds between PATCHes")
    parser.add_argument("--proxy", default="",
                        help="proxy for the link checks, e.g. "
                             "http://user:pass@gate.provider.com:7000 "
                             "(defaults to EXPIRE_PROXY_URL in the environment/.env). "
                             "Applies to both the plain GETs and Chromium.")
    parser.add_argument("--expire-missing-from", default="",
                        help="a fresh scrape CSV: panel jobs in --scope that it no "
                             "longer lists are recorded as expired (no requests to "
                             "the job board at all)")
    parser.add_argument("--scope", default="indeed.com",
                        help="host the diff applies to (default indeed.com)")
    parser.add_argument("--min-per-location", type=int, default=25,
                        help="a location needs this many rows in the export before "
                             "the diff will judge jobs there")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --expire-missing-from: show the diff, record nothing")
    parser.add_argument("--min-fresh", type=int, default=200,
                        help="refuse the diff if the export has fewer links than this")
    parser.add_argument("--min-ratio", type=float, default=0.5,
                        help="refuse the diff if the export covers less than this "
                             "fraction of the panel jobs in scope")
    parser.add_argument("--proxy-file", default="",
                        help="file of proxies (one per line) or a comma-separated "
                             "list; defaults to EXPIRE_PROXY_LIST. One is drawn per "
                             "run, and any that answers with a block is dropped.")
    parser.add_argument("--rotate", choices=("run", "request"), default="run",
                        help="draw one proxy for the whole run (default) or a fresh "
                             "one for every request")
    parser.add_argument("--cdp", default="",
                        help="attach to a Chrome already running with "
                             "--remote-debugging-port=9222 (e.g. http://localhost:9222) "
                             "instead of launching a fresh headless one, so Indeed sees "
                             "your normal browser session")
    parser.add_argument("--browser", action="store_true",
                        help="render Cloudflare-blocked hosts (Indeed, Glassdoor, "
                             "ZipRecruiter) in headless Chromium instead of giving up")
    parser.add_argument("--max-age-days", type=int, default=0,
                        help="last resort: treat a job the panel has carried this "
                             "long as expired when its link cannot be verified "
                             "(0 = off; this is a policy, not evidence)")
    parser.add_argument("--include-inactive", action="store_true",
                        help="also check jobs already marked inactive")
    parser.add_argument("--recheck-alive-days", type=float, default=0,
                        help="skip jobs found alive within this many days (default 0: "
                             "every active job is checked on every run)")
    parser.add_argument("--reuse-primary-hours", type=float, default=6,
                        help="reuse the other panel's verdict for the same link if it "
                             "was checked within this many hours (its sweep just ran)")
    parser.add_argument("--panel", choices=("primary", "mirror"), default="primary",
                        help="which admin panel to sweep; the mirror keeps its state "
                             "in expire_jobs_state_mirror/")
    args = parser.parse_args()

    global PROXY_URL, CDP_URL, PROXY_POOL, ROTATE
    admin_auth._load_env()
    use_panel(args.panel)
    print(f"panel: {args.panel} ({API_BASE}) state: {STATE_DIR}")
    ROTATE = args.rotate
    PROXY_POOL = load_proxy_pool(args.proxy_file or os.environ.get("EXPIRE_PROXY_LIST", ""))
    PROXY_URL = (args.proxy or os.environ.get("EXPIRE_PROXY_URL", "")).strip()
    if PROXY_POOL and not PROXY_URL:
        PROXY_URL = random.choice(PROXY_POOL)
    if PROXY_POOL:
        print(f"proxy pool: {len(PROXY_POOL)} entries, rotating per {ROTATE}")
    if PROXY_URL:
        print(f"proxy: {re.sub(r'//[^@/]+@', '//***@', PROXY_URL)}")
    CDP_URL = (args.cdp or os.environ.get("EXPIRE_CDP_URL", "")).strip()
    if CDP_URL:
        print(f"attaching to Chrome at {CDP_URL}")
        args.browser = True

    os.makedirs(STATE_DIR, exist_ok=True)
    if not any((args.fetch, args.check, args.report, args.apply,
                args.expire_missing_from)):
        parser.error("pick a phase: --fetch, --check, --expire-missing-from, "
                     "--report or --apply")
    if args.expire_missing_from:
        phase_expire_missing(args)
    if args.fetch:
        phase_fetch(args)
    if args.check:
        phase_check(args)
    if args.report:
        phase_report(args)
    if args.apply:
        phase_apply(args)


if __name__ == "__main__":
    main()
