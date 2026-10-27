#!/usr/bin/env python3
"""One command for the whole daily jobs run: scrape -> clean -> taxonomy/tags ->
gate -> post (both panels) -> verify.

    .venv/bin/python daily_pipeline.py                 # catch up from last successful run to today, then post
    .venv/bin/python daily_pipeline.py --no-post       # everything except the live post
    .venv/bin/python daily_pipeline.py --skip-scrape   # reuse today's raw CSV
    .venv/bin/python daily_pipeline.py --with-expire   # also run the expiry sweep (hours)

Every stage writes into daily_runs/YYYY-MM-DD/ and is skipped on a rerun if its
output already exists (--force redoes everything), so a crash halfway resumes
instead of rescraping. job_scraper.py records what it scraped in scraped_seen.json
as it goes; the raw CSV kept here is what makes `--skip-scrape` safe after a failure.

The gate enforces the must-rules before anything is posted:
  1. industry/category/subCategory filled, the full triple in job_taxonomy_master,
     and not an "Other / Not Specified" fallback
  2. 7+ tags
  3. a real description (not a stub); over 8000 chars is trimmed at a paragraph break
  4. US-only (explicit non-US rows are dropped, ambiguous ones are held)
Descriptions are completed before the gate: USAJOBS rows from the announcement page, incomplete
Greenhouse / SmartRecruiters / Ashby rows from their public APIs (refetch_descriptions.py). A USAJOBS row whose page can't be
fetched is held, not posted with the API's truncated text.
plus: no confirmed-dead link (404/410). Rows that pass are posted; rows that fail
go to held.csv with the reason, to be fixed by hand and posted later.
Uses no paid API keys: only the scraper's public sources and the admin intake.
"""
import argparse
import csv
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRAPER_DIR = os.path.join(HERE, "Job_scraper")
RUNS_DIR = os.path.join(HERE, "daily_runs")
PY = sys.executable

MIN_TAGS = 7
MIN_DESCRIPTION = 600       # below this a description is a stub, not a posting
MAX_DESCRIPTION = 8000      # both panels' DTO reject anything longer
SKIP_LINK_HOSTS = ("linkedin.com", "indeed.com")   # answer bots with 401/403, so can't be judged

csv.field_size_limit(sys.maxsize)

from refetch_descriptions import refetch_ats  # noqa: E402


LAST_SUCCESS = os.path.join(RUNS_DIR, "last_success.json")
FALLBACK_DAYS = 5           # first run with no marker: look back this far (cross-run dedup trims it)
OVERLAP_DAYS = 1            # re-cover the last day: feeds publish late, dedup drops what already posted


