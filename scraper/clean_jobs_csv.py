"""Normalizes a payload-shaped jobs CSV so nothing is silently mangled by the
admin intake API's DTO limits or shown wrong on the panel.

Input and output are both the shape send_csv_to_admin_api.py posts. The source
file is never modified in place. Every change is counted and reported, so a run
can be reviewed before the rows are posted to the live panel.

    python3 clean_jobs_csv.py --csv colorado_3k_jobs.csv --out colorado_clean.csv

Fixes, in the order the audit found them:
  * company recovered from the description's lead-in when the scrape missed it;
    rows with no recoverable company are dropped (the API rejects them anyway)
  * salary 0 blanked (the DTO accepts 0, and the panel renders it as "$0")
  * hourly and monthly rates stored in an annual field converted to annual
  * description is never trimmed: the posting goes up whole. Rows over the
    intake's 8000-char limit are counted as description_over_limit so they can
    be dealt with before posting, never cut short
  * role trimmed to 160 chars on a word boundary
  * tag/skill casing folded to one canonical spelling per tag, de-duplicated
  * seniority folded onto the vocabulary the panel already filters by
  * remote=True kept only when the posting text actually says so
  * thin tag lists topped up from the description, using only vocabulary the rest
    of the file already uses as tags
"""
import argparse
import collections
import csv
import re
import sys

csv.field_size_limit(10 ** 7)

MAX_ROLE = 160
MAX_DESCRIPTION = 8000
MAX_TAG_LEN = 50
HOURS_PER_YEAR = 2080

# Salary figures arrive in whatever period the posting used. The bands below come
# from the actual spread in the source file: 15-100 is an hourly rate (216 rows),
# 1k-30k is a monthly one (13 rows, mostly Colorado state jobs), and 30k+ is
# already annual. Anything between them is ambiguous (a day rate? a stipend?) and
# is blanked rather than guessed at, since a wrong salary on the panel is worse
# than a missing one.
HOURLY_MIN, HOURLY_MAX = 15.0, 100.0
MONTHLY_MIN, MONTHLY_MAX = 1000.0, 30000.0
HOURS_PER_YEAR = 2080
MONTHS_PER_YEAR = 12

# Tags arrive title-cased by the extractor ("Aws", "Ci/Cd", "Node.Js"), which
# splits one tag into two entries in the panel's filter list. Anything not
# listed here keeps the casing it came with.
CANONICAL_TAGS = {
    "aws": "AWS", "gcp": "GCP", "sql": "SQL", "nosql": "NoSQL", "html": "HTML",
    "css": "CSS", "api": "API", "apis": "APIs", "rest": "REST", "restful": "RESTful",
    "graphql": "GraphQL", "ci/cd": "CI/CD", "cicd": "CI/CD", "etl": "ETL",
    "saas": "SaaS", "ui": "UI", "ux": "UX", "ui/ux": "UI/UX", "qa": "QA",
    "ml": "ML", "ai": "AI", "nlp": "NLP", "llm": "LLM", "iot": "IoT",
    "devops": "DevOps", "mlops": "MLOps", "sre": "SRE", "tdd": "TDD",
    "node.js": "Node.js", "nodejs": "Node.js", "next.js": "Next.js",
    "vue.js": "Vue.js", "react.js": "React.js", "asp.net": "ASP.NET",
    ".net": ".NET", "c#": "C#", "c++": "C++", "php": "PHP", "ios": "iOS",
    "macos": "macOS", "postgresql": "PostgreSQL", "mysql": "MySQL",
    "mongodb": "MongoDB", "javascript": "JavaScript", "typescript": "TypeScript",
    "github": "GitHub", "gitlab": "GitLab", "jira": "Jira", "kubernetes": "Kubernetes",
    "grpc": "gRPC", "json": "JSON", "xml": "XML", "yaml": "YAML", "http": "HTTP",
    "tcp/ip": "TCP/IP", "hipaa": "HIPAA", "erp": "ERP", "crm": "CRM",
    "hvac": "HVAC", "cdl": "CDL", "rn": "RN", "lpn": "LPN", "cpa": "CPA",
    "pmp": "PMP", "osha": "OSHA", "b2b": "B2B", "b2c": "B2C", "seo": "SEO",
}