def catchup_window():
    """(since, until) ISO dates: from just before the last successful run's end through today."""
    today = date.today()
    try:
        through = date.fromisoformat(json.load(open(LAST_SUCCESS))["through"])
        since = min(through - timedelta(days=OVERLAP_DAYS), today)
    except (OSError, ValueError, KeyError):
        since = today - timedelta(days=FALLBACK_DAYS)
    return since.isoformat(), today.isoformat()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(cmd, cwd=HERE, logfile=None, check=True, capture=None):
    """Runs a stage, streaming output to the console and the run log."""
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")
    for line in proc.stdout:
        sys.stdout.write(line)
        if logfile:
            logfile.write(line)
        if capture is not None:
            capture.append(line)
    proc.wait()
    if check and proc.returncode != 0:
        raise SystemExit(f"stage failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc.returncode


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = [(n or "").lstrip("﻿").strip().strip('"') for n in (reader.fieldnames or [])]
        rows = [{(k or "").lstrip("﻿").strip().strip('"'): v for k, v in r.items()} for r in reader]
    return fields, rows


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- gate

def link_is_dead(url):
    """True only on positive evidence: the posting answers 404/410."""
    import requests
    if not url or any(h in url.lower() for h in SKIP_LINK_HOSTS):
        return False
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    for method in (requests.head, requests.get):
        try:
            r = method(url, headers=headers, timeout=15, allow_redirects=True)
        except requests.RequestException:
            return False
        if r.status_code in (404, 410):
            continue            # confirm with GET before calling it dead
        return False
    return True


def gate(rows, check_links=True, force_hold=None):
    """Splits rows into (post, held, dropped_non_us, trimmed, repaired, dropped_stubs). Taxonomy is repaired first;
    held/dropped rows carry `hold_reason`. `force_hold` maps sourceLink -> reason."""
    force_hold = force_hold or {}
    sys.path.insert(0, HERE)
    from categorize_jobs import VALID_TRIPLES
    from fetch_full_descriptions import trim
    scraper = load_module("job_scraper_mod", os.path.join(SCRAPER_DIR, "job_scraper.py"))

    from taxonomy_repair import repair_row
    from job_keywords import build_matching_fields
    post, held, non_us, dropped_stubs = [], [], [], []
    trimmed = 0
    repaired = []
    for row in rows:
        # Jooble only publishes a truncated stub ending in "..." and its link can't be refetched,
        # so such a row can never be completed: drop it instead of holding it forever.
        jooble_text = (row.get("description") or "").strip()
        if (row.get("platform") or "").lower().startswith("jooble") and (
                len(jooble_text) < MIN_DESCRIPTION or jooble_text.endswith(("...", "…"))):
            row["hold_reason"] = "jooble_stub"
            dropped_stubs.append(row)
            continue
        before = (row.get("industry"), row.get("category"), row.get("subCategory"))
        if repair_row(row) == "repaired":
            repaired.append({"role": row.get("role"), "company": row.get("company"),
                             "from": " > ".join(x or "" for x in before),
                             "to": f'{row["industry"]} > {row["category"]} > {row["subCategory"]}'})
        # tags + requiredSkills are (re)built after the repair, from the description, role and address.
        tags, skills = build_matching_fields(row, row.get("subCategory", ""), row.get("category", ""))
        row["tags"], row["requiredSkills"] = ", ".join(tags), ", ".join(skills)
        reasons = []
        triple = (row.get("industry", "").strip(), row.get("category", "").strip(),
                  row.get("subCategory", "").strip())
        if not all(triple):
            reasons.append("taxonomy_missing")
        elif triple not in VALID_TRIPLES:
            reasons.append("taxonomy_invalid_triple")
        elif triple[1].lower().startswith("other") or "not specified" in triple[2].lower():
            reasons.append("taxonomy_other_fallback")

        tags = [t for t in (row.get("tags") or "").split(",") if t.strip()]
        if len(tags) < MIN_TAGS:
            reasons.append(f"tags_{len(tags)}_of_{MIN_TAGS}")

        desc = (row.get("description") or "").strip()
        if len(desc) < MIN_DESCRIPTION or desc.endswith(("...", "…")):
            reasons.append("description_stub")
        elif len(desc) > MAX_DESCRIPTION:
            row["description"] = trim(desc)
            trimmed += 1

        forced = force_hold.get((row.get("sourceLink") or "").strip())
        if forced:
            reasons.append(forced)

        verdict = scraper.is_usa_location(row.get("location", ""), desc)
        if verdict is False:
            row["hold_reason"] = "non_us"
            non_us.append(row)
            continue
        if verdict is None:
            reasons.append("location_unknown")

        if reasons:
            row["hold_reason"] = ",".join(reasons)
            held.append(row)
        else:
            post.append(row)

    if check_links and post:
        log(f"checking {len(post)} links for 404/410 ...")
        with ThreadPoolExecutor(max_workers=12) as pool:
            dead = list(pool.map(lambda r: link_is_dead((r.get("sourceLink") or "").strip()), post))
        alive = []
        for row, is_dead in zip(post, dead):
            if is_dead:
                row["hold_reason"] = "dead_link"
                held.append(row)
            else:
                alive.append(row)
        post = alive
    return post, held, non_us, trimmed, repaired, dropped_stubs


def notify(summary):
    """Writes daily_runs/LAST_STATUS.txt and, when ALERT_WEBHOOK_URL is set (a Slack/Discord-style
    incoming webhook), posts one line there. Never raises: alerting must not break a run."""
    status = summary.get("status")
    label = "OK" if status == "ok" else ("POSTED BUT NOT VERIFIED" if status == "posted_but_unverified" else "FAILED")
    verify = summary.get("verify") or {}
    issues = "".join(f" verify-{k}={v['problems']} problems" for k, v in verify.items()
                     if v.get("exit_code") == 1)
    line = (f"[{summary['date']}] daily jobs run {label}: "
            f"posted-ready={summary.get('to_post', 0)} held={summary.get('held', 0)} "
            f"scraped={summary.get('scraped', 0)}{issues}" + ("" if status == "ok" else f" | {status}"))
    try:
        with open(os.path.join(RUNS_DIR, "LAST_STATUS.txt"), "w") as f:
            f.write(line + "\n")
        url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
        if url:
            import requests
            requests.post(url, json={"text": line}, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"notify failed: {e}", file=sys.stderr)


# ----------------------------------------------------------------- pipeline

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="", help="scrape only this posting date (YYYY-MM-DD); "
                    "default catches up from the last successful run through today")
    ap.add_argument("--skip-scrape", action="store_true", help="reuse the raw CSV already in the run dir")
    ap.add_argument("--no-post", action="store_true", help="stop after the dry run; post nothing")
    ap.add_argument("--no-link-check", action="store_true", help="skip the 404/410 link check")
    ap.add_argument("--with-expire", action="store_true",
                    help="afterwards run the expiry sweep on both panels (takes hours)")
    ap.add_argument("--force", action="store_true", help="redo stages whose output already exists")
    ap.add_argument("--no-retry", action="store_true", help="don't retry once after a failed run")
    args = ap.parse_args()

    # Default run (no --date): catch up from the last successful run through today. Every run gets
    # its own folder, so a second run the same day picks up what appeared since the first.
    args.since = args.until = ""
    if args.date:
        args.run_name = f"{date.today().isoformat()}_for_{args.date}"
    else:
        args.since, args.until = catchup_window()
        args.run_name = datetime.now().strftime("%Y-%m-%d_%H%M") + f"_since_{args.since}"
        log(f"catch-up window: {args.since} -> {args.until}")
    try:
        run_once(args)
    except SystemExit as e:
        if not e.code or args.no_retry:
            raise
        # Resume-safe: stages reuse the files already saved and the dedup state skips rows that posted.
        log(f"run failed ({e.code}); retrying once")
        args.skip_scrape = True
        run_once(args)


def run_once(args):
    today = date.today().isoformat()
    # A run for a specific posting date gets its own folder, so it never reuses another run's files.
    run_dir = os.path.join(RUNS_DIR, args.run_name)
    os.makedirs(run_dir, exist_ok=True)
    p = lambda name: os.path.join(run_dir, name)

    lock = open(os.path.join(RUNS_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another daily_pipeline run is still going; exiting")

    logfile = open(p("pipeline.log"), "a", encoding="utf-8")
    summary = {"date": today, "started": datetime.now().isoformat(timespec="seconds")}
    done = lambda path: os.path.exists(path) and not args.force

    try:
        # 1. scrape ---------------------------------------------------------
        raw = p("raw.csv")
        if args.skip_scrape or done(raw):
            log(f"scrape: reusing {raw}")
        else:
            cmd = [PY, "job_scraper.py", "--out", raw]
            if args.date:
                cmd += ["--date", args.date]
            elif args.since:
                cmd += ["--since", args.since, "--until", args.until]
            run(cmd, cwd=SCRAPER_DIR, logfile=logfile)
        if not os.path.exists(raw):
            raise SystemExit(f"no raw CSV at {raw}")
        summary["scraped"] = len(read_csv(raw)[1])
        if summary["scraped"] == 0:
            log("scraper returned no new jobs; nothing to do")
            summary["status"] = "ok"
            return

        # 2. clean ----------------------------------------------------------
        clean = p("clean.csv")
        if not done(clean):
            run([PY, "clean_jobs_csv.py", "--csv", raw, "--out", clean,
                 "--dropped-out", p("dropped_by_clean.csv")], logfile=logfile)

        # 2b. full descriptions (before taxonomy, which reads them) --------
        full = p("full.csv")
        force_hold = {}
        if not done(full):
            run([PY, "fetch_full_descriptions.py", "--csv", clean, "--out", full, "--host", "usajobs",
                 "--workers", "4"], logfile=logfile)
            fields_f, rows_f = read_csv(full)
            ats = refetch_ats(rows_f)
            log(f"greenhouse/smartrecruiters/ashby descriptions: {ats}")
            summary["ats_refetch"] = ats
            write_csv(full, fields_f, rows_f)
        # A USAJOBS row the page fetch missed still carries the API's truncated text: hold it.
        unfetched = p("full_unfetched.csv")
        if os.path.exists(unfetched):
            force_hold = {(r.get("sourceLink") or "").strip(): "usajobs_description_unfetched"
                          for r in read_csv(unfetched)[1] if "usajobs" in (r.get("sourceLink") or "")}
        summary["usajobs_unfetched"] = len(force_hold)

        # 3. unique + taxonomy + 7 tags ------------------------------------
        ready = p("ready.csv")
        if not done(ready):
            run([PY, "prepare_jobs.py", "--csv", full, "--out", ready], logfile=logfile)

        # 4. gate -----------------------------------------------------------
        fields, rows = read_csv(ready)
        post_rows, held, non_us, trimmed, repaired, dropped_stubs = gate(rows, check_links=not args.no_link_check, force_hold=force_hold)
        hold_fields = fields + ["hold_reason"]
        write_csv(p("post.csv"), fields, post_rows)
        write_csv(p("held.csv"), hold_fields, held)
        write_csv(p("dropped_non_us.csv"), hold_fields, non_us)
        write_csv(p("dropped_jooble_stubs.csv"), hold_fields, dropped_stubs)
        with open(p("taxonomy_repaired.json"), "w") as f:
            json.dump(repaired, f, indent=2)
        summary.update(ready=len(rows), to_post=len(post_rows), held=len(held),
                       dropped_non_us=len(non_us), dropped_jooble_stubs=len(dropped_stubs),
                       trimmed_over_8000=trimmed,
                       taxonomy_repaired=len(repaired),
                       held_reasons=dict(Counter(r for h in held for r in h["hold_reason"].split(","))))
        log(f"gate: {len(post_rows)} to post, {len(held)} held, {len(non_us)} non-US dropped, {len(dropped_stubs)} Jooble stubs dropped, "
            f"{trimmed} trimmed to <=8000, {len(repaired)} taxonomy rows repaired")

        # 5. post (both panels) --------------------------------------------
        if post_rows:
            run([PY, "send_csv_to_admin_api.py", "--csv", p("post.csv"), "--dry-run"], logfile=logfile)
        if args.no_post or not post_rows:
            log("not posting (--no-post or nothing to post)")
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            for state in ("admin_sent_jobs.json", "admin_sent_jobs.mirror.json"):
                src = os.path.join(HERE, state)
                if os.path.exists(src):
                    shutil.copy2(src, f"{src}.bak-{stamp}")
            code = run([PY, "-u", "send_csv_to_admin_api.py", "--csv", p("post.csv")],
                       logfile=logfile, check=False)
            summary["post_exit_code"] = code
            if code != 0:
                raise SystemExit(f"posting exited {code}; rerun is safe, the dedup state skips what landed")

            # 6. verify ----------------------------------------------------
            # exit 0 = clean, 1 = checked and found problems, 2 = panel unreachable (retried here).
            summary["verify"] = {}
            for panel in ("primary", "mirror"):
                for attempt in range(1, 4):
                    out = []
                    code = run([PY, "verify_panel.py", "--panel", panel], logfile=logfile,
                               check=False, capture=out)
                    if code != 2:
                        break
                    log(f"verify {panel}: panel unreachable (attempt {attempt}/3)")
                    time.sleep(120)
                problems = None
                for line in out:
                    m = re.search(r"audited — (\d+) problems", line)
                    if m:
                        problems = int(m.group(1))
                summary["verify"][panel] = {"exit_code": code, "problems": problems if code != 0 else 0}
            if any(v["exit_code"] == 2 for v in summary["verify"].values()):
                summary["status"] = "posted_but_unverified"
            else:
                summary["status"] = "ok"

        # 7. optional expiry sweep ----------------------------------------
        if args.with_expire:
            for panel_args in ([], ["--panel", "mirror"]):
                run([PY, "expire_jobs.py", *panel_args, "--fetch", "--check", "--browser", "--apply"],
                    logfile=logfile, check=False)
        summary.setdefault("status", "ok")
    except SystemExit as e:
        summary["status"] = f"failed: {e}"
        raise
    finally:
        summary["finished"] = datetime.now().isoformat(timespec="seconds")
        if args.since and not args.no_post and summary.get("status") in ("ok", "posted_but_unverified"):
            # Only a fully posted catch-up moves the marker, so a failed run is re-covered next time.
            with open(LAST_SUCCESS, "w") as f:
                json.dump({"through": args.until, "run": args.run_name}, f)
            summary["window"] = [args.since, args.until]
        with open(p("summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        log("summary: " + json.dumps(summary))
        notify(summary)
        logfile.close()


if __name__ == "__main__":
    main()