# The panel filters on these, so near-misses ("Entry level", "Mid-Senior Level")
# have to fold onto one spelling or they become separate filter options.
CANONICAL_SENIORITY = {
    "entry level": "Entry Level", "entry-level": "Entry Level", "entry": "Entry Level",
    "junior": "Junior", "jr": "Junior",
    "associate": "Associate",
    "mid": "Mid Level", "mid level": "Mid Level", "mid-level": "Mid Level",
    "mid-senior level": "Mid-Senior Level", "mid senior level": "Mid-Senior Level",
    "senior": "Senior", "sr": "Senior",
    "lead": "Lead", "staff": "Staff", "principal": "Principal",
    "manager": "Manager", "manager / director": "Manager / Director",
    "director": "Director", "executive": "Executive", "vp": "Executive",
    "intern": "Internship", "internship": "Internship",
}

# "Remote" in a posting is usually stated outright; a hybrid posting that also
# says "remote" is caught by the hybrid check first, since hybrid is not remote.
REMOTE_RE = re.compile(
    r"\b(fully|100%|work)?[\s-]*remote\b|\bwork from home\b|\btelework\b|\btelecommut",
    re.I,
)
HYBRID_RE = re.compile(r"\bhybrid\b|\bon[\s-]?site\b|\bin[\s-]?office\b|\bin[\s-]?person\b", re.I)

# The company name a description leads with, e.g. "**Quindar**" or "About Acme:".
# Only the bolded lead-in and an explicit "X is a/an ..." sentence are trusted;
# looser patterns pulled headings like "Company Overview:" and the role title
# itself into the company field, which is worse on the panel than no row at all.
LEAD_COMPANY_RES = (
    re.compile(r"^\s*\*\*([A-Z][^*\n]{1,80}?)\*\*"),
    re.compile(r"^\s*(?:About|Join)\s+([A-Z][\w&.,'\- ]{1,60}?)[:!.\n]"),
    re.compile(r"^\s*([A-Z][\w&.,'\- ]{1,60}?)\s+is\s+(?:a|an|the)\b"),
)
# A match containing any of these is a section heading or a job title, not a name.
NOT_A_COMPANY = re.compile(
    r"\b(job|summary|overview|description|position|responsibilities|requirements|"
    r"benefits|about|company|location|role|duties|opportunity|posting|announcement|"
    r"salary|schedule|department|qualifications|engineer|manager|developer|analyst|"
    r"supervisor|superintendent|consultant|technician|specialist|director|"
    r"coordinator|assistant|associate|intern|hourly|full[\s-]?time|part[\s-]?time)\b",
    re.I,
)
# Bold lead-ins are as often a section heading as a name ("**What You'll Be
# Doing**"), and headings open with these words where company names do not.
HEADING_START = re.compile(
    r"^(what|who|why|how|where|when|this|you|we|our|your|the|a|an|here|welcome|"
    r"join|come|are|is|do|key|top|new|now|apply)\b",
    re.I,
)


def clean_header(name):
    return (name or "").lstrip("﻿").strip().strip('"')


def text(row, field):
    return (row.get(field) or "").strip()


def recover_company(description, role):
    """Best-effort company name from the posting text, for rows the scrape left
    blank. Returns "" whenever the candidate looks like a heading or restates the
    role, so a doubtful row is dropped instead of posted under a fake name."""
    head = description[:600]
    role_words = {w for w in re.findall(r"[a-z]+", role.lower()) if len(w) > 3}
    for pattern in LEAD_COMPANY_RES:
        match = pattern.search(head)
        if not match:
            continue
        candidate = match.group(1).strip().strip(",.:;-").strip()
        if len(candidate) < 3 or len(candidate.split()) > 4:
            continue
        if NOT_A_COMPANY.search(candidate) or HEADING_START.match(candidate):
            continue
        # A candidate built out of the role's own words is the title echoed back.
        candidate_words = {w for w in re.findall(r"[a-z]+", candidate.lower()) if len(w) > 3}
        if candidate_words and candidate_words <= role_words:
            continue
        return candidate
    return ""


def parse_number(value):
    raw = str(value or "").strip().replace(",", "").replace("$", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def fix_salary(minimum, maximum):
    """Returns (min, max, note) in annual dollars. 0 means "not listed", not
    "unpaid", and a figure below the annual bands is an hourly or monthly rate
    the source pipeline never converted."""
    low, high = parse_number(minimum), parse_number(maximum)
    values = [v for v in (low, high) if v]
    if not values:
        return "", "", "blanked_zero" if (low == 0 or high == 0) else ""

    anchor = min(values)
    if anchor > MONTHLY_MAX:
        factor, note = 1, ""
    elif MONTHLY_MIN <= anchor <= MONTHLY_MAX:
        factor, note = MONTHS_PER_YEAR, "monthly_to_annual"
    elif HOURLY_MIN <= anchor <= HOURLY_MAX:
        factor, note = HOURS_PER_YEAR, "hourly_to_annual"
    else:
        return "", "", "dropped_implausible"

    low = low * factor if low else 0
    high = high * factor if high else 0
    return (
        str(int(low)) if low else "",
        str(int(high)) if high else "",
        note,
    )


def trim_role(role):
    if len(role) <= MAX_ROLE:
        return role, False
    window = role[:MAX_ROLE]
    cut = window.rfind(" ")
    return (window[:cut] if cut > MAX_ROLE * 0.6 else window).rstrip(" -,"), True


def canonical_tag(tag):
    """One spelling per tag. An all-lowercase tag is title-cased ("java" and
    "Java" are one filter option, not two); a tag that already carries capitals
    is left alone, so "SIEM Tools" and "ERP" survive intact."""
    canonical = CANONICAL_TAGS.get(tag.lower())
    if canonical:
        return canonical
    return tag.title() if tag.islower() else tag


def clean_list(value):
    """Canonicalizes casing, drops over-length tags rather than truncating them
    mid-word, and de-duplicates case-insensitively while keeping order."""
    seen, out, dropped = set(), [], 0
    for part in str(value or "").split(","):
        item = canonical_tag(part.strip())
        if not item:
            continue
        if len(item) > MAX_TAG_LEN:
            dropped += 1
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return ", ".join(out), dropped


MIN_TAGS = 10
VOCAB_MIN_USES = 3


def build_tag_vocabulary(rows):
    """The tags the rest of the file already uses, so a thin row is topped up
    with real skills instead of whatever words its description happens to open
    with. A term used only once or twice is usually extractor noise, so only
    tags seen VOCAB_MIN_USES times are trusted, and each is matched on word
    boundaries to keep "R" or "Go" out of ordinary prose."""
    counts = collections.Counter()
    for row in rows:
        for tag in str(row.get("tags") or "").split(","):
            tag = canonical_tag(tag.strip())
            if len(tag) >= 2:
                counts[tag] += 1
    vocabulary = []
    for tag, uses in counts.most_common():
        if uses < VOCAB_MIN_USES or len(tag) < 3:
            continue
        vocabulary.append((tag, re.compile(r"(?<![\w+#.])" + re.escape(tag) + r"(?![\w+#])", re.I)))
    return vocabulary


def enrich_tags(row, vocabulary):
    """Tops a thin tag list up to MIN_TAGS from the posting text. Returns the new
    tag string and how many were added."""
    tags = [t.strip() for t in str(row.get("tags") or "").split(",") if t.strip()]
    if len(tags) >= MIN_TAGS:
        return ", ".join(tags), 0
    seen = {t.lower() for t in tags}
    haystack = f"{text(row, 'role')}\n{text(row, 'description')}"
    added = 0
    for tag, pattern in vocabulary:
        if len(tags) >= MIN_TAGS:
            break
        if tag.lower() in seen or not pattern.search(haystack):
            continue
        seen.add(tag.lower())
        tags.append(tag)
        added += 1
    return ", ".join(tags), added


def fix_seniority(value):
    return CANONICAL_SENIORITY.get(value.strip().lower(), value.strip())


def fix_remote(row):
    """The audit found 853 rows flagged remote whose location is a Colorado
    city, so the flag is only trusted when the posting text backs it up."""
    flagged = text(row, "remote").lower() in ("true", "1", "yes")
    if not flagged:
        return "False", False
    haystack = f"{text(row, 'location')} {text(row, 'role')} {text(row, 'description')}"
    if "remote" in text(row, "location").lower():
        return "True", False
    if HYBRID_RE.search(haystack) and not REMOTE_RE.search(haystack):
        return "False", True
    if REMOTE_RE.search(haystack):
        return "True", False
    return "False", True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True, help="new CSV; --csv is never overwritten")
    parser.add_argument("--dropped-out", default="",
                        help="optional CSV to write rows that had to be dropped")
    args = parser.parse_args()

    if args.out == args.csv:
        sys.exit("--out must differ from --csv")

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [clean_header(name) for name in reader.fieldnames]
        rows = [{clean_header(k): v for k, v in row.items()} for row in reader]

    stats = {k: 0 for k in (
        "company_recovered", "dropped_no_company", "salary_blanked", "salary_hourly", "salary_monthly",
        "salary_dropped", "description_over_limit", "role_trimmed", "tags_dropped",
        "tags_deduped", "tags_enriched_rows", "tags_added", "skills_deduped", "seniority_normalized", "remote_cleared",
    )}
    kept, dropped_rows = [], []
    vocabulary = build_tag_vocabulary(rows)

    for row in rows:
        company = text(row, "company")
        if not company:
            company = recover_company(text(row, "description"), text(row, "role"))
            if company:
                stats["company_recovered"] += 1
                row["company"] = company
            else:
                stats["dropped_no_company"] += 1
                dropped_rows.append(row)
                continue

        low, high, note = fix_salary(row.get("salaryMin"), row.get("salaryMax"))
        row["salaryMin"], row["salaryMax"] = low, high
        if note == "blanked_zero":
            stats["salary_blanked"] += 1
        elif note == "hourly_to_annual":
            stats["salary_hourly"] += 1
        elif note == "monthly_to_annual":
            stats["salary_monthly"] += 1
        elif note == "dropped_implausible":
            stats["salary_dropped"] += 1

        stats["description_over_limit"] += len(text(row, "description")) > MAX_DESCRIPTION

        role, trimmed = trim_role(text(row, "role"))
        row["role"] = role
        stats["role_trimmed"] += trimmed

        before = text(row, "tags")
        row["tags"], dropped = clean_list(before)
        stats["tags_dropped"] += dropped
        stats["tags_deduped"] += row["tags"] != before

        row["tags"], added = enrich_tags(row, vocabulary)
        if added:
            stats["tags_enriched_rows"] += 1
            stats["tags_added"] += added

        before = text(row, "requiredSkills")
        row["requiredSkills"], _ = clean_list(before)
        stats["skills_deduped"] += row["requiredSkills"] != before

        before = text(row, "seniority")
        row["seniority"] = fix_seniority(before)
        stats["seniority_normalized"] += row["seniority"] != before

        row["remote"], cleared = fix_remote(row)
        stats["remote_cleared"] += cleared

        kept.append(row)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    if args.dropped_out and dropped_rows:
        with open(args.dropped_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dropped_rows)

    print(f"in={len(rows)} out={len(kept)} -> {args.out}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
