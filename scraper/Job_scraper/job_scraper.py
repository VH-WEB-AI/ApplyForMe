#!/usr/bin/env python3
"""
Multi-portal job scraper
=========================
Scrapes job postings from the 67 portals listed in portals.csv and normalizes
each posting into a common schema:

    Portal, Job Title, Company, URL, About the job, Pay, Location, Summary,
    Requirements, Responsibilities, Benefits, Scraped At

Output: jobs_output.csv (in the same folder, or --out path)

--------------------------------------------------------------------------
IMPORTANT / HONEST LIMITATIONS (read before you rely on this)
--------------------------------------------------------------------------
Not every one of the 67 portals can be reliably scraped with plain HTTP
requests. They fall into four buckets (see the "scrape_method" column in
portals.csv):

  1. api            - Real public JSON API. Fully automated, high reliability.
                       (Remote OK, Remotive, Hacker News Who's Hiring)
  2. rss             - Real public RSS feed. Fully automated, high reliability.
                       (We Work Remotely, Working Nomads, NoDesk)
  3. api_key_required- Real official API but YOU must supply a free API key/
                       email as an environment variable. Fully automated once
                       you have the key. (Adzuna, Jooble, USAJOBS)
  4. generic         - No public API/feed found. The script does a best-effort
                       generic HTML scrape (find job links -> open each -> pull
                       text -> split into sections with keyword heuristics).
                       This WILL work on plain server-rendered sites and WILL
                       silently return few/no rows on JS-heavy sites (React/
                       Next.js, e.g. Built In) or sites with bot protection
                       (e.g. Cloudflare-protected boards, LinkedIn-style
                       login walls). Those need a headless browser
                       (Playwright/Selenium) or manual export, which is out
                       of scope for a single requests-based script.
  5. dead            - Portal no longer exists as a live source (GitHub Jobs
                       Archive was shut down by GitHub) - skipped.

This script cannot be executed against the live internet from this sandbox
(network is restricted to package registries), so it has NOT been tested
against the real sites. Run it yourself with `python3 job_scraper.py`.
Please also check each site's Terms of Service / robots.txt before scraping
at scale, and keep request volume + frequency polite.
--------------------------------------------------------------------------
"""

import csv
import os
import re
import sys
# Full job descriptions are kept untruncated, so CSV fields can be large;
# remove the reader's default 128KB per-field cap to read/write them safely.
csv.field_size_limit(sys.maxsize)
import time
import json
import argparse
import unicodedata
from html import unescape as html_unescape
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urljoin, urlparse, urlsplit
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
load_dotenv()

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Force IPv4 for all outbound requests.
# On some Linux machines Python's DNS resolver picks the IPv6 address returned
# by the system resolver but the machine has no working IPv6 route, causing
# "Name or service not known" / "Temporary failure in name resolution" even
# though ping (which uses the system resolver directly) works fine.
# Monkey-patching socket.getaddrinfo to return only AF_INET (IPv4) results
# fixes this transparently for the entire requests session.
# ---------------------------------------------------------------------------
import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo

def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)

_socket.getaddrinfo = _ipv4_getaddrinfo

# ---------------------------------------------------------------------------
# Date-window utilities
#
# The scraper collects jobs posted in the previous 5 calendar days,
# excluding today. So if today is 2026-09-07, the window is:
#   2026-09-02 (inclusive) → 2026-09-06 (inclusive)
#
# "Today" is evaluated in UTC at scrape time and is always excluded so
# that partially-published feeds (jobs posted hours ago with no end-of-day
# cut-off) don't create a gap on the next run.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SINGLE-DAY SCRAPE — set this to scrape one specific date instead of the
# default 5-day rolling window.
#
# Option 1 (edit here):  change None to a date, e.g. date(2026, 9, 8)
# Option 2 (CLI):        python3 job_scraper.py --date 2026-09-08
#
# Leave as None to use the default 5-day window.
# ---------------------------------------------------------------------------
DATE_OVERRIDE = None  # e.g. date(2026, 9, 8)
# Catch-up range (--since/--until): inclusive (start, end) covering every day since the last run.
DATE_RANGE = None


def get_date_window():
    """Return (window_start, window_end) as UTC date objects.

    When DATE_OVERRIDE is set (a single target day), the window collapses to
    exactly that day (start == end == override) so scrapers collect postings
    posted on that date and nothing else.

    Otherwise the window covers the previous 5 calendar days, excluding both
    today AND yesterday, so that half-published feeds from the last 48 hours
    don't create gaps on re-runs.

    Example: if today is 2026-09-08, window = 2026-09-03 → 2026-09-06
      (yesterday 2026-09-07 is also excluded along with today 2026-09-08)

    window_end   = today - 2 days  (i.e. the day before yesterday)
    window_start = today - 6 days  (5 days back from window_end)
    """
    if DATE_RANGE is not None:
        return DATE_RANGE
    if DATE_OVERRIDE is not None:
        return DATE_OVERRIDE, DATE_OVERRIDE
    today = datetime.now(timezone.utc).date()
    window_end = today - timedelta(days=2)    # exclude yesterday + today
    window_start = window_end - timedelta(days=4)  # 5-day span inclusive
    return window_start, window_end


def set_date_range(since, until=None):
    """Pin scraping to every day from `since` through `until` (default today, UTC), inclusive."""
    global DATE_RANGE
    start = parse_date(since)
    if start is None:
        raise SystemExit(f"error: could not parse --since date: {since!r}")
    end = parse_date(until) if until else datetime.now(timezone.utc).date()
    if end is None:
        raise SystemExit(f"error: could not parse --until date: {until!r}")
    if end < start:
        raise SystemExit(f"error: --until {end} is before --since {start}")
    DATE_RANGE = (start, end)


def set_date_override(value):
    """Pin scraping to a single calendar day, e.g. --date 2026-09-07."""
    global DATE_OVERRIDE
    if value is None:
        DATE_OVERRIDE = None
        return
    parsed = parse_date(value)
    if parsed is None:
        raise SystemExit(f"error: could not parse date override: {value!r}")
    DATE_OVERRIDE = parsed


def parse_date(value) -> date | None:
    """Parse a date/datetime string or epoch int into a UTC date.
    Returns None if the value cannot be parsed.
    """
    if not value:
        return None
    # Already a date/datetime
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    # Unix timestamp (int or float)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (OSError, OverflowError, ValueError):
            return None
    value = str(value).strip()
    if not value:
        return None
    # Strip trailing timezone name in parens e.g. "Mon, 01 Sep 2026 00:00:00 +0000 (UTC)"
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    # Try common formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo:
                return dt.astimezone(timezone.utc).date()
            return dt.date()
        except ValueError:
            continue
    return None


def is_in_window(date_value) -> bool | None:
    """Return True if date_value falls inside the 5-day window (excl. today),
    False if it is outside, None if the date cannot be determined.
    """
    d = parse_date(date_value)
    if d is None:
        return None
    start, end = get_date_window()
    return start <= d <= end


def is_too_old(date_value) -> bool:
    """Return True if the date is older than the window start.
    Used to stop pagination early — once results are this old we won't
    find newer ones further into the feed/page list.
    """
    d = parse_date(date_value)
    if d is None:
        return False  # can't tell, keep going
    start, _ = get_date_window()
    return d < start

ENABLE_LLM_REFINEMENT = os.environ.get("ENABLE_LLM_REFINEMENT", "").strip().lower() in {
    "1", "true", "yes", "on"
}

# When enabled, technical (software/IT/engineering) jobs are prioritized
# (sorted first) in the output, but non-technical jobs are still kept.
# Overridable via the --technical-only / --include-nontechnical flags.
TECHNICAL_ONLY = os.environ.get("TECHNICAL_ONLY", "1").strip().lower() in {
    "1", "true", "yes", "on"
}

try:
    import feedparser
except ImportError:
    feedparser = None

if ENABLE_LLM_REFINEMENT:
    try:
        from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
        TRANSFORMERS_AVAILABLE = True
    except ImportError:
        AutoTokenizer = AutoModelForCausalLM = pipeline = None
        TRANSFORMERS_AVAILABLE = False
else:
    AutoTokenizer = AutoModelForCausalLM = pipeline = None
    TRANSFORMERS_AVAILABLE = False

try:
    from huggingface_hub import HfApi
    HUGGINGFACE_HUB_AVAILABLE = True
except ImportError:
    HfApi = None
    HUGGINGFACE_HUB_AVAILABLE = False

_SKILLS_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "data", "skills_taxonomy.json")
_skills_taxonomy_lookup = None
_skills_taxonomy_aliases = None


def _load_skills_taxonomy():
    """Lazy-loaded {alias: canonical_name} lookup, mirroring backend/app/services/
    skill_extractor.py — duplicated (not imported) so the scraper stays a standalone
    deployable with no dependency on the backend package."""
    global _skills_taxonomy_lookup
    if _skills_taxonomy_lookup is not None:
        return _skills_taxonomy_lookup
    with open(_SKILLS_TAXONOMY_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    lookup = {}
    for canonical, aliases in raw.items():
        for alias in aliases:
            lookup[alias.lower()] = canonical
    _skills_taxonomy_lookup = lookup
    return lookup


def _taxonomy_search_key(text):
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f" {text} "


def _load_skills_taxonomy_aliases():
    global _skills_taxonomy_aliases
    if _skills_taxonomy_aliases is not None:
        return _skills_taxonomy_aliases
    aliases = []
    for alias, canonical in _load_skills_taxonomy().items():
        if alias in _AMBIGUOUS_SKILL_ALIASES:
            continue
        aliases.append((_taxonomy_search_key(alias), canonical))
    _skills_taxonomy_aliases = aliases
    return aliases


# Job type detection patterns
_JOB_TYPE_PATTERNS = [
    ("Internship",  re.compile(r"\bintern(ship)?\b", re.I)),
    ("Part-time",   re.compile(r"\bpart[\s\-]?time\b", re.I)),
    ("Contract",    re.compile(r"\b(contract(or)?|freelance|1099|temp(orary)?|fixed[\s\-]term)\b", re.I)),
    ("Remote",      re.compile(r"\b(remote|work from home|wfh|distributed|fully remote)\b", re.I)),
    ("Full-time",   re.compile(r"\bfull[\s\-]?time\b", re.I)),
]

REQUESTED_FIELDNAMES = [
    "company", "role", "platform", "location", "remote", "salaryMin",
    "salaryMax", "seniority", "visaSponsorship", "employmentType",
    "description", "requiredSkills", "tags", "sourceLink", "active",
]


# ---------------------------------------------------------------------------
# Tag extraction for resume matching.
#
# Every tag a job gets comes from that job's own text (title, About the job,
# Summary, Requirements, Responsibilities, Benefits) via high-confidence
# structured signals:
#
#   1. Known skill/tech taxonomy matches   - real tools/technologies actually
#                                             named in the posting (skills_
#                                             taxonomy.json), not injected
#                                             based on job title.
#   2. Seniority / employment type         - senior, junior, intern,
#                                             full-time, contract, remote, etc.
#   3. N-years-experience                  - explicit years required in text.
#
# We intentionally do not add open-ended keyword phrases to `tags`; those were
# too noisy for matching and could include addresses, boilerplate, or sentence
# fragments.
# ---------------------------------------------------------------------------

_CUE_PHRASE_RE = re.compile(
    r"\b(?:"
    r"\d+\+?\s*years?(?:\s+of)?\s+experience\s+(?:in|with|using)"
    r"|experience\s+(?:in|with|using)"
    r"|proficien(?:t|cy)\s+(?:in|with)"
    r"|knowledge\s+of"
    r"|familiar(?:ity)?\s+with"
    r"|background\s+in"
    r"|expertise\s+in"
    r"|skilled\s+in"
    r"|strong\s+(?:understanding|knowledge)\s+of"
    r"|working\s+knowledge\s+of"
    r"|hands[\s\-]on\s+experience\s+(?:in|with)"
    r"|certified\s+in"
    r")\s+([A-Za-z][A-Za-z0-9+#./&\- ]{1,60}?)(?=[,.;:\n]|\s+(?:and|or|is|are|to)\b|$)",
    re.I,
)

# Words that are too generic to be useful tags even when they show up as the
# object of a cue phrase or as a high-scoring RAKE phrase. A RAKE phrase is
# dropped entirely if every one of its words falls in this set.
_TAG_FILLER_WORDS = {
    "a", "an", "the", "our", "your", "their", "this", "that", "these", "those",
    "job", "role", "position", "candidate", "candidates", "team", "teams",
    "company", "work", "working", "including", "etc", "related", "various",
    "similar", "other", "relevant", "strong", "excellent", "good", "years",
    "year", "experience", "skills", "skill", "knowledge", "ability", "abilities",
    "required", "requirement", "requirements", "necessary", "preferred",
    "ideal", "plus", "join", "assist", "assisting", "duties", "apply",
    "applicants", "looking", "seeking", "must", "responsibilities", "benefits",
    "summary", "description", "opportunity", "environment",
    # Generic words that come from the intro context sentence or section headers —
    # not useful as single-word resume-matching tags.
    "hiring", "location", "compensation", "organisation", "organization",
    "key", "technologies",
}

# Stopwords used to break the posting text into RAKE candidate phrases.
_RAKE_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "itself", "just", "me", "more", "most", "my", "myself", "no",
    "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other",
    "our", "ours", "ourselves", "out", "over", "own", "s", "same", "she",
    "should", "so", "some", "such", "t", "than", "that", "the", "their",
    "theirs", "them", "themselves", "then", "there", "these", "they", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "you", "your", "yours", "yourself", "yourselves",
    "etc", "eg", "ie", "per", "via", "including", "include", "includes",
    "job", "role", "position", "candidate", "candidates", "team", "teams",
    "company", "work", "working", "must", "may", "also", "well", "using",
    "use", "used", "within", "across", "across", "including", "based",
    "looking", "seeking", "apply", "application", "please", "us", "we're",
    "you'll", "we'll", "an", "-",
}

_RAKE_WORD_RE = re.compile(r"^[a-z0-9][a-z0-9+#.]*$")
# Sentence/list punctuation only — NOT the apostrophe, so contractions and
# possessives ("you'll", "company's") stay intact as single tokens and are
# rejected by _RAKE_WORD_RE as a unit (correctly breaking the phrase) rather
# than being torn into meaningless fragments like "ll" or "s". A period is
# only treated as a delimiter when it ends a sentence (followed by
# whitespace/end), so abbreviations and dotted names inside a phrase aren't
# split mid-word.
_RAKE_SPLIT_RE = re.compile(r"[,;:!?()\[\]\u201c\u201d/|\\]+|\.(?=\s|$)")
_YEARS_EXPERIENCE_PHRASE_RE = re.compile(
    r"^\d+\+?\s*years?(?:\s+of)?\s+experience(?:\s+\w+)?$", re.I
)


def _extract_cue_phrase_terms(text):
    """Pull the objects of "experience with X" / "knowledge of Y"-style
    phrases straight out of the posting text — these are skills/topics the
    listing itself explicitly calls out."""
    terms = []
    for m in _CUE_PHRASE_RE.finditer(text or ""):
        chunk = m.group(1)
        for part in re.split(r",|\band\b|\bor\b|/|&", chunk, flags=re.I):
            part = part.strip(" .;:-")
            part = re.sub(r"\s+", " ", part)
            words = part.split(" ")
            start, end = 0, len(words)
            while start < end and words[start].lower() in _TAG_FILLER_WORDS:
                start += 1
            while end > start and words[end - 1].lower() in _TAG_FILLER_WORDS:
                end -= 1
            part = " ".join(words[start:end])
            if not part:
                continue
            if part.lower() in _TAG_FILLER_WORDS:
                continue
            if len(part) < 2 or len(part) > 40:
                continue
            if part.isdigit():
                continue
            if _YEARS_EXPERIENCE_PHRASE_RE.match(part):
                continue  # redundant with the separate N-years-experience tag
            terms.append(part)
    return terms


def _rake_keywords(text, top_k=12, max_phrase_words=4):
    """Minimal, dependency-free RAKE (Rapid Automatic Keyword Extraction).

    Splits `text` into candidate phrases at stopwords/punctuation, scores
    each surviving word by degree-of-co-occurrence / frequency, scores each
    phrase as the sum of its words' scores, and returns the top phrases in
    their original casing. This surfaces whatever terms are actually
    distinctive in *this* posting's text rather than matching against any
    fixed tag vocabulary.
    """
    if not text:
        return []

    phrases = []
    for chunk in _RAKE_SPLIT_RE.split(text.replace("\n", ". ")):
        current = []
        for raw_word in chunk.split():
            word = raw_word.rstrip(".")
            lower = word.lower()
            if not lower or lower in _RAKE_STOPWORDS or not _RAKE_WORD_RE.match(lower):
                if current:
                    phrases.append(current)
                    current = []
                continue
            current.append(word)
        if current:
            phrases.append(current)

    # Trim generic/boilerplate words off each phrase's edges (keeps a strong
    # interior term like "gRPC" out of a weaker wrapper like "gRPC preferred").
    trimmed = []
    for p in phrases:
        start, end = 0, len(p)
        while start < end and p[start].lower() in _TAG_FILLER_WORDS:
            start += 1
        while end > start and p[end - 1].lower() in _TAG_FILLER_WORDS:
            end -= 1
        if end > start:
            trimmed.append(p[start:end])
    phrases = trimmed

    phrases = [p for p in phrases if 1 <= len(p) <= max_phrase_words]
    # Drop phrases made up entirely of generic/boilerplate words — they
    # aren't useful tags even if they scored well structurally.
    phrases = [
        p for p in phrases
        if not all(w.lower() in _TAG_FILLER_WORDS for w in p)
        and not _YEARS_EXPERIENCE_PHRASE_RE.match(" ".join(p))
    ]
    if not phrases:
        return []

    freq, degree = {}, {}
    for phrase in phrases:
        co_occurrence = len(phrase) - 1
        for word in phrase:
            key = word.lower()
            freq[key] = freq.get(key, 0) + 1
            degree[key] = degree.get(key, 0) + co_occurrence
    for key in freq:
        degree[key] += freq[key]  # RAKE degree includes the word itself

    best_score, best_display = {}, {}
    for phrase in phrases:
        key = " ".join(w.lower() for w in phrase)
        score = sum(degree[w.lower()] / freq[w.lower()] for w in phrase)
        if key not in best_score or score > best_score[key]:
            best_score[key] = score
            best_display[key] = " ".join(phrase)

    ranked = sorted(best_score.items(), key=lambda kv: kv[1], reverse=True)
    keywords = []
    for key, _ in ranked:
        display = best_display[key]
        if key in _TAG_FILLER_WORDS or len(key) < 3:
            continue
        keywords.append(display)
        if len(keywords) >= top_k:
            break
    return keywords



def _job_text(row, field_names=None):
    field_names = field_names or [
        "Job Title", "About the job", "Requirements",
        "Responsibilities", "Summary", "Benefits",
    ]
    fields = [
        row.get(field, "") for field in field_names
    ]
    # ". " (not a plain space) between fields so downstream sentence-boundary
    # splitting (RAKE, cue-phrase extraction) never fuses the tail of one
    # field with the head of the next (e.g. a job title running into the
    # first word of the description).
    return ". ".join(f.strip().rstrip(".") for f in fields if f and f.strip())


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def _tag_slug(value):
    """Normalize a readable skill/domain label into a resume-search tag."""
    value = one_line(str(value or "")).lower()
    replacements = {
        "c++": "cpp",
        "c#": "csharp",
        "ci/cd": "ci-cd",
        "node.js": "nodejs",
        "next.js": "nextjs",
        "vue.js": "vuejs",
        "rest api": "rest-api",
        "restful api": "rest-api",
        "pos": "pos",
        "point of sale": "pos",
        "self checkout": "self-checkout",
    }
    value = replacements.get(value, value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9+#.]+", "-", value)
    value = value.strip("-")
    value = value.replace(".", "")
    return value


def _append_tag(items, value):
    _append_unique(items, _tag_slug(value))


_EXPERIENCE_RE = re.compile(
    r"\b(\d{1,2})\s?\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+)?experience\b",
    re.I,
)

_SENIORITY_TAG_RULES = [
    ("senior", re.compile(r"\b(senior|sr\.?|staff|principal|lead)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]level)\b", re.I)),
    ("intern", re.compile(r"\bintern(ship)?\b", re.I)),
]

_AMBIGUOUS_SKILL_ALIASES = {
    "analytics", "bd", "building", "compliance", "federal", "growth",
    "inventory", "ip", "kpi", "law", "medical", "pm", "production", "pt",
    "qa", "qc", "retention", "sourcing",
}


def extract_tag_list(row, slug=False, max_tags=15, min_tags=7):
    """Thin wrapper kept for backward compat with callers that use extract_tag_list.
    Delegates to extract_resume_tags with an empty required_skills list so tags
    are built from the description only, then returns slugs if requested."""
    skills = extract_required_skills(_job_text(row))
    tags = extract_resume_tags(row, required_skills=skills, min_tags=min_tags, max_tags=max_tags)
    if not slug:
        return tags
    # tags are already slugged inside extract_resume_tags
    return tags


def extract_tags(row):
    return ", ".join(extract_tag_list(row, slug=True))


def _format_tags(tag_list):
    # tags already slugged; just join
    return ", ".join(tag_list)


def _format_skills(skills_list):
    """Return comma-separated canonical skill names, capped at 10."""
    return ", ".join(skills_list[:10])


def extract_skills(row):
    return _format_skills(extract_required_skills(_job_text(row)))


def detect_job_type(row):
    """Detect employment type from job text.
    Returns the first match from: Internship, Part-time, Contract, Remote, Full-time.
    Falls back to empty string if none detected.
    """
    fields = [
        row.get("Job Title", ""),
        row.get("About the job", ""),
        row.get("Requirements", ""),
        row.get("Summary", ""),
        row.get("Location", ""),
    ]
    combined = " ".join(f for f in fields if f)
    for label, pattern in _JOB_TYPE_PATTERNS:
        if pattern.search(combined):
            return label
    return ""


def extract_required_skills(text):
    """Finds hard technical/tool skills mentioned in `text` via the taxonomy.
    Returns canonical names, de-duplicated, capped at 10.
    These are concrete tools/technologies (Python, SQL, React, AWS, etc.) —
    kept short and precise. No RAKE padding, no seniority labels."""
    if not text:
        return []
    search_text = _taxonomy_search_key(text)
    found = set()
    for alias_key, canonical in _load_skills_taxonomy_aliases():
        if alias_key in search_text:
            found.add(canonical)
    return sorted(found)[:10]


EXPIRATION_DATE_RE = re.compile(
    r"\b(?:"
    r"closing\s+date|close\s+date|closes|closing|"
    r"apply\s+by|application\s+deadline|deadline|"
    r"expires?|expiration\s+date|valid\s+(?:through|until)"
    r")\b"
    r"(?:\s*(?:is|on|at|:|-|through|until|by))*\s*"
    r"("
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
    r")",
    re.IGNORECASE,
)

EXPIRATION_KEY_RE = re.compile(
    r"(close|closing|deadline|expires?|expiration|validthrough|valid_through|enddate|end_date)",
    re.IGNORECASE,
)


def extract_expiration_date(row):
    """Return a detected closing/expiration date for a row, if the source exposes one."""
    for field in (
        "Expires At", "Expiration Date", "Closing Date", "Application Deadline",
        "Valid Through", "validThrough", "expires_at", "expirationDate",
    ):
        parsed = parse_date(row.get(field))
        if parsed:
            return parsed

    combined = "\n".join(
        str(row.get(field, "") or "")
        for field in (
            "About the job", "Summary", "Requirements", "Responsibilities",
            "Benefits", "Portal Skills",
        )
    )
    for match in EXPIRATION_DATE_RE.finditer(combined):
        parsed = parse_date(match.group(1))
        if parsed:
            return parsed
    return None


def find_expiration_date_in_obj(value):
    """Best-effort extraction of a closing/expiry date from nested API payloads."""
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            if EXPIRATION_KEY_RE.search(key_text):
                parsed = parse_date(nested)
                if parsed:
                    return parsed
                if isinstance(nested, str):
                    fake_row = {"About the job": f"{key_text}: {nested}"}
                    parsed = extract_expiration_date(fake_row)
                    if parsed:
                        return parsed
            parsed = find_expiration_date_in_obj(nested)
            if parsed:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = find_expiration_date_in_obj(item)
            if parsed:
                return parsed
    return None


def is_expired_job(row, today=None):
    expires_on = extract_expiration_date(row)
    if not expires_on:
        return False
    today = today or datetime.now(timezone.utc).date()
    return expires_on < today


def looks_like_raw_json_description(text):
    text = clean_text(text or "")
    if not text:
        return False
    body = text
    if body.lower().startswith("about the role:"):
        body = body.split(":", 1)[1].strip()
    return bool(re.match(r"^[\[{]\s*\"(?:id|name|uuid|jobId|jobAdId)\"\s*:", body))


def output_payload_for_row(row):
    """Build the requested/admin CSV payload and enforce final output quality gates."""
    if is_expired_job(row):
        return None, "expired"
    payload = row_to_requested_format(row)
    if payload is None:
        return None, "missing_required_fields"
    if not payload.get("requiredSkills"):
        return None, "missing_required_skills"
    if looks_like_raw_json_description(payload.get("description", "")):
        return None, "raw_json_description"
    return payload, ""


# ---------------------------------------------------------------------------
# Resume-matching tag extraction
# ---------------------------------------------------------------------------

_SUPPLEMENT_SKIP_RE = re.compile(
    r"^(\d+[\+\-]?\s*(years?|yrs?|months?)|"
    r"\d+.*experience|"
    r"(full|part)[-\s]time|remote|hybrid|onsite|"
    r"(bachelor|master|phd|degree|diploma)|"
    r"(monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|jan|feb|mar|apr|may|jun|"
    r"jul|aug|sep|oct|nov|dec))$",
    re.I,
)

_GOOD_SKILL_CHAR_RE = re.compile(r"^[a-z0-9][a-z0-9+#.\-/ ]{1,45}$", re.I)


def _looks_like_skill(term: str) -> bool:
    """Return True when `term` is plausibly a concrete skill / domain term
    rather than a filler phrase, date, or employment-condition string."""
    t = term.strip()
    if not t or len(t) < 2 or len(t) > 50:
        return False
    if not _GOOD_SKILL_CHAR_RE.match(t):
        return False
    if _SUPPLEMENT_SKIP_RE.match(t):
        return False
    words = t.lower().split()
    if all(w in _TAG_FILLER_WORDS for w in words):
        return False
    return True


def extract_resume_tags(row, required_skills: list, min_tags: int = 7, max_tags: int = 15,
                        extra_text: str = "") -> list:
    """Extract resume-matching keyword tags purely from the job description text.

    Distinct from requiredSkills:
    - No hardcoded labels (no seniority, no employment-type strings)
    - Sourced only from the actual posting text via cue phrases + RAKE
    - Explicitly excludes anything already in required_skills (no overlap)
    - Slugified for consistent resume keyword matching
    - Min 7, max 15

    When a candidate uploads a resume these tags are the keyword bridge —
    prominent domain terms, methodologies, and role-specific language from
    the posting that a good-fit resume will also contain.

    extra_text: optional additional text to feed into tag extraction (e.g.
    the already-assembled full description, which may contain padded context
    not present in the raw row fields).
    """
    # Use the richer of: (a) the assembled description passed in via extra_text,
    # or (b) the standard _job_text view of the row. Combine both so we don't
    # lose any structured data from the raw row.
    base_text = _job_text(row)
    if extra_text and extra_text.strip():
        # Merge: prefer extra_text (already includes all sections) but append
        # any raw-field content not already in it.
        text = (extra_text.strip() + ". " + base_text).strip() if base_text else extra_text.strip()
    else:
        text = base_text
    if not text:
        return []

    # Exclusion set: slugs of required_skills so tags never duplicate them
    excluded = {_tag_slug(s).lower() for s in required_skills}
    excluded |= {s.lower() for s in required_skills}

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(term: str):
        if not _looks_like_skill(term):
            return
        slug = _tag_slug(term)
        if not slug or len(slug) < 2:
            return
        if slug in excluded or slug in seen:
            return
        seen.add(slug)
        candidates.append(slug)

    # 1. Cue-phrase objects: "experience with X", "knowledge of Y", etc.
    #    Highest signal — the posting explicitly names what it wants.
    for term in _extract_cue_phrase_terms(text):
        _add(term)

    # 2. RAKE keywords: statistically distinctive noun phrases from the text.
    #    Catches domain terms, methodologies, and tools that appear prominently
    #    in the description but not after a cue phrase.
    for term in _rake_keywords(text, top_k=40, max_phrase_words=3):
        _add(term)

    # 3. Enforce minimum tag count — pad with fallback signals when the above
    #    extraction didn't yield enough candidates (sparse postings, short
    #    descriptions, heavily boilerplate text).
    if len(candidates) < min_tags:
        # 3a. Widen the RAKE pass (longer phrases, more results) to catch
        #     anything we missed with conservative settings above.
        for term in _rake_keywords(text, top_k=60, max_phrase_words=5):
            if len(candidates) >= min_tags:
                break
            _add(term)

    if len(candidates) < min_tags:
        # 3b. Use individual meaningful words from the job title as fallback tags
        #     (e.g. "Software", "Engineer", "Data", "Analyst").
        #     Never adds required_skills — the no-overlap rule is always enforced.
        title = (row.get("Job Title") or "").strip()
        for word in re.split(r"\W+", title):
            if len(candidates) >= min_tags:
                break
            w = word.strip()
            if not w or len(w) < 3:
                continue
            if w.lower() in _TAG_FILLER_WORDS:
                continue
            slug = _tag_slug(w)
            if slug and len(slug) >= 2 and slug not in seen and slug not in excluded:
                seen.add(slug)
                candidates.append(slug)

    if len(candidates) < min_tags:
        # 3c. Seniority / employment-type signals — always relevant to resume
        #     matching even on the sparsest posting.
        fallback_signals = []
        combined = f"{row.get('Job Title', '')} {text}"
        for label, pattern in _SENIORITY_TAG_RULES:
            if pattern.search(combined):
                fallback_signals.append(label)
        for label, pattern in _JOB_TYPE_PATTERNS:
            if pattern.search(combined):
                fallback_signals.append(label.lower())
        for signal in fallback_signals:
            if len(candidates) >= min_tags:
                break
            slug = _tag_slug(signal)
            if slug and slug not in seen and slug not in excluded:
                seen.add(slug)
                candidates.append(slug)

    if len(candidates) < min_tags:
        # 3d. Last resort: company name slug — always a meaningful identifier
        #     and guaranteed not to duplicate any technical skill.
        company = (row.get("Company") or "").strip()
        if company:
            company_slug = _tag_slug(company)
            if company_slug and company_slug not in seen and company_slug not in excluded:
                seen.add(company_slug)
                candidates.append(company_slug)

    return candidates[:max_tags]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobResearchBot/1.0; "
                  "+https://example.com/bot-info)"
}
TIMEOUT = 15
CONNECT_TIMEOUT = float(os.environ.get("JOB_SCRAPER_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.environ.get("JOB_SCRAPER_READ_TIMEOUT", str(TIMEOUT)))
BODY_TIMEOUT = float(os.environ.get("JOB_SCRAPER_BODY_TIMEOUT", str(TIMEOUT * 2)))
MAX_RESPONSE_BYTES = int(os.environ.get("JOB_SCRAPER_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024)))
REQUEST_DELAY = 1.0  # be polite between requests
BLOCKED_BUT_NOT_DEAD_STATUSES = {401, 403, 407, 408, 425, 429, 500, 502, 503, 504}
DEFINITIVELY_DEAD_STATUSES = {404, 410, 451}

_SOURCE_LINK_ACTIVE_CACHE = {}
_DETAIL_FETCH_FAILED_HOSTS = set()

# Domains/patterns that are known tracking URLs or inactive job links
# These should be skipped as they don't lead to actual job postings
# Derived from linkcheck_09_12_final.tsv analysis:
#   - jobviewtrack.com (CareerJet tracking URLs return 429/too many requests)
#   - adzuna.com/land/ (Adzuna redirect URLs return 404)
#   - jooble.org/desc/ with tracking params (returns 403)
SKIP_URL_PATTERNS = [
    r"jobviewtrack",
    r"indeed\.com/viewjob.*(?:tracking|[?&]trk=)",
    r"linkedin\.com.*tracking",
    r"\?trk=",
    r"/track/",
    r"indeed\.qld",
    r"adzuna\.com/land/",
    r"jooble\.org/desc/.*[?&]ckey=",
]

SKIP_URL_RE = re.compile("|".join(SKIP_URL_PATTERNS), re.IGNORECASE)

# How much of a page to scan for a board's own "this posting is closed" marker.
# The status badge sits near the top of the page, so a bounded prefix is enough
# and keeps validation cheap across thousands of links.
POSTING_MARKER_SCAN_BYTES = int(
    os.environ.get("JOB_SCRAPER_POSTING_MARKER_SCAN_BYTES", str(256 * 1024))
)

# A job board renders its posting status in a `badge` element. USAJOBS is the
# clearest case: an open posting reads "Accepting applications" or "Reviewing
# applications", while a withdrawn one still returns HTTP 200 but reads
# "Job closed", "Job canceled" or "Hiring complete".
STATUS_BADGE_RE = re.compile(
    r'class="[^"]*\bbadge\b[^"]*"[^>]*>\s*([A-Za-z][A-Za-z &/-]{2,30}?)\s*<',
    re.IGNORECASE,
)

# Status-badge values (and matching inline wording) that mean the posting is no
# longer taking applications. Scoped deliberately tightly to status-like
# phrasing: loose patterns such as "not accepting applications" or "this job is
# filled" also occur in *live* postings (contact details saying applications
# are not accepted by mail, and the standard "you will be notified if this job
# is filled" boilerplate), so matching them would drop valid jobs.
TERMINAL_STATUS_RE = re.compile(
    r"\bjob (?:closed|cancell?ed|expired)\b"
    r"|\bhiring (?:complete|closed)\b"
    r"|\bposition (?:closed|has been filled)\b"
    r"|\bthis (?:job|posting) (?:is|has been) (?:closed|expired|filled)\b"
    r"|\bno longer (?:be )?(?:accepting|available|open)\b",
    re.IGNORECASE,
)

# Greenhouse serves a posting at /<board>/jobs/<id> and redirects a withdrawn
# posting back to the board's job index with ?error=true, keeping HTTP 200.
GREENHOUSE_HOST_RE = re.compile(
    r"^(?:www\.)?(?:boards|job-boards)\.greenhouse\.io$", re.IGNORECASE
)
GREENHOUSE_JOB_PATH_RE = re.compile(r"^/([^/]+)/jobs/(\d+)/?$", re.IGNORECASE)


def _status_badge_is_terminal(body):
    """True when a board's own status badge marks the posting as withdrawn.

    Needed because closed postings are served with HTTP 200 — only the rendered
    status reveals that no one can apply any more.
    """
    if not body:
        return False
    for match in STATUS_BADGE_RE.finditer(body):
        if TERMINAL_STATUS_RE.search(match.group(1)):
            return True
    return False


def _greenhouse_posting_missing(requested_url, final_url):
    """True when a Greenhouse job URL comes back without its posting.

    Greenhouse redirects a closed/removed posting to the board index with
    ?error=true, so both that flag and the loss of the /jobs/<id> segment mean
    the posting itself is gone even though the response is a 200.
    """
    match = GREENHOUSE_HOST_RE.match(urlsplit(requested_url or "").netloc)
    if not match:
        return False
    job_id = GREENHOUSE_JOB_PATH_RE.match(urlsplit(requested_url).path)
    final_url = final_url or ""
    if "error=true" in final_url.lower():
        return True
    if job_id and "/jobs/%s" % job_id.group(2) not in final_url.lower():
        return True
    return False


def is_source_link_active(url):
    """Check if a source link is active and not a tracking/broken URL.
    
    Returns True if the URL is valid and accessible, False otherwise.
    This helps avoid scraping inactive job postings or tracking URLs
    that don't lead to actual job listings.
    """
    if not SKIP_INACTIVE_URLS:
        return True
    
    if not url:
        return False
    
    url = url.strip()
    if not url:
        return False

    cached = _SOURCE_LINK_ACTIVE_CACHE.get(url)
    if cached is not None:
        return cached

    def remember(value):
        _SOURCE_LINK_ACTIVE_CACHE[url] = value
        return value
    
    # Skip known tracking/broken URL patterns
    if SKIP_URL_RE.search(url):
        return remember(False)
    
    # Check if URL has valid scheme
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return remember(False)

    def verdict_for(code, final_url, body=""):
        """Decide liveness from a response we already hold.

        URL shape is judged first (a link that lands on a login/error page or,
        on Greenhouse, back on the board index is not a live posting). The page
        body is only consulted for a success, where a 200 can still be a
        withdrawn posting. Blocked statuses stay "keep" — being refused is not
        proof a posting is dead.
        """
        if _greenhouse_posting_missing(url, final_url):
            return False
        if not _looks_like_live_page(final_url):
            return False
        if code < 400 and _status_badge_is_terminal(body):
            return False
        if code < 400 or code in BLOCKED_BUT_NOT_DEAD_STATUSES:
            return True
        return False

    def read_marker_prefix(resp):
        """Read a bounded prefix of the body to look for a closed-posting badge."""
        if not POSTING_MARKER_SCAN_BYTES:
            return ""
        try:
            buf = []
            for chunk in resp.iter_content(65536):
                buf.append(chunk)
                if sum(len(c) for c in buf) >= POSTING_MARKER_SCAN_BYTES:
                    break
            return b"".join(buf).decode("utf-8", "replace")
        except (requests.RequestException, UnicodeError):
            return ""
        finally:
            resp.close()

    # A single GET answers everything: status, the URL a redirect actually
    # lands on, and the page body a closed-posting marker is read from. Probing
    # with HEAD first used to be the cheap shortcut, but boards that serve
    # closed postings with HTTP 200 (USAJOBS) answer HEAD with 200 and an empty
    # body, so the marker could never be seen — and HEAD+GET costs two requests
    # to learn less. GET is streamed and the body is read only up to
    # POSTING_MARKER_SCAN_BYTES.
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=True,
            stream=True,
        )
        code = resp.status_code
        # Blocked/erroring responses are not read: being refused or rate
        # limited is not proof a posting is dead, so the status decides alone.
        body = read_marker_prefix(resp) if code < 400 else ""
        return remember(verdict_for(code, resp.url, body))
    except requests.RequestException:
        # Transport-level failure (DNS outage, timeout, connection reset,
        # "no route to host") means we could not reach the site — that is NOT
        # proof the job is dead. Only a definitive HTTP dead status (404/410/451),
        # a withdrawn-posting marker, or a URL that resolves to a non-posting
        # page should drop a posting, consistent with the "when in doubt, keep
        # the job" philosophy just above.
        return remember(True)


def prime_source_link_cache(urls, workers=8):
    """Warm _SOURCE_LINK_ACTIVE_CACHE for `urls` concurrently.

    Link liveness probes are network calls (up to a few seconds each). Doing
    them one at a time inside make_row makes a page of N postings take N x
    probe_time, which looks like a hang on big portals like USAJOBS. Probing a
    page's links up front with a small thread pool keeps the sequential
    make_row path (and its cache hit) unchanged.
    """
    if not SKIP_INACTIVE_URLS:
        return
    pending = []
    seen = set()
    for url in urls:
        if not url:
            continue
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if url not in _SOURCE_LINK_ACTIVE_CACHE:
            pending.append(url)
    if not pending:
        return
    if len(pending) == 1:
        is_source_link_active(pending[0])
        return
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(pending)))) as pool:
        list(pool.map(is_source_link_active, pending))


def _looks_like_live_page(final_url):
    """Heuristic: a final URL pointing at a login/error page is not a live job."""
    if not final_url:
        return True
    lower = final_url.lower()
    # USAJOBS job IDs are pure digits (e.g. .../job/885240400) and can contain
    # "404" as a substring while the posting is perfectly live. Judge only the
    # non-numeric part of the URL so numeric job IDs can't trigger a false
    # "dead page" verdict.
    parsed = urlparse(lower)
    segs = [s for s in parsed.path.split("/") if s]
    if segs and segs[-1].isdigit():
        segs = segs[:-1]
    body = f"{parsed.netloc}/{''.join(segs)}"
    if "login" in body or "signin" in body:
        return False
    if "error" in body or "not-found" in body or "404" in body:
        return False
    return True


def should_skip_url(url):
    """Check if a URL should be skipped (inactive, broken, or tracking).
    
    Returns True if the URL should be skipped, False otherwise.
    """
    return not is_source_link_active(url)

FIELDNAMES = [
    "Portal", "Job Title", "Company", "URL", "About the job", "Pay",
    "Location", "Summary", "Requirements", "Responsibilities", "Benefits",
    "Tags", "Job Type", "Portal Skills",
    "Scraped At",
]

BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "").strip()
BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "").strip()
BACKEND_API_BATCH_SIZE = int(os.environ.get("BACKEND_API_BATCH_SIZE", "100"))
BACKEND_API_TIMEOUT = int(os.environ.get("BACKEND_API_TIMEOUT", "30"))

# Admin job-creation API (e.g. https://admin.applyforme.us/api/jobs) - a different
# destination/shape than BACKEND_API_URL above: one job object per POST, Bearer auth,
# field names are company/role/platform/... rather than the batched {"jobs": [...]} shape.
ADMIN_API_URL = os.environ.get("ADMIN_API_URL", "").strip()
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "").strip()
ADMIN_API_TIMEOUT = int(os.environ.get("ADMIN_API_TIMEOUT", "30"))
# Local record of jobs already sent to the admin API, so re-running the scraper
# doesn't resend the same postings — the admin API itself has no known dedup behavior.
ADMIN_API_STATE_PATH = os.environ.get("ADMIN_API_STATE_PATH", "").strip() or os.path.join(
    os.path.dirname(__file__), "admin_sent_jobs.json"
)
# Cross-run CSV dedup: tracks every job (by URL and company::title) ever written
# to a CSV output so subsequent runs skip jobs already seen in a previous scrape.
# Delete or clear this file to reset the seen history.
SCRAPE_SEEN_PATH = os.environ.get("SCRAPE_SEEN_PATH", "").strip() or os.path.join(
    os.path.dirname(__file__), "scraped_seen.json"
)
# Descriptions are kept in full — the scraper never truncates a scraped job
# description, regardless of length, so the output carries the entire posting text.

LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemma-3-270m")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "512"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
ENABLE_LLM_REFINEMENT = os.environ.get("ENABLE_LLM_REFINEMENT", "").strip().lower() in {
    "1", "true", "yes", "on"
}
STRICT_VALIDATION = os.environ.get("STRICT_VALIDATION", "1").strip().lower() in {
    "1", "true", "yes", "on"
}
FETCH_FULL_DETAIL_DESCRIPTIONS = os.environ.get("FETCH_FULL_DETAIL_DESCRIPTIONS", "1").strip().lower() in {
    "1", "true", "yes", "on"
}
ENRICH_SPARSE_DESCRIPTIONS = os.environ.get("ENRICH_SPARSE_DESCRIPTIONS", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
ADD_DESCRIPTION_INTRO = os.environ.get("ADD_DESCRIPTION_INTRO", "0").strip().lower() in {
    "1", "true", "yes", "on"
}

# URL validation: skip inactive/broken/tracking source links
# Set SKIP_INACTIVE_URLS=0 to disable this validation
SKIP_INACTIVE_URLS = os.environ.get("SKIP_INACTIVE_URLS", "1").strip().lower() in {
    "1", "true", "yes", "on"
}

# --- USA-only location filter -----------------------------------------------
# Scrape jobs -> Extract Location -> USA location validation -> YES: save / NO: discard.
# On by default; set USA_ONLY_FILTER=0 to disable and keep jobs from any location.
USA_ONLY_FILTER = os.environ.get("USA_ONLY_FILTER", "1").strip().lower() in {
    "1", "true", "yes", "on"
}
# A Location string that gives no country signal at all ("Remote", blank, a
# bare city name) is ambiguous. By default ambiguous rows are discarded so the
# output only contains postings positively validated as USA-based.
USA_ONLY_TREAT_UNKNOWN_AS_USA = os.environ.get("USA_ONLY_TREAT_UNKNOWN_AS_USA", "0").strip().lower() in {
    "1", "true", "yes", "on"
}

_US_STATE_ABBR = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "GU", "VI", "AS", "MP",
}

_US_STATE_NAMES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    "Puerto Rico",
]

# "TX" / "CA" etc. only count when they read like a real state code (preceded
# by a comma, as in "Austin, TX") — otherwise 2-letter abbreviations like
# "OR", "IN", "HI", "ME", "OK" collide with ordinary English words.
_US_STATE_ABBR_RE = re.compile(
    r",\s*(" + "|".join(sorted(_US_STATE_ABBR)) + r")\b(?!\.\w)", re.I
)
_US_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in _US_STATE_NAMES) + r")\b", re.I
)
_USA_INDICATOR_RE = re.compile(
    r"\b(united states of america|united states|u\.s\.a\.?|usa|u\.s\.)\b", re.I
)
_REMOTE_US_RE = re.compile(
    r"\bremote\b.{0,20}\b(us|usa|u\.s\.?)\b|\b(us|usa|u\.s\.?)\b.{0,20}\bremote\b", re.I
)

# Common non-US countries / regions / demonyms that show up verbatim in a
# "Location" field when a posting is based outside the US. Not exhaustive —
# this only needs to catch what portals actually put in a Location string,
# not every place name on earth.
_FOREIGN_LOCATION_RE = re.compile(
    r"\b("
    r"worldwide|global|anywhere|europe|european union|e\.u\.?|emea|apac|latam|"
    r"united kingdom|u\.k\.?|england|scotland|wales|northern ireland|"
    r"canada|ontario|quebec|british columbia|alberta|manitoba|"
    r"mexico|india|bengaluru|bangalore|mumbai|hyderabad|delhi|pune|chennai|"
    r"gurgaon|gurugram|noida|kolkata|"
    r"pakistan|bangladesh|philippines|manila|vietnam|indonesia|malaysia|"
    r"singapore|thailand|china|beijing|shanghai|shenzhen|hong kong|taiwan|"
    r"japan|tokyo|south korea|seoul|"
    r"germany|berlin|munich|france|paris|spain|madrid|barcelona|italy|milan|"
    r"rome|netherlands|amsterdam|belgium|brussels|ireland|dublin|portugal|"
    r"lisbon|poland|warsaw|romania|bucharest|ukraine|kyiv|kiev|russia|moscow|"
    r"sweden|stockholm|norway|oslo|denmark|copenhagen|finland|helsinki|"
    r"switzerland|zurich|austria|vienna|greece|athens|turkey|istanbul|"
    r"israel|tel aviv|united arab emirates|u\.a\.e\.?|dubai|abu dhabi|"
    r"saudi arabia|riyadh|qatar|doha|egypt|cairo|nigeria|lagos|kenya|"
    r"nairobi|south africa|johannesburg|cape town|"
    r"australia|sydney|melbourne|brisbane|perth|new zealand|auckland|"
    r"brazil|sao paulo|argentina|buenos aires|chile|santiago|colombia|"
    r"bogota|peru|lima|costa rica|panama|jamaica|dominican republic"
    r")\b", re.I
)

_US_AUTHORIZATION_RE = re.compile(
    r"\b(authoriz(?:ed|ation) to work in the (?:u\.?s\.?|united states)"
    r"|must (?:be|reside) (?:based |located )?in the (?:u\.?s\.?|united states)"
    r"|(?:u\.?s\.?|united states) citizenship (?:required|preferred)"
    r"|based (?:anywhere )?in the (?:u\.?s\.?|united states))\b", re.I
)


def is_usa_location(location_text, job_text=""):
    """Classify a Location string as USA (True), explicitly not-USA (False),
    or unknown/ambiguous (None) — e.g. blank, a bare "Remote", or a city name
    with no state/country attached."""
    text = one_line(location_text or "").strip()
    if not text:
        # No location string at all — fall back to explicit USA-work-
        # authorization language elsewhere in the posting, if any.
        return True if job_text and _US_AUTHORIZATION_RE.search(job_text) else None

    if _FOREIGN_LOCATION_RE.search(text):
        return False
    if (
        _USA_INDICATOR_RE.search(text)
        or _US_STATE_ABBR_RE.search(text)
        or _US_STATE_NAME_RE.search(text)
        or _REMOTE_US_RE.search(text)
    ):
        return True
    if job_text and _US_AUTHORIZATION_RE.search(job_text):
        return True
    return None


def is_usa_job(row):
    """Applies the USA-only policy:
    Scrape jobs -> Extract Location -> USA validation -> save only YES rows.
    Explicit non-USA locations and ambiguous/unknown locations are discarded by
    default. Set USA_ONLY_TREAT_UNKNOWN_AS_USA=1 to keep ambiguous rows."""
    if not USA_ONLY_FILTER:
        return True
    job_text = _job_text(row, ["About the job", "Requirements", "Summary"])
    verdict = is_usa_location(row.get("Location", ""), job_text)
    if verdict is None:
        return USA_ONLY_TREAT_UNKNOWN_AS_USA
    return verdict

SPANISH_INDICATOR_RE = re.compile(
    r"\b(el|la|los|las|un|una|unos|unas|por|para|que|como|entre|sobre|más|sí|trabajo|empresa|equipo|descripción|responsabilidades|requisitos|beneficios)\b|[áéíóúñ¿¡]",
    re.IGNORECASE,
)

_llm_client = None

SECTION_HEADERS = {
    "Requirements": [
        "requirements", "qualifications", "what you'll need", "what you need",
        "who you are", "must have", "skills required", "minimum qualifications",
    ],
    "Responsibilities": [
        "responsibilities", "what you'll do", "what you will do", "duties",
        "the role", "key responsibilities", "day to day", "role overview",
    ],
    "Benefits": [
        "benefits", "perks", "what we offer", "why join us", "compensation and benefits",
        "why you'll love working here",
    ],
    "Summary": [
        "summary", "about the role", "about this role", "overview", "job summary",
        "about the job", "about the position",
    ],
}

PAY_RE = re.compile(
    r"(\$\s?\d[\d,]*(\.\d+)?\s?(-|to|–)\s?\$?\s?\d[\d,]*(\.\d+)?\s?"
    r"(k|/hr|/hour|/yr|/year|per hour|per year)?)|"
    r"(\$\s?\d[\d,]*(\.\d+)?\s?(k|/hr|/hour|/yr|/year|per hour|per year))",
    re.IGNORECASE,
)

LOCATION_HINTS = re.compile(
    r"(remote|hybrid|on[- ]?site|onsite)\b|,\s?[A-Z]{2}\b|"
    r"\b(USA|United States|New York|San Francisco|Austin|Chicago|Seattle|Boston)\b"
)

JOB_SIGNAL_RE = re.compile(
    r"\b(job|role|position|opening|hire|hiring|apply|candidate|team|experience|"
    r"responsibilit|qualifications|requirements|salary|benefits|manager|engineer|"
    r"developer|designer|analyst|specialist|director|coordinator|assistant)\b",
    re.IGNORECASE,
)

NON_JOB_SIGNAL_RE = re.compile(
    r"(https?://t\.co/|#(?!LI\b)\w+|\bCA:\s*[A-Za-z0-9]{20,}|\bATH MC\b|\bLIQ\b|"
    r"\bHolders:\b|\bSnipers:\b|\bBot Degens\b|\bCTO RADAR\b|\bpump\b|"
    r"\bmemecoin\b|\btoken\b|\bchart\s*-\s*signal\b)",
    re.IGNORECASE,
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()





# Characters that show up when UTF-8 bytes get mis-decoded as Latin-1/
# Windows-1252 ("mojibake"), e.g. an em-dash (E2 80 94) turning into "â€"".
# We try to repair this automatically rather than write garbage to the CSV.


# Typical "marker" characters that show up when UTF-8 bytes get displayed as
# if they were Latin-1/Windows-1252 - e.g. "Ã¦", "â€"", "Ã°". Rather than
# keeping a curated list of specific broken sequences (which missed non-Latin
# languages like Icelandic/Vietnamese), we score how much of this marker
# "noise" a string contains and only accept a repair pass if it strictly
# reduces that noise.
_MOJIBAKE_MARKER_RE = re.compile(r"[\u00c2\u00c3\u00e2\u0080-\u009f]")


def _mojibake_score(text):
    return len(_MOJIBAKE_MARKER_RE.findall(text))


def fix_mojibake(text):
    if not text:
        return text
    # some feeds double-corrupt text (UTF-8 -> Latin-1 -> UTF-8 -> Latin-1),
    # so try up to two repair passes, only keeping each pass if it actually
    # reduces the amount of mojibake-marker noise in the string
    for _ in range(2):
        score_before = _mojibake_score(text)
        if score_before == 0:
            break
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired.count("\ufffd") > 0:
            break
        if _mojibake_score(repaired) < score_before:
            text = repaired
        else:
            break
    return text


# Boilerplate injected by some boards (RemoteOK in particular) to catch bots
# ("Please mention the word X when applying..."). It's not real job content
# and just adds noise, so we strip it out of descriptions.
_SPAM_PATTERNS = [
    re.compile(r"Please mention the word.*?\)\.?", re.IGNORECASE | re.DOTALL),
    re.compile(r"This is a beta feature to avoid spam applicants\.[^\n]*", re.IGNORECASE),
]


def strip_boilerplate(text):
    for pattern in _SPAM_PATTERNS:
        text = pattern.sub("", text)
    return text


def clean_text(html_or_text):
    if not html_or_text:
        return ""
    if "<" in html_or_text and ">" in html_or_text:
        soup = BeautifulSoup(html_or_text, "html.parser")
        text = soup.get_text("\n")
    else:
        text = html_or_text
    text = html_unescape(text)
    text = fix_mojibake(text)
    text = strip_boilerplate(text)
    # normalize unicode (curly quotes, NBSPs, etc.) to consistent forms
    text = unicodedata.normalize("NFKC", text)
    # strip control/zero-width characters that corrupt CSV/Excel rendering
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def parse_sections(full_text, title="", location_hint=""):
    """Heuristically split a raw job-description text into the requested
    fields using keyword-header matching. Falls back to putting everything
    into 'About the job' / 'Summary' when no headers are recognized."""
    text = clean_text(full_text)
    lines = text.split("\n")

    # Build a map of line_index -> matched section name for lines that look
    # like headers (short line, matches a known keyword).
    header_positions = []
    for i, line in enumerate(lines):
        stripped = line.strip().strip(":").lower()
        if not stripped or len(stripped) > 60:
            continue
        for section, keywords in SECTION_HEADERS.items():
            if any(stripped == kw or stripped.startswith(kw) for kw in keywords):
                header_positions.append((i, section))
                break

    sections = {"Summary": "", "Requirements": "", "Responsibilities": "", "Benefits": ""}
    if header_positions:
        header_positions.append((len(lines), None))
        for idx in range(len(header_positions) - 1):
            start_i, section = header_positions[idx]
            end_i, _ = header_positions[idx + 1]
            chunk = "\n".join(lines[start_i + 1:end_i]).strip()
            if section:
                sections[section] = (sections[section] + "\n" + chunk).strip()
        about = "\n".join(lines[:header_positions[0][0]]).strip()
    else:
        about = text

    if not sections["Summary"]:
        sections["Summary"] = about
    if not about:
        about = sections["Summary"]

    pay_match = PAY_RE.search(text)
    pay = pay_match.group(0).strip() if pay_match else ""
    pay = normalize_pay(pay)

    location = location_hint
    if not location:
        loc_match = LOCATION_HINTS.search(text)
        location = loc_match.group(0).strip() if loc_match else ""

    return {
        "About the job": about,
        "Pay": pay,
        "Location": location,
        "Summary": sections["Summary"],
        "Requirements": sections["Requirements"],
        "Responsibilities": sections["Responsibilities"],
        "Benefits": sections["Benefits"],
    }


def one_line(text):
    """Clean + collapse to a single line, for fields like Title/Company/URL
    that should never contain embedded newlines in the CSV."""
    if not text:
        return ""
    text = html_unescape(text)
    text = fix_mojibake(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\n", " ").replace("\t", " ").replace("\r", " ")
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", text).strip()


# Bullet separators show up on Greenhouse boards ("San Francisco, CA • New York,
# NY • United States"). They are treated like ';' so a bullet list is reduced to
# its first place like every other multi-location shape.
LOCATION_MULTI_SPLIT = re.compile(
    r"\s*(?:;|(?:\s\|\s)|\s/\s|[\u2022\u2023\u2027\u2219\u00b7])\s*"
)

# Some sources (Mercury) list several offices with commas and the word "or"
# instead of ';', e.g.
#     "San Francisco, CA, New York, NY, Portland, OR, or Remote within United States"
#     "New York, NY or San Francisco, CA"
#     "San Francisco or New York"
# A bare " or " only counts as a separator when it sits between two non-empty
# chunks, so a title-like phrase such as "Order or Billing" is not mangled
# unless it is genuinely listing alternatives. The comma form is handled by
# LOCATION_COMMA_LIST_SPLIT below, which needs a "City, ST" shape to avoid
# cutting a legitimate "City, State" cell in half.
LOCATION_OR_SPLIT = re.compile(r"\s+or\s+", re.IGNORECASE)

# A "City, ST" chunk. The state part is either a 2-letter code or a spelled-out
# name, and a chunk may carry a trailing qualifier such as "(HQ)".
_CITY_ST = r"(?:[A-Z]{2}\b|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
_CITY_ST_CHUNK = (
    r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)*"   # city
    r",\s*" + _CITY_ST +                            # state
    r"(?:\s*\([A-Za-z][A-Za-z\s]*\))?"               # optional "(HQ)"
)
# Placeholders a source may put in front of the real location.
#
# "Remote" is deliberately NOT in the plain set: there are real places named
# Remote (Remote, IL and Remote, TX both appear in this corpus), so a bare
# "Remote, IL" is a location and must survive. Only a qualified "Remote (US)",
# "Remote, US" or "Remote, United States" is treated as a placeholder, since a
# country code cannot follow a city/state pair.
_LOCATION_LEADIN_PLAIN = r"Multiple\s+Locations?|US[-\s]?Based|Anywhere|Nationwide"
_LOCATION_LEADIN_REMOTE = (
    r"Remote(?:\s*\([A-Za-z.]+\)|\s*,\s*(?:US|USA|United States)\b)"
)
_LOCATION_LEADIN = (
    r"(?:" + _LOCATION_LEADIN_PLAIN + r"|" + _LOCATION_LEADIN_REMOTE + r")"
)

# Split on a comma ONLY when the WHOLE cell is a run of two or more complete
# "City, ST" chunks ("Austin, TX, Southlake, TX"). Matching the entire cell with
# fullmatch() instead of using a split lookahead is what keeps single locations
# intact: a lookahead would read "CA, New York" inside
# "San Francisco, CA, New York, NY" as a city/state pair and cut the cell down to
# "San Francisco". Requiring the full cell to be a list also means
# "Toronto, ON, CA" and "Palo Alto, CA, US" — one real pair plus a country code —
# never match, so they are left alone.
LOCATION_COMMA_LIST_RE = re.compile(
    _CITY_ST_CHUNK + r"(?:\s*,\s*" + _CITY_ST_CHUNK + r")+"
    r"(?:\s*,?\s*(?:or\s+)?[A-Za-z][A-Za-z\s().,-]*)?"
)
# Splits an already-validated list on the commas BETWEEN "City, ST" chunks,
# leaving the intra-chunk comma (city vs state) intact.
LOCATION_CITY_CHUNK_RE = re.compile(_CITY_ST_CHUNK)

# Same idea, but the first chunk is a placeholder and only one real city follows
# ("Remote (US), San Francisco, CA").
LOCATION_LEADIN_SPLIT = re.compile(
    r"^(?:" + _LOCATION_LEADIN + r")\s*,\s*(?=" + _CITY_ST_CHUNK + r")",
    re.IGNORECASE,
)

# A few boards lead with the work arrangement and list the cities after a dash:
#     "Hybrid - San Francisco, New York City, London"
#     "Remote - San Francisco, New York"
# The dash is the mirror image of the usual "City, ST - Hybrid" shape, so the
# comma-list rules below never see the cities. Only a cell that STARTS with an
# arrangement word matches, which leaves "Denver, CO - Hybrid" untouched.
LOCATION_ARRANGEMENT_PREFIX = re.compile(
    r"^(?P<mode>Remote|Hybrid|On-?site|On-?office|In-?office|Office)\s+-\s+(?P<rest>\S.*)$",
    re.IGNORECASE,
)

# A place name with no state part ("San Francisco", "New York City", "London").
LOCATION_PLAIN_CITY_RE = re.compile(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)*")

# Trailing chunk that is a state/country code rather than a second city. Used so
# "Hybrid - Washington, DC" and "Remote - Boston, MA" stay whole while
# "Hybrid - Boston, New York" is read as two cities.
LOCATION_REGION_CODE_RE = re.compile(
    r"(?:"
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO"
    r"|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
    r"|DC|PR|US|USA|U\.S\.?|United States|United Kingdom|EMEA|APAC|LATAM"
    r")\.?$",
    re.IGNORECASE,
)


def _split_location_alternatives(text):
    """Split a location cell on ' or ' and on comma-separated city lists.

    Only fires when the cell really does list several places; a plain
    "City, State" or "City, State, Country" cell is returned untouched.
    """
    if not text:
        return text

    # Arrangement first: "Hybrid - San Francisco, New York City, London". The
    # cities sit after a leading mode word, so they are comma lists of bare place
    # names rather than "City, ST" chunks. The mode is kept on the surviving city
    # ("San Francisco - Hybrid") to match the "City, ST - Hybrid" shape other
    # boards use.
    m = LOCATION_ARRANGEMENT_PREFIX.match(text)
    if m:
        mode = m.group("mode").strip()
        rest = m.group("rest").strip()
        chunks = [c.group(0).strip() for c in LOCATION_PLAIN_CITY_RE.finditer(rest)]
        # The chunk list only describes the whole tail when re-joining it yields
        # the tail itself; anything else means the commas belong inside a place
        # name ("Washington, DC", "Toronto, Ontario Canada") and the cell stays.
        covers_rest = "".join(chunks).replace(" ", "") == rest.replace(",", "").replace(" ", "")
        if len(chunks) > 1 and covers_rest:
            last_is_region = bool(LOCATION_REGION_CODE_RE.match(chunks[-1]))
            if len(chunks) > 2 or not last_is_region:
                return [f"{chunks[0]} - {mode}"]

    # Placeholder first: "Remote (US), San Francisco, CA". The lookahead is what
    # makes this safe: "Multiple Locations, USA" also starts with a placeholder,
    # but nothing city-like follows the comma, so it is left intact.
    if "," in text:
        m = LOCATION_LEADIN_SPLIT.match(text)
        if m:
            stripped = text[m.end():].strip().strip(",").strip()
            if stripped:
                return [stripped]

    # Comma list of two or more "City, ST" chunks. Only when the ENTIRE cell
    # matches that shape, so "Toronto, ON, CA" stays whole.
    if "," in text:
        if LOCATION_COMMA_LIST_RE.fullmatch(text.strip()):
            # Re-scan the validated cell for the chunk boundaries. Splitting by
            # looking ahead at the next chunk cannot work here: every chunk
            # starts with "City, ", so the comma that ends one chunk is
            # indistinguishable from the comma inside it. finditer walks the
            # chunks left to right instead, and each match's span tells us where
            # this chunk ends.
            chunks = [m.group(0).strip() for m in LOCATION_CITY_CHUNK_RE.finditer(text)]
            if len(chunks) > 1:
                return chunks

    # Alternatives: "New York, NY or San Francisco, CA" / "San Francisco or New York"
    if re.search(r"\s+or\s+", text, re.IGNORECASE):
        parts = [p.strip() for p in LOCATION_OR_SPLIT.split(text) if p.strip()]
        if len(parts) > 1:
            return parts

    return text

def dedupe_repeated_location(loc):
    """Collapse location cells that repeat the same place in one row, e.g.
        "Mount Prospect, IL, us, Mount Prospect, IL, United States"
        "Casablanca, ma, Casablanca, , Morocco"
    These come from sources that concatenate a short country-code form with a
    full country-name form of the same place. Valid "City, State" cells like
    "New York, New York" are left untouched.
    """
    s = (loc or "").strip()
    if not s:
        return loc

    def _collapse(pattern, country_name):
        m = re.fullmatch(pattern, s, re.IGNORECASE)
        if not m:
            return None
        g = m.groupdict()
        if g.get("city1", "").strip().lower() != g.get("city2", "").strip().lower():
            return None
        if "state1" in g and "state2" in g:
            if g["state1"].strip().lower() != g["state2"].strip().lower():
                return None
            state = g["state1"].strip()
            if re.fullmatch(r"[A-Z]{2}", state):
                state = state.upper()
            elif state.isupper():
                state = state.title()
            return "%s, %s, %s" % (g["city1"].strip(), state, country_name)
        return "%s, %s" % (g["city1"].strip(), g["country"].strip())

    # City, ST, us/usa, City, ST, United States
    fixed = _collapse(
        r"^\s*(?P<city1>.+?)\s*,\s*(?P<state1>[^,]+?)\s*,\s*(?:us|usa)\s*,"
        r"\s*(?P<city2>.+?)\s*,\s*(?P<state2>[^,]+?)\s*,\s*(?:United States)\s*$",
        "United States",
    )
    if fixed:
        return fixed
    # City, cc, City, , Country
    fixed = _collapse(
        r"^\s*(?P<city1>.+?)\s*,\s*[a-z]{2}\s*,\s*(?P<city2>.+?)\s*,\s*,\s*(?P<country>.+?)\s*$",
        None,
    )
    if fixed:
        return fixed
    return loc


def normalize_location(text):
    """Clean a location field and reduce it to a SINGLE location.

    Many sources (Greenhouse, USAJOBS, Lever, Mercury) report several offices for
    one posting as one string, e.g.
        "Bellevue, Washington; Chicago, Illinois; New York, New York"
        "Atlanta, GA - Remote; Denver, CO - Hybrid; Phoenix, AZ - Remote"
        "San Francisco, CA, New York, NY, Portland, OR, or Remote within US"
        "New York, NY or San Francisco, CA"
    Separators are ';', ' | ', ' / ', a comma-separated list of "City, ST"
    chunks, and the word "or". We keep only the first listed location so every
    row ends up with exactly one value in the location cell instead of several
    values jammed into one field.
    """
    text = one_line(text)
    parts = LOCATION_MULTI_SPLIT.split(text)
    parts = [p.strip().strip(" ,") for p in parts if p and p.strip().strip(" ,")]
    if not parts:
        return ""
    # Collapse a repeated place ("Mount Prospect, IL, us, Mount Prospect, IL,
    # United States") BEFORE splitting on commas, otherwise the split would cut
    # that cell at "us" and lose the country.
    single = dedupe_repeated_location(parts[0])
    single = _split_location_alternatives(single)
    if isinstance(single, list):
        single = single[0] if single else ""
    return single.strip().strip(" ,")



def normalize_row_row_values(row):
    return {field: (row.get(field, "") or "") for field in FIELDNAMES}


def _truncate_for_llm(text, max_chars=1200):
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0] + "..."


def extract_json_object(text):
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def is_text_non_english(text):
    if not text:
        return False
    return bool(SPANISH_INDICATOR_RE.search(text))


def get_model_candidates(model_name):
    candidates = [model_name]
    if ":" in model_name:
        base, variant = model_name.split(":", 1)
        candidates.extend([
            model_name.replace(":", "-"),
            f"{base}/{variant}",
            f"{base}/{base}-{variant}",
            f"{base}/{base}_{variant}",
            f"{base}-{variant}",
            f"{base}/{variant}".lower(),
            f"{base}-{variant}".lower(),
        ])
    if "/" in model_name:
        candidates.extend([
            model_name.replace("/", "-"),
            model_name.replace("/", "_"),
        ])
    if "-" in model_name:
        candidates.extend([
            model_name.replace("-", "/"),
            model_name.replace("-", "_"),
        ])
    unique = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique



def validate_hf_model(model_name):
    if not HUGGINGFACE_HUB_AVAILABLE or not HF_TOKEN:
        return True
    try:
        api = HfApi()
        api.model_info(model_name, token=HF_TOKEN)
        return True
    except Exception:
        return False


def get_llm_client():
    global _llm_client
    if not ENABLE_LLM_REFINEMENT:
        _llm_client = False
        return None
    if _llm_client is False:
        return None
    if _llm_client is not None:
        return _llm_client
    if not TRANSFORMERS_AVAILABLE:
        _llm_client = False
        return None

    hf_kwargs = {}
    if HF_TOKEN:
        hf_kwargs = {"token": HF_TOKEN, "use_auth_token": HF_TOKEN}
    candidates = get_model_candidates(LLM_MODEL)
    last_error = None
    for candidate in candidates:
        if HUGGINGFACE_HUB_AVAILABLE and HF_TOKEN and not validate_hf_model(candidate):
            continue
        if candidate != LLM_MODEL:
            print(f"  [info] Trying LLM model candidate {candidate!r}", file=sys.stderr)
        try:
            tokenizer = AutoTokenizer.from_pretrained(candidate, trust_remote_code=True, **hf_kwargs)
            model = AutoModelForCausalLM.from_pretrained(candidate, trust_remote_code=True, **hf_kwargs)
            _llm_client = pipeline(
                "text-generation",
                model=model,
                tokenizer=tokenizer,
                device_map="auto",
                trust_remote_code=True,
                **hf_kwargs,
            )
            return _llm_client
        except Exception as exc:
            last_error = exc
            continue

    token_note = " with Hugging Face token" if HF_TOKEN else " (no Hugging Face token provided)"
    warning_text = (
        f"LLM model {LLM_MODEL!r} could not be loaded{token_note}. "
        f"Tried candidate names: {', '.join(candidates)}. "
        "Make sure the model name is a valid Hugging Face repo identifier or a local path."
    )
    if last_error:
        warning_text += f" Last error: {last_error}"
    print(f"  [warn] {warning_text}", file=sys.stderr)
    _llm_client = False
    return None


def generate_llm_response(prompt, max_new_tokens=LLM_MAX_TOKENS):
    client = get_llm_client()
    if client is None:
        return None
    try:
        responses = client(prompt, max_new_tokens=max_new_tokens, temperature=LLM_TEMPERATURE)
        if not responses:
            return None
        return responses[0]["generated_text"]
    except Exception as exc:
        print(f"  [warn] LLM generation failed: {exc}", file=sys.stderr)
        return None


def llm_is_real_job_post(row):
    if not get_llm_client():
        return None
    prompt = (
        "You are validating scraped job data. Decide whether this is a real job posting "
        "and not spam, a social post, a meme coin promo, a navigation page, or generic site copy. "
        "Return only JSON like {\"is_real_job\": true, \"reason\": \"short reason\"}.\n\n"
        "Row:\n"
        + json.dumps(
            {
                "Portal": row.get("Portal", ""),
                "Job Title": _truncate_for_llm(row.get("Job Title", ""), 200),
                "Company": _truncate_for_llm(row.get("Company", ""), 200),
                "Location": _truncate_for_llm(row.get("Location", ""), 200),
                "About the job": _truncate_for_llm(row.get("About the job", ""), 1000),
                "Summary": _truncate_for_llm(row.get("Summary", ""), 500),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    response = generate_llm_response(prompt, max_new_tokens=120)
    if not response:
        return None
    json_text = extract_json_object(response)
    if not json_text:
        return None
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    verdict = parsed.get("is_real_job")
    if isinstance(verdict, bool):
        return verdict
    if isinstance(verdict, str):
        verdict = verdict.strip().lower()
        if verdict in {"true", "yes", "job", "real"}:
            return True
        if verdict in {"false", "no", "non-job", "not job"}:
            return False
    return None


def refine_row_with_llm(row):
    if not get_llm_client():
        return row
    combined = "\n\n".join(
        _truncate_for_llm(row.get(field, "")) for field in [
            "Job Title", "Company", "About the job", "Summary",
            "Requirements", "Responsibilities", "Benefits",
        ]
    )
    if not is_text_non_english(combined):
        return row

    prompt = (
        "Translate and normalize this job posting into clean English. "
        "Preserve all meaning and keep the output as valid JSON with the keys: "
        "Portal, Job Title, Company, URL, About the job, Pay, Location, Summary, "
        "Requirements, Responsibilities, Benefits. "
        "Output only the JSON object without any extra commentary. "
        "If a field is already English, keep it unchanged. "
        "If a field is empty, return an empty string for that field.\n\n"
        "Current values:\n"
        + json.dumps(
            {
                field: _truncate_for_llm(row.get(field, ""))
                for field in FIELDNAMES
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    response = generate_llm_response(prompt)
    if not response:
        return row
    json_text = extract_json_object(response)
    if not json_text:
        return row
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        return row
    updated = row.copy()
    for field in FIELDNAMES:
        if field in parsed and parsed[field] is not None:
            updated[field] = clean_text(str(parsed[field])) if field not in ["Portal", "URL", "Scraped At"] else one_line(str(parsed[field]))
    if "Location" in updated:
        updated["Location"] = normalize_location(updated.get("Location", ""))
    return updated


def is_probable_job_post(title, company, text):
    combined = " ".join(part for part in [title, company, text] if part).strip()
    if not combined:
        return False
    if NON_JOB_SIGNAL_RE.search(combined):
        return False
    return bool(JOB_SIGNAL_RE.search(combined) or len(clean_text(text)) >= 200)


def is_suspicious_row(row):
    title = one_line(row.get("Job Title", "")).strip()
    company = one_line(row.get("Company", "")).strip()
    about = clean_text(row.get("About the job", ""))
    summary = clean_text(row.get("Summary", ""))
    combined = "\n".join(part for part in [about, summary] if part)

    if is_junk_title(title, row.get("Portal", "")):
        return True
    if NON_JOB_SIGNAL_RE.search(" ".join([title, company, combined])):
        return True
    if len(title.split()) <= 1 and len(combined) < 250:
        return True
    if not JOB_SIGNAL_RE.search(" ".join([title, combined])) and len(combined) < 350:
        return True
    return False


def should_keep_row(row):
    title = row.get("Job Title", "")
    portal = row.get("Portal", "")
    combined = "\n".join(
        row.get(field, "") for field in ["About the job", "Summary", "Requirements", "Responsibilities"]
    )
    if is_junk_title(title, portal):
        return False
    if not is_probable_job_post(title, row.get("Company", ""), combined):
        if not ENABLE_LLM_REFINEMENT:
            return not STRICT_VALIDATION
        verdict = llm_is_real_job_post(row)
        return (not STRICT_VALIDATION) if verdict is None else verdict
    if is_suspicious_row(row):
        if not ENABLE_LLM_REFINEMENT:
            return not STRICT_VALIDATION
        verdict = llm_is_real_job_post(row)
        return (not STRICT_VALIDATION) if verdict is None else verdict
    return True


# ---------------------------------------------------------------------------
# Technical-job classification
#
# Used to keep/prioritize technical (software / IT / data / engineering) job
# postings while scraping. A posting is "technical" when its text matches one
# of the curated software/IT taxonomy categories (Python, Docker, AWS, SQL,
# Machine Learning, etc.) OR its role title carries a technical keyword
# (Engineer, Developer, Software, DevOps, Data Scientist, etc.). Notably
# excluded categories that merely appear in the same taxonomy — Healthcare,
# Nursing, Marketing, Sales, Finance, Legal, Technical Support, Writing,
# Project Management, Agile, etc. — are NOT treated as technical.
# ---------------------------------------------------------------------------

# Taxonomy categories (canonical names in data/skills_taxonomy.json) that count
# as genuinely technical/software-engineering skills.
_TECH_CATEGORIES = {
    "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "Go", "Ruby",
    "PHP", "SQL", "React", "Next.js", "Vue.js", "Angular", "Node.js",
    "FastAPI", "Django", "Flask", "Spring Boot", "PostgreSQL", "MySQL",
    "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "Azure", "GCP",
    "Terraform", "CI/CD", "Git", "REST API", "GraphQL", "Machine Learning",
    "Deep Learning", "TensorFlow", "PyTorch", "Data Analysis", "Data Engineering",
    "Pandas", "NumPy", "Spark", "Kafka", "Airflow", "Tableau", "Power BI",
    "Linux", "Bash", "HTML", "CSS", "Swift", "Kotlin", "Android", "iOS",
    "Microservices", "System Design", "Cybersecurity", "IT Infrastructure",
    "DevOps", "Engineering", "Software", "Data Science",
}

_TECH_TITLE_RE = re.compile(
    r"\b(engineer(ing)?|developer|software|devops|site reliability|sre|"
    r"data (?:scientist|engineer|analyst)|machine learning|ml engineer|"
    r"backend|frontend|full[- ]stack|fullstack|embedded|firmware|"
    r"systems? (?:engineer|admin)|it (?:support|admin|engineer)|sysadmin|"
    r"network engineer|cyber|security (?:engineer|analyst)|devops engineer|"
    r"cloud (?:engineer|architect)|platform engineer|qa (?:engineer|automation|test)|"
    r"test(?:ing)? engineer|automation engineer|data engineer|data scientist|"
    r"database administrator|dba|android|ios|mobile developer|python developer|"
    r"web developer|software engineer|programmer|coder|backend developer|"
    r"frontend developer|fullstack developer|site reliability engineer|"
    r"infrastructure|sre|devops|engineering)\b",
    re.IGNORECASE,
)


def _technical_skills_in(text):
    """Return the technical (software/IT) taxonomy categories found in `text`."""
    if not text:
        return []
    search_text = _taxonomy_search_key(text)
    found = set()
    for alias_key, canonical in _load_skills_taxonomy_aliases():
        if canonical in _TECH_CATEGORIES and alias_key in search_text:
            found.add(canonical)
    return sorted(found)


def is_technical_job(row):
    """Return True when `row` looks like a technical (software/IT/engineering) job.

    Uses either a matched software/IT taxonomy skill in the posting text or a
    technical role keyword in the job title. Works on both the raw scraped row
    shape (Job Title / About the job / ...) and the admin shape (role / description).
    """
    title = row.get("Job Title") or row.get("role") or ""
    fields = row.get("About the job") or row.get("description") or ""
    combined = " ".join(str(p) for p in [title, fields] if p)
    if _technical_skills_in(combined):
        return True
    return bool(title and _TECH_TITLE_RE.search(str(title)))


def make_row(portal, title, company, url, raw_text, location_hint="", fetch_detail=True):
    # Skip jobs with inactive/broken/tracking source links
    if url and not is_source_link_active(url):
        print(f"  [skip] {portal}: skipping inactive/broken source link: {url[:100]}...", file=sys.stderr)
        return None

    # Try the detail page before section parsing when the source needs it; API
    # scrapers can opt out when their payload already carries enough detail.
    if fetch_detail and url:
        raw_text = fetch_full_description_from_url(url, raw_text, portal)
    
    parsed = parse_sections(raw_text, title, location_hint)
    row = {
        "Portal": one_line(portal),
        "Job Title": one_line(title),
        "Company": one_line(company),
        "URL": one_line(url),
        "Scraped At": now_iso(),
    }
    row.update(parsed)
    row["Pay"] = normalize_pay(row.get("Pay", ""))
    row["Location"] = normalize_location(row.get("Location", ""))
    # Scrape jobs -> Extract Location -> USA location validation ->
    # YES: keep going / NO: discard. Runs before the (more expensive) LLM
    # refinement and validation steps below.
    if not is_usa_job(row):
        return None
    row = refine_row_with_llm(row)
    if row and should_keep_row(row):
        row["Pay"] = normalize_pay(row.get("Pay", ""))
        row["Tags"] = extract_tags(row)
        row["Job Type"] = detect_job_type(row)
        return row
    return None


def set_portal_skills(row, tags_value):
    """Record the skills/tags a portal explicitly provides for a posting.

    Only portal-supplied structured lists are stored (e.g. Arbeitnow's
    `tags`, Remotive's `tags`, Findwork's `keywords`, Himalayas' `skillTags`).
    These are kept separate from the description so requiredSkills can prefer
    the portal's own words, falling back to the posting text otherwise.
    """
    names = []
    if isinstance(tags_value, list):
        names = [str(t) for t in tags_value if t]
    elif isinstance(tags_value, str):
        names = [t.strip() for t in tags_value.replace("|", ",").split(",") if t.strip()]
    elif isinstance(tags_value, (dict,)):
        names = [str(v) for v in tags_value.values() if v]
    if names:
        row["Portal Skills"] = ", ".join(names)
    return row


def safe_get(url, params=None, headers=None, retries=3):
    """GET a URL and force correct decoding.

    requests only trusts the charset declared in the Content-Type header; if
    a server doesn't declare one (common for JSON APIs and many HTML pages)
    requests silently falls back to Latin-1 for .text, which corrupts every
    non-ASCII character (curly quotes, em-dashes, accented names, etc.) and
    is the #1 cause of "garbled CSV" output. We override that here.

    Transient transport-level failures (DNS hiccups, "no route to host",
    connection resets, timeouts) are retried with backoff instead of giving
    up on the whole portal — flaky networks are the norm, not the exception.
    Only a real HTTP error status or a repeat transport failure gives up.
    """
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        resp = None
        try:
            resp = requests.get(
                url,
                params=params,
                headers=h,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                stream=True,
            )
            resp.raise_for_status()
            chunks = []
            bytes_read = 0
            started_at = time.monotonic()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if time.monotonic() - started_at > BODY_TIMEOUT:
                    raise requests.Timeout(f"response body timed out after {BODY_TIMEOUT:g}s")
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read > MAX_RESPONSE_BYTES:
                    raise requests.RequestException(
                        f"response exceeded {MAX_RESPONSE_BYTES} bytes"
                    )
                chunks.append(chunk)
            resp._content = b"".join(chunks)
            declared = (resp.headers.get("content-type") or "").lower()
            if "charset" not in declared:
                # no explicit charset from the server -> don't trust requests'
                # Latin-1 default, decode the raw bytes as UTF-8 (the overwhelming
                # norm on the modern web) with a safe fallback.
                resp.encoding = "utf-8"
            return resp
        except requests.RequestException as e:
            if resp is not None:
                resp.close()
            # requests.Timeout is a subclass of requests.ConnectionError, so
            # this covers DNS failures, connect errors, resets and timeouts.
            retriable = isinstance(e, requests.ConnectionError)
            if retriable and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"  [warn] request failed for {url} after {attempt + 1} attempt(s): {e}",
                  file=sys.stderr)
            return None


def safe_json(resp):
    """Parse JSON directly off the raw response bytes (bypasses any charset
    guessing entirely) so API responses can never get mangled."""
    try:
        return json.loads(resp.content.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None


def normalize_pay(pay_text):
    """Normalize and clean pay/salary text."""
    if not pay_text:
        return ""
    # Basic cleanup: strip extra whitespace, normalize common patterns
    normalized = pay_text.strip()
    # Remove multiple spaces
    normalized = " ".join(normalized.split())
    return normalized


def make_soup(resp):
    """Build a BeautifulSoup tree from raw bytes so BeautifulSoup's own
    (more reliable) encoding detection is used instead of requests' guess."""
    return BeautifulSoup(resp.content, "html.parser", from_encoding="utf-8")


def extract_jsonld_jobposting(soup):
    """Many ATS-backed career pages (Greenhouse, Lever, Workday, and lots of
    the smaller boards) embed a schema.org JobPosting <script type=
    'application/ld+json'> block. When present it's a much more reliable
    source of company/location/salary than scraping visible text."""
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw or "JobPosting" not in raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") in ("JobPosting", ["JobPosting"]):
                return item
    return None


def parse_jsonld_fields(jd):
    """Pull company / location / pay out of a schema.org JobPosting dict."""
    company = ""
    org = jd.get("hiringOrganization")
    if isinstance(org, dict):
        company = org.get("name", "") or ""
    elif isinstance(org, str):
        company = org

    location = ""
    loc = jd.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            location = ", ".join(p for p in parts if p)
    if not location and jd.get("jobLocationType") == "TELECOMMUTE":
        location = "Remote"

    pay = ""
    base = jd.get("baseSalary")
    if isinstance(base, dict):
        val = base.get("value")
        if isinstance(val, dict):
            lo, hi = val.get("minValue"), val.get("maxValue")
            unit = val.get("unitText", "")
            currency = base.get("currency", "")
            if lo and hi:
                pay = f"{currency} {lo}-{hi} {unit}".strip()
            elif val.get("value"):
                pay = f"{currency} {val.get('value')} {unit}".strip()

    description = jd.get("description", "")
    title = jd.get("title", "")
    return {"company": company, "location": location, "pay": pay, "description": description, "title": title}


DETAIL_DESCRIPTION_SELECTORS = [
    "[data-automation='jobDescription']",
    "[data-testid='job-description']",
    "[data-testid='jobDescription']",
    "[data-qa='job-description']",
    "[itemprop='description']",
    ".job-description",
    ".jobDescription",
    ".job-description__content",
    ".jobsearch-JobComponent-description",
    ".description",
    ".posting-description",
    ".content-intro",
    ".jobDetails",
    ".job-details",
    "#job-description",
    "#jobDescription",
    "#job_summary",
    "main",
    "article",
]


def _best_detail_page_text(soup, fallback_text=""):
    """Extract the most job-like description text from a detail page.

    Prefer schema.org JobPosting JSON-LD and common ATS/job-board description
    containers. Falling back to the whole body is useful for plain pages but
    guarded by length checks in choose_full_description() so nav-heavy pages do
    not replace cleaner API text.
    """
    candidates = []
    jd = extract_jsonld_jobposting(soup)
    if jd:
        fields = parse_jsonld_fields(jd)
        if fields.get("description"):
            candidates.append(fields["description"])

    for selector in DETAIL_DESCRIPTION_SELECTORS:
        for node in soup.select(selector):
            text = node.get_text("\n")
            if text:
                candidates.append(text)

    if fallback_text:
        candidates.append(fallback_text)

    cleaned = [clean_text(text) for text in candidates if text and clean_text(text)]
    if not cleaned:
        return ""

    # Prefer the richest candidate, but avoid absurd whole-page captures when a
    # focused description container exists.
    focused = [text for text in cleaned if len(text) <= 50000]
    return max(focused or cleaned, key=len)


def choose_full_description(current_desc, detail_desc):
    """Return detail_desc when it is meaningfully richer than current_desc."""
    current = clean_text(current_desc or "")
    detail = clean_text(detail_desc or "")
    if not detail:
        return current
    if not current:
        return detail
    current_len = len(current)
    detail_len = len(detail)
    # If a feed/API summary is short, accept a detail page that is clearly more
    # substantial. For already-rich ATS/API descriptions, keep the cleaner text
    # unless the detail page adds a lot of content.
    if detail_len >= max(current_len + 250, int(current_len * 1.2)):
        return detail
    return current


def fetch_full_description_from_url(url, current_desc="", portal_name=""):
    """Fetch a job detail URL and return the fullest description we can find.

    This is intentionally best-effort: if a portal blocks HTTP requests,
    redirects to a login wall, or renders entirely in JavaScript, callers keep
    their existing API/feed description.
    """
    if not FETCH_FULL_DETAIL_DESCRIPTIONS or not url:
        return clean_text(current_desc or "")
    
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return clean_text(current_desc or "")
    host = (parsed.netloc or "").lower()
    if host in _DETAIL_FETCH_FAILED_HOSTS:
        return clean_text(current_desc or "")

    # Skip inactive/broken/tracking source links. This uses the cached result
    # from make_row() in the common path, so it does not issue a second probe.
    if not is_source_link_active(url):
        print(f"  [skip] {portal_name}: skipping inactive/broken source link for description", file=sys.stderr)
        return clean_text(current_desc or "")

    resp = safe_get(url)
    if not resp:
        if host:
            _DETAIL_FETCH_FAILED_HOSTS.add(host)
        return clean_text(current_desc or "")
    soup = make_soup(resp)
    main = soup.find("main") or soup.find("article") or soup.body
    fallback = main.get_text("\n") if main else soup.get_text("\n")
    detail = _best_detail_page_text(soup, fallback)
    chosen = choose_full_description(current_desc, detail)
    if chosen and len(chosen) > len(clean_text(current_desc or "")) + 250:
        label = f"{portal_name}: " if portal_name else ""
        print(f"    [detail] {label}expanded description from {len(clean_text(current_desc or ''))} to {len(chosen)} chars")
    return chosen


# --------------------------------------------------------------------------
# 1. API-based scrapers (real, working, no key needed)
# --------------------------------------------------------------------------

def scrape_remoteok(limit=50):
    rows = []
    resp = safe_get("https://remoteok.com/api")
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue  # first element is a legal-notice blob, skip it
        title = item.get("position", "")
        company = item.get("company", "")
        url = item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('id')}"
        desc = item.get("description", "")
        location = normalize_location(item.get("location", "") or "Remote")
        if not is_probable_job_post(title, company, desc):
            continue
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        pay_hint = f"${salary_min}-${salary_max}" if salary_min and salary_max else ""
        row = make_row("Remote OK", title, company, url, desc, location)
        if not row:
            continue
        if pay_hint:
            row["Pay"] = pay_hint
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def scrape_remotive(limit=50):
    rows = []
    resp = safe_get("https://remotive.com/api/remote-jobs")
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows
    for item in data.get("jobs", [])[:limit]:
        title = item.get("title", "")
        company = item.get("company_name", "")
        url = item.get("url", "")
        desc = item.get("description", "")
        location = item.get("candidate_required_location", "") or "Remote"
        pay_hint = item.get("salary", "")
        row = make_row("Remotive", title, company, url, desc, location)
        if not row:
            continue
        set_portal_skills(row, item.get("tags"))
        if pay_hint:
            row["Pay"] = pay_hint
        rows.append(row)
    return rows


def scrape_hn_whoshiring(limit=50):
    """Uses the public Algolia Hacker News Search API to find the latest
    'Ask HN: Who is hiring?' thread and pulls top-level comments as postings."""
    rows = []
    resp = safe_get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"tags": "story,author_whoishiring", "query": "Who is hiring"},
    )
    if not resp:
        return rows
    search_data = safe_json(resp)
    hits = (search_data or {}).get("hits", [])
    if not hits:
        return rows
    thread_id = hits[0]["objectID"]
    item_resp = safe_get(f"https://hn.algolia.com/api/v1/items/{thread_id}")
    if not item_resp:
        return rows
    thread = safe_json(item_resp)
    if thread is None:
        return rows
    for comment in thread.get("children", [])[:limit]:
        text = comment.get("text") or ""
        if not text:
            continue
        first_line = clean_text(text).split("\n")[0][:120]
        url = f"https://news.ycombinator.com/item?id={comment.get('id')}"
        row = make_row("Hacker News Who's Hiring", first_line, "", url, text)
        if row:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# 2. RSS-based scrapers (real, working, no key needed)
# --------------------------------------------------------------------------

def scrape_rss(portal_name, feed_url, limit=50):
    rows = []
    if feedparser is None:
        print("  [warn] feedparser not installed, skipping RSS portal:", portal_name)
        return rows
    win_start, win_end = get_date_window()
    feed = feedparser.parse(feed_url)
    for entry in feed.entries:
        # Use published_parsed (struct_time in UTC) if available, else pubDate string
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import calendar
            pub_date = datetime.utcfromtimestamp(
                calendar.timegm(entry.published_parsed)
            ).date()
        else:
            pub_date = parse_date(entry.get("published", "") or entry.get("updated", ""))

        in_win = is_in_window(pub_date)
        if in_win is False:
            if is_too_old(pub_date):
                break  # RSS feeds are chronological newest-first; stop early
            continue   # posted today — skip but keep going

        title = entry.get("title", "")
        url = entry.get("link", "")
        desc = entry.get("summary", "") or entry.get("description", "")
        company = ""
        if ":" in title:
            maybe_company, maybe_title = title.split(":", 1)
            if len(maybe_company) < 40:
                company, title = maybe_company.strip(), maybe_title.strip()
        desc = fetch_full_description_from_url(url, desc, portal_name)
        row = make_row(portal_name, title, company, url, desc)
        if row:
            if pub_date:
                row["Scraped At"] = str(pub_date)
            rows.append(row)
    return rows


def scrape_weworkremotely(limit=None):
    # WWR publishes an RSS feed per category; the combined/all-jobs feed:
    return scrape_rss("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss")


def scrape_workingnomads(limit=None):
    return scrape_rss("Working Nomads", "https://www.workingnomads.com/jobs.rss")


def scrape_nodesk(limit=None):
    return scrape_rss("NoDesk", "https://nodesk.co/remote-jobs/feed/")


# --------------------------------------------------------------------------
# 3. Official APIs that require a free key (env vars) - implemented but
#    skipped automatically if the key isn't set.
# --------------------------------------------------------------------------

def scrape_adzuna(limit=None):
    """Adzuna USA jobs API — filters to the previous 5 days, excludes today.
    Paginates until results go outside the date window.
    Adzuna results are sorted newest-first; stop as soon as a full page is too old.
    """
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("  [skip] Adzuna USA: set ADZUNA_APP_ID / ADZUNA_APP_KEY env vars "
              "(free at https://developer.adzuna.com/) to enable")
        return []
    rows = []
    seen_jobs = set()
    page = 1
    per_page = 50
    win_start, win_end = get_date_window()
    print(f"  [Adzuna] date window: {win_start} → {win_end}")

    # Adzuna has no absolute date filter (min_date_posted/max_date_posted are
    # rejected with HTTP 400); the only date knob is max_days_old, capped at 30
    # by the API. Ask for just enough days to cover the window so the result set
    # is bounded instead of the full ~6.6M-row index.
    today = datetime.now(timezone.utc).date()
    max_days_old = max(1, min(30, (today - win_start).days + 1))

    # Adzuna clamps pagination at page 100 (ResultsPerPage 50 => 5000 rows).
    # Past that it keeps returning HTTP 200 with the *same* page body forever,
    # so an unbounded `while True` never terminates. Detect the repeated page
    # and stop, and keep a hard page cap as a backstop.
    max_pages = int(os.environ.get("ADZUNA_MAX_PAGES", "100"))
    prev_page_signature = None
    stopped_reason = "exhausted"
    reached_window_start = False

    # Optional query narrowing. Adzuna has no absolute date filter, so the only
    # way to reach a specific past day is to shrink the result set until the
    # 5000-row pagination window spans far enough back.
    extra_params = {}
    adzuna_keyword = os.environ.get("ADZUNA_KEYWORD", "").strip()
    if adzuna_keyword:
        extra_params["what"] = adzuna_keyword
    adzuna_category = os.environ.get("ADZUNA_CATEGORY", "").strip()
    if adzuna_category:
        extra_params["category"] = adzuna_category
    adzuna_loc = os.environ.get("ADZUNA_LOC", "").strip()
    if adzuna_loc:
        extra_params["where"] = adzuna_loc
    if extra_params:
        print(f"  [Adzuna] narrowed query: {extra_params}")

    while True:
        params = {"app_id": app_id, "app_key": app_key,
                  "results_per_page": per_page,
                  "sort_by": "date",
                  "max_days_old": max_days_old,
                  "content-type": "application/json"}
        params.update(extra_params)
        resp = safe_get(
            f"https://api.adzuna.com/v1/api/jobs/us/search/{page}",
            params=params,
        )
        if not resp:
            stopped_reason = f"request failed on page {page}"
            break
        data = safe_json(resp)
        if data is None:
            stopped_reason = f"unparseable JSON on page {page}"
            break

        page_results = data.get("results", [])
        if not page_results:
            stopped_reason = "empty page"
            break

        # Adzuna serves the same page body for every page past its internal
        # clamp, so a repeat of the previous page's ids means we have walked off
        # the end of the index — stop instead of looping forever.
        page_signature = tuple(str(item.get("id", "")) for item in page_results)
        if page_signature == prev_page_signature:
            stopped_reason = f"API returned a repeated page at page {page} (pagination clamp)"
            break
        prev_page_signature = page_signature

        if page > max_pages:
            stopped_reason = f"hit ADZUNA_MAX_PAGES={max_pages}"
            break

        all_too_old = True
        for item in page_results:
            title = item.get("title", "")
            company = (item.get("company") or {}).get("display_name", "")

            job_key = (title.lower().strip(), company.lower().strip())
            if job_key in seen_jobs:
                continue
            seen_jobs.add(job_key)

            posted_date = item.get("created", "")
            in_win = is_in_window(posted_date)
            if in_win is False:
                if is_too_old(posted_date):
                    # We have walked past the window start, so the date-sorted
                    # feed is exhausted for our purposes.
                    reached_window_start = True
                else:
                    all_too_old = False  # it's today — skip but keep paginating
                continue
            if in_win is True:
                all_too_old = False

            url = item.get("redirect_url", "")
            desc = item.get("description", "")
            desc = fetch_full_description_from_url(url, desc, "Adzuna USA")
            location = (item.get("location") or {}).get("display_name", "")

            title = re.sub(r'\$[\d,]+(?:\.\d{2})?\s*(?:per year)?[*\s]*', '', title)
            title = re.sub(r'\s*-\s*', ' - ', title)
            title = re.sub(r'\s+', ' ', title).strip()

            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            salary_predicted = item.get("salary_is_predicted", False)
            if salary_min and salary_max:
                if salary_predicted and abs(salary_min - salary_max) < 100:
                    pay_hint = f"${salary_min:.0f} (est.)"
                elif abs(salary_min - salary_max) < 100:
                    pay_hint = f"${salary_min:.0f}"
                else:
                    pay_hint = f"${salary_min:.0f}-${salary_max:.0f}"
            elif salary_min:
                pay_hint = f"${salary_min:.0f}+"
            else:
                pay_hint = ""

            row = make_row("Adzuna USA", title, company, url, desc, location)
            if not row:
                continue
            if pay_hint:
                row["Pay"] = pay_hint
            if posted_date:
                row["Scraped At"] = posted_date
            rows.append(row)

        # If every item on this page was older than the window, stop paginating
        if all_too_old:
            stopped_reason = "results went past the date window"
            break

        # Adzuna returns fewer than per_page when there are no more results
        if len(page_results) < per_page:
            stopped_reason = f"short page ({len(page_results)} < {per_page})"
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"  [Adzuna] {len(rows)} row(s) in window across {page} page(s); "
          f"stopped: {stopped_reason}")

    # Adzuna hard-clamps pagination at 100 pages x 50 = 5000 rows, and it
    # publishes far more than 5000 jobs per day, so an unfiltered date-sorted
    # walk can end while still sitting on the newest day. Say so instead of
    # letting a 0-row result look like "no jobs were posted".
    if not reached_window_start and not rows:
        print(f"  [Adzuna] warning: stopped before reaching the window start "
              f"({win_start}); the newest {max_pages * 50} Adzuna results did not "
              f"extend back that far, so this portal is likely under-collected "
              f"for {win_start}. Narrow the query (e.g. ADZUNA_KEYWORD / "
              f"ADZUNA_CATEGORY) to reach older dates.", file=sys.stderr)
    return rows


def scrape_usajobs(limit=None):
    """USAJOBS official API — filters to the previous 5 days, excludes today.
    Paginates until results go outside the date window or API is exhausted.
    """
    api_key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not api_key or not email:
        print("  [skip] USAJOBS: set USAJOBS_API_KEY / USAJOBS_EMAIL env vars "
              "(free at https://developer.usajobs.gov/) to enable")
        return []
    rows = []
    PAGE_SIZE = int(os.environ.get("USAJOBS_PAGE_SIZE", "100"))
    page = 1
    win_start, win_end = get_date_window()
    print(f"  [USAJOBS] date window: {win_start} → {win_end}")

    _ambiguous_loc = re.compile(
        r"^(remote|multiple locations?|various|nationwide|telework|anywhere|)$", re.I
    )

    # DatePosted = number of days back to fetch. With a single-day date override
    # we fetch only enough days to reach that date (never more than needed), so
    # results are date-accurate; the per-row is_in_window() filter below then
    # keeps exactly the target day, so runs for different dates don't overlap.
    if DATE_RANGE is not None:
        days_back = max(1, (datetime.now(timezone.utc).date() - DATE_RANGE[0]).days + 1)
    elif DATE_OVERRIDE is not None:
        days_back = max(1, (datetime.now(timezone.utc).date() - DATE_OVERRIDE).days + 1)
    else:
        days_back = 6

    base_params = {
        "ResultsPerPage": PAGE_SIZE,
        "DatePosted": days_back,
    }
    job_category_code = os.environ.get("USAJOBS_JOB_CATEGORY_CODE", "").strip()
    if job_category_code:
        base_params["JobCategoryCode"] = job_category_code
    keyword = os.environ.get("USAJOBS_KEYWORD", "").strip()
    if keyword:
        base_params["Keyword"] = keyword

    too_old_count = 0
    while True:
        params = {**base_params, "Page": page}
        resp = safe_get(
            "https://data.usajobs.gov/api/search",
            params=params,
            headers={"Authorization-Key": api_key, "User-Agent": email, "Host": "data.usajobs.gov"},
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        search_result = data.get("SearchResult", {})
        items = search_result.get("SearchResultItems", [])
        if not items:
            break

        page_had_results = False
        prepared = []
        for item in items:
            d = item.get("MatchedObjectDescriptor", {})
            details = d.get("UserArea", {}).get("Details", {})
            title = d.get("PositionTitle", "")
            company = d.get("OrganizationName", "")
            url = d.get("PositionURI", "")
            desc = clean_text("\n\n".join(
                str(part)
                for part in (
                    details.get("JobSummary", ""),
                    d.get("QualificationSummary", ""),
                    details.get("MajorDuties", ""),
                    details.get("Requirements", ""),
                    details.get("Evaluations", ""),
                    details.get("Benefits", ""),
                )
                if part
            ))
            locations = d.get("PositionLocationDisplay", "")
            if not locations or _ambiguous_loc.match(locations.strip()):
                locations = (locations.strip() + ", USA") if locations.strip() else "United States"

            posted_date = (
                details.get("JobSummaryFields", {}).get("DatePosted")
                or d.get("PositionStartDate", "")
                or d.get("PublicationStartDate", "")
            )

            in_win = is_in_window(posted_date)
            if in_win is False:
                if is_too_old(posted_date):
                    too_old_count += 1
                continue  # today or too old — skip
            # in_win is True or None (unknown date → include, let downstream filter)

            pay = ""
            remun = d.get("PositionRemuneration", [])
            if remun:
                pay = (
                    f"${remun[0].get('MinimumRange', '')}"
                    f"-${remun[0].get('MaximumRange', '')} "
                    f"{remun[0].get('RateIntervalCode', '')}"
                )
            prepared.append((title, company, url, desc, locations, posted_date, pay, d))

        # Probe every candidate link for this page in parallel: the per-row
        # probe in make_row would otherwise block for seconds on each URL.
        prime_source_link_cache([p[2] for p in prepared])

        for title, company, url, desc, locations, posted_date, pay, d in prepared:
            row = make_row("USAJOBS", title, company, url, desc, locations, fetch_detail=False)
            if not row:
                continue
            expiration_date = find_expiration_date_in_obj(d)
            if expiration_date:
                row["Expires At"] = expiration_date.isoformat()
            if pay.strip("$- "):
                row["Pay"] = pay
            if posted_date:
                row["Scraped At"] = posted_date
            rows.append(row)
            page_had_results = True

        # Stop if the whole page was too old (results are sorted newest-first)
        if too_old_count >= len(items) and not page_had_results:
            break

        total_count = int(search_result.get("SearchResultCountAll", 0) or 0)
        if page * PAGE_SIZE >= total_count:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"  [USAJOBS] fetched {len(rows)} jobs in window across {page} page(s)")
    return rows


def scrape_the_muse(limit=None):
    """The Muse public JSON API — filters to previous 5 days, excludes today.
    Paginates until results go outside the date window.
    """
    rows = []
    page = 1
    win_start, win_end = get_date_window()
    print(f"  [The Muse] date window: {win_start} → {win_end}")

    while True:
        resp = safe_get(
            "https://www.themuse.com/api/public/jobs",
            params={"page": page, "descending": "true"},
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        items = data.get("results", [])
        if not items:
            break

        all_too_old = True
        for item in items:
            pub_date = item.get("publication_date", "") or item.get("updated_at", "")
            in_win = is_in_window(pub_date)
            if in_win is False:
                if not is_too_old(pub_date):
                    all_too_old = False  # today — skip but keep paginating
                continue
            if in_win is True:
                all_too_old = False

            title = item.get("name", "")
            company = (item.get("company") or {}).get("name", "")
            url = item.get("refs", {}).get("landing_page", "") or item.get("url", "")
            locations = item.get("locations", [])
            location = next((loc.get("name", "") for loc in locations if loc.get("name")), "")
            levels = item.get("levels", [])
            seniority = ", ".join(lv.get("name", "") for lv in levels) if levels else ""
            cats = item.get("categories", [])
            category = ", ".join(c.get("name", "") for c in cats) if cats else ""
            desc = item.get("contents", "") or category
            row = make_row("The Muse", title, company, url, desc, location)
            if not row:
                continue
            set_portal_skills(row, category)
            if seniority:
                row["Summary"] = (row.get("Summary") or "") + f" Level: {seniority}"
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)

        if all_too_old:
            break
        total_pages = data.get("page_count", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    return rows


def scrape_jobicy(limit=None):
    """Jobicy public JSON API — filters to previous 5 days, excludes today."""
    rows = []
    win_start, win_end = get_date_window()
    # Jobicy API max count is 50; fetch in batches if needed
    resp = safe_get(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": 50},
    )
    if resp:
        data = safe_json(resp)
        if data and "jobs" in data:
            for item in data["jobs"]:
                pub_date = item.get("pubDate", "") or item.get("jobPostDate", "")
                in_win = is_in_window(pub_date)
                if in_win is False:
                    continue  # skip today or too old

                title = item.get("jobTitle", "")
                company = item.get("companyName", "")
                url = item.get("url", "")
                raw_desc = item.get("jobDescription", "")
                desc = BeautifulSoup(raw_desc, "html.parser").get_text("\n") if raw_desc else ""
                location = item.get("jobGeo", "") or item.get("jobType", "") or "Remote"
                pay_min = item.get("annualSalaryMin", "")
                pay_max = item.get("annualSalaryMax", "")
                if pay_min and pay_max:
                    pay_hint = f"${pay_min}-${pay_max}"
                elif pay_min:
                    pay_hint = f"${pay_min}+"
                else:
                    pay_hint = ""
                row = make_row("Jobicy", title, company, url, desc, location)
                if not row:
                    continue
                set_portal_skills(row, item.get("tags"))
                if pay_hint:
                    row["Pay"] = pay_hint
                if pub_date:
                    row["Scraped At"] = pub_date
                rows.append(row)
            return rows
    # Fallback: RSS feed with date filtering
    return scrape_rss("Jobicy", "https://jobicy.com/?feed=job_feed")


def scrape_remotejobs_org(limit=50):
    """RemoteJobs.org public JSON API — no key required."""
    rows = []
    resp = safe_get("https://remotejobs.org/api/v1/jobs")
    if not resp:
        return rows
    data = safe_json(resp)
    if not data:
        return rows
    items = data.get("data", data if isinstance(data, list) else [])
    for item in items[:limit]:
        title = item.get("title", "")
        company_data = item.get("company") or {}
        company = company_data.get("name", "") if isinstance(company_data, dict) else str(company_data)
        url = item.get("url", "") or item.get("apply_url", "")
        desc = item.get("description", "")
        location = item.get("location", "") or "Remote"
        row = make_row("RemoteJobs.org", title, company, url, desc, location)
        if not row:
            continue
        pay = item.get("salary_text", "")
        if not pay and (item.get("salary_min") or item.get("salary_max")):
            pay = "-".join(str(v) for v in [item.get("salary_min"), item.get("salary_max")] if v)
        if pay:
            row["Pay"] = one_line(str(pay))
        if item.get("type"):
            row["Job Type"] = one_line(str(item["type"]))
        category = item.get("category") or {}
        if isinstance(category, dict) and category.get("name"):
            tags = [row.get("Tags", ""), category["name"]]
            row["Tags"] = ", ".join(t for t in tags if t)
        rows.append(row)
    return rows[:limit]


def scrape_reed_uk(limit=None):
    """Reed UK official REST API — needs REED_API_KEY.
    Free registration at https://www.reed.co.uk/developers/jobseeker
    Auth: HTTP Basic with API key as username, empty password.
    Paginates and filters to the current date window.
    """
    api_key = os.environ.get("REED_API_KEY", "").strip()
    if not api_key:
        print("  [skip] Reed UK: set REED_API_KEY env var "
              "(free at https://www.reed.co.uk/developers/jobseeker) to enable")
        return []

    win_start, win_end = get_date_window()
    print(f"  [Reed UK] date window: {win_start} → {win_end}")

    import base64
    auth = base64.b64encode((api_key + ":").encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    rows = []
    results_to_skip = 0

    while True:
        resp = safe_get(
            "https://www.reed.co.uk/api/1.0/search",
            params={"resultsToTake": 100, "resultsToSkip": results_to_skip},
            headers=headers,
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        items = data.get("results") or []
        if not items:
            break

        all_too_old = True
        for item in items:
            pub_date = item.get("date", "") or item.get("expirationDate", "")
            in_win = is_in_window(pub_date)
            if in_win is False:
                if not is_too_old(pub_date):
                    all_too_old = False  # future/today — skip but keep paginating
                continue
            if in_win is True:
                all_too_old = False

            title    = item.get("jobTitle", "")
            company  = item.get("employerName", "")
            job_id   = item.get("jobId", "")
            url      = f"https://www.reed.co.uk/jobs/{job_id}" if job_id else item.get("jobUrl", "")
            raw_desc = item.get("jobDescription", "") or item.get("summary", "")
            desc     = BeautifulSoup(raw_desc, "html.parser").get_text("\n") if raw_desc else ""
            location = item.get("locationName", "") or item.get("location", "")
            pay_min  = item.get("minimumSalary")
            pay_max  = item.get("maximumSalary")
            pay_hint = ""
            if pay_min and pay_max:
                pay_hint = f"£{pay_min:,.0f}-£{pay_max:,.0f}"
            elif pay_min:
                pay_hint = f"£{pay_min:,.0f}+"

            row = make_row("Reed UK", title, company, url, desc, location)
            if not row:
                continue
            if pay_hint:
                row["Pay"] = pay_hint
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)

        if all_too_old:
            break
        results_to_skip += 100
        time.sleep(REQUEST_DELAY)

    print(f"  [Reed UK] fetched {len(rows)} jobs in window")
    return rows


def scrape_careerjet(limit=None):
    """CareerJet partner API — needs CAREERJET_AFFID affiliate ID.
    Free registration at https://www.careerjet.com/partners/
    Paginates and filters to the current date window.
    """
    affid = os.environ.get("CAREERJET_AFFID", "").strip()
    if not affid:
        print("  [skip] CareerJet: set CAREERJET_AFFID env var "
              "(free affiliate ID at https://www.careerjet.com/partners/) to enable")
        return []

    win_start, win_end = get_date_window()
    print(f"  [CareerJet] date window: {win_start} → {win_end}")

    careerjet_headers = {"Referer": "https://www.careerjet.com/"}
    rows = []
    page = 1

    while True:
        resp = safe_get(
            "http://public.api.careerjet.net/search",
            params={
                "affid":      affid,
                "keywords":   os.environ.get("CAREERJET_KEYWORDS", ""),
                "location":   os.environ.get("CAREERJET_LOCATION", ""),
                "pagesize":   99,
                "page":       page,
                "locale_code": os.environ.get("CAREERJET_LOCALE", "en_US"),
                "user_ip":    os.environ.get("CAREERJET_USER_IP", "1.0.0.1"),
                "user_agent": "Mozilla/5.0 (compatible; JobResearchBot/1.0)",
                "sort":       "date",  # newest first so we can stop early
            },
            headers=careerjet_headers,
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        items = data.get("jobs") or []
        if not items:
            break

        all_too_old = True
        for item in items:
            pub_date = item.get("date", "") or item.get("onlinedate", "")
            in_win = is_in_window(pub_date)
            if in_win is False:
                if not is_too_old(pub_date):
                    all_too_old = False
                continue
            if in_win is True:
                all_too_old = False

            title    = item.get("title", "")
            company  = item.get("company", "")
            url      = item.get("url", "")
            desc     = item.get("description", "")
            location = item.get("locations", "") or item.get("location", "") or item.get("site", "")
            pay_hint = item.get("salary", "")

            row = make_row("CareerJet", title, company, url, desc, location)
            if not row:
                continue
            if pay_hint:
                row["Pay"] = pay_hint
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)

        if all_too_old:
            break
        total_pages = data.get("pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"  [CareerJet] fetched {len(rows)} jobs in window across {page} page(s)")
    return rows


def careerjet_scrape(limit=50):
    """CareerJet partner API — needs CAREERJET_AFFID affiliate ID.
    Free registration at https://www.careerjet.com/partners/
    """
    affid = os.environ.get("CAREERJET_AFFID", "").strip()
    if not affid:
        print("  [skip] CareerJet: set CAREERJET_AFFID env var "
              "(free affiliate ID at https://www.careerjet.com/partners/) to enable")
        return []
    rows = []
    resp = safe_get(
        "http://public.api.careerjet.net/search",
        params={
            "affid": affid,
            "keywords": os.environ.get("CAREERJET_KEYWORDS", ""),
            "location": os.environ.get("CAREERJET_LOCATION", ""),
            "pagesize": min(limit, 99),
            "page": 1,
            "locale_code": os.environ.get("CAREERJET_LOCALE", "en_GB"),
        },
    )
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows
    for item in (data.get("jobs") or [])[:limit]:
        title = item.get("title", "")
        company = item.get("company", "")
        url = item.get("url", "")
        desc = item.get("description", "")
        location = item.get("locations", "") or item.get("site", "")
        pay_hint = item.get("salary", "")
        row = make_row("CareerJet", title, company, url, desc, location)
        if not row:
            continue
        if pay_hint:
            row["Pay"] = pay_hint
        rows.append(row)
    return rows


def scrape_arbeitnow(limit=None):
    """Arbeitnow free public API — filters to previous 5 days, excludes today.
    Paginates until results go outside the date window.
    """
    rows = []
    page = 1
    win_start, win_end = get_date_window()
    print(f"  [Arbeitnow] date window: {win_start} → {win_end}")

    while True:
        resp = safe_get(
            "https://www.arbeitnow.com/api/job-board-api",
            params={"page": page},
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        items = data.get("data", []) if isinstance(data, dict) else data
        if not items:
            break

        all_too_old = True
        for item in items:
            # created_at is a Unix timestamp integer
            created_at = item.get("created_at") or item.get("updated_at") or ""
            in_win = is_in_window(created_at)
            if in_win is False:
                if not is_too_old(created_at):
                    all_too_old = False  # today — skip but keep paginating
                continue
            if in_win is True:
                all_too_old = False

            title = item.get("title", "")
            company = item.get("company_name", "")
            url = item.get("url", "")
            desc = item.get("description", "") or item.get("job_description", "")
            location = item.get("location", "")
            job_type = item.get("job_types", [])
            if isinstance(job_type, list) and job_type:
                job_type = job_type[0]
            else:
                job_type = ""
            remote = item.get("remote", False)
            if remote:
                location = "Remote" if not location else f"{location} (Remote)"
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            pay_hint = ""
            if salary_min and salary_max:
                pay_hint = f"${salary_min}-${salary_max}"
            elif salary_min:
                pay_hint = f"${salary_min}+"
            visa = item.get("visa_sponsorship", False)
            row = make_row("Arbeitnow", title, company, url, desc, location, fetch_detail=False)
            if not row:
                continue
            set_portal_skills(row, item.get("tags"))
            if pay_hint:
                row["Pay"] = pay_hint
            if job_type:
                row["Job Type"] = job_type
            if visa:
                row["Benefits"] = (row.get("Benefits") or "") + " | Visa sponsorship available"
            if created_at:
                row["Scraped At"] = str(parse_date(created_at) or "")
            rows.append(row)

        if all_too_old:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    return rows


def scrape_arbeitnow_uk(limit=50):
    """Arbeitnow UK free public API — no key required.
    UK jobs from multiple ATS (Greenhouse, SmartRecruiters, etc.)
    API: https://www.arbeitnow.co.uk/api/job-board-api
    Response format: {"data": [job1, job2, ...]}
    """
    rows = []
    page = 1
    while len(rows) < limit:
        resp = safe_get(
            "https://www.arbeitnow.co.uk/api/job-board-api",
            params={"page": page},
        )
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break
        # Response is {"data": [...]} not a direct list
        items = data.get("data", []) if isinstance(data, dict) else data
        if not items:
            break
        for item in items:
            title = item.get("title", "")
            company = item.get("company_name", "")
            url = item.get("url", "")
            desc = item.get("description", "") or item.get("job_description", "")
            location = item.get("location", "") or "UK"
            job_type = item.get("job_types", [])
            if isinstance(job_type, list) and job_type:
                job_type = job_type[0]
            else:
                job_type = ""
            remote = item.get("remote", False)
            if remote:
                location = "Remote" if not location else f"{location} (Remote)"
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            pay_hint = ""
            if salary_min and salary_max:
                pay_hint = f"£{salary_min}-£{salary_max}"
            elif salary_min:
                pay_hint = f"£{salary_min}+"
            row = make_row("Arbeitnow UK", title, company, url, desc, location)
            if not row:
                continue
            set_portal_skills(row, item.get("tags"))
            if pay_hint:
                row["Pay"] = pay_hint
            if job_type:
                row["Job Type"] = job_type
            rows.append(row)
            if len(rows) >= limit:
                break
        page += 1
    return rows[:limit]


def scrape_findwork(limit=None):
    """Findwork API — needs FINDWORK_API_KEY.
    Free registration at https://findwork.dev/
    Tech and non-tech jobs globally. Paginates and filters to date window.
    """
    api_key = os.environ.get("FINDWORK_API_KEY", "").strip()
    if not api_key:
        print("  [skip] Findwork: set FINDWORK_API_KEY env var "
              "(free at https://findwork.dev/) to enable")
        return []

    win_start, win_end = get_date_window()
    print(f"  [Findwork] date window: {win_start} → {win_end}")

    headers = {"Authorization": f"Token {api_key}"}
    rows = []
    url = "https://findwork.dev/api/jobs/"

    while url:
        resp = safe_get(url, params={"limit": 100} if "?" not in url else None, headers=headers)
        if not resp:
            break
        data = safe_json(resp)
        if data is None:
            break

        items = data.get("results", []) if isinstance(data, dict) else data
        if not items:
            break

        all_too_old = True
        for item in items:
            pub_date = item.get("date_posted", "") or item.get("created_at", "")
            in_win = is_in_window(pub_date)
            if in_win is False:
                if not is_too_old(pub_date):
                    all_too_old = False
                continue
            if in_win is True:
                all_too_old = False

            title    = item.get("role", "") or item.get("title", "")
            company  = item.get("company_name", "") or item.get("company", "")
            job_url  = item.get("url", "")
            desc     = item.get("text", "") or item.get("description", "")
            location = item.get("location", "") or item.get("region", "")
            remote   = item.get("remote", False)
            if remote:
                location = "Remote" if not location else f"{location} (Remote)"
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            pay_hint = ""
            if salary_min and salary_max:
                currency = item.get("currency", "USD")
                pay_hint = f"{currency} {salary_min}-{salary_max}"
            elif salary_min:
                currency = item.get("currency", "USD")
                pay_hint = f"{currency} {salary_min}+"

            row = make_row("Findwork", title, company, job_url, desc, location)
            if not row:
                continue
            set_portal_skills(row, item.get("keywords") or item.get("tags"))
            if pay_hint:
                row["Pay"] = pay_hint
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)

        if all_too_old:
            break
        # Findwork returns a `next` cursor URL for pagination
        url = data.get("next") if isinstance(data, dict) else None
        if url:
            time.sleep(REQUEST_DELAY)

    print(f"  [Findwork] fetched {len(rows)} jobs in window")
    return rows


def scrape_remote1stjobs(limit=50):
    """Remote1stJobs public JSON API — no key required.
    UK/Europe/EMEA remote jobs feed. Most results will be filtered out by the
    default USA_ONLY_FILTER; set USA_ONLY_TREAT_UNKNOWN_AS_USA=1 to keep jobs
    whose location is ambiguous (e.g. bare "Remote" with no country signal).
    API: https://www.remote1stjobs.com/jobs.json
    Docs: https://www.remote1stjobs.com/remote-jobs-api-rss-europe
    """
    rows = []
    resp = safe_get("https://www.remote1stjobs.com/jobs.json")
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows

    # The feed may be a bare list or wrapped in a dict keyed by "jobs", "data",
    # "positions", "results", etc. — handle all common shapes.
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Try common wrapper keys in priority order
        for key in ("jobs", "data", "positions", "results", "listings"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        else:
            # No recognised wrapper — warn and bail so the mismatch is visible
            top_keys = list(data.keys())[:8]
            print(f"  [warn] Remote1stJobs: unexpected response shape "
                  f"(top-level keys: {top_keys}). "
                  "Check https://www.remote1stjobs.com/jobs.json and update the scraper.",
                  file=sys.stderr)
            return rows
    else:
        return rows

    if not items:
        return rows

    win_start, win_end = get_date_window()

    for item in items:
        if not isinstance(item, dict):
            continue

        # ── date filter ───────────────────────────────────────────────────
        pub_date = (
            item.get("date_posted")
            or item.get("pubDate")
            or item.get("published_at")
            or item.get("created_at")
            or item.get("date")
            or item.get("posted_at")
            or ""
        )
        in_win = is_in_window(pub_date)
        if in_win is False:
            continue  # outside the 5-day window — skip

        # ── field extraction (handles multiple naming conventions) ────────
        title = (
            item.get("title")
            or item.get("job_title")
            or item.get("position")
            or item.get("name")
            or ""
        )
        company = (
            item.get("company")
            or item.get("company_name")
            or item.get("employer")
            or item.get("organization")
            or ""
        )
        if isinstance(company, dict):
            company = company.get("name", "")

        url = (
            item.get("url")
            or item.get("link")
            or item.get("job_url")
            or item.get("apply_url")
            or item.get("href")
            or ""
        )
        desc = (
            item.get("description")
            or item.get("job_description")
            or item.get("content")
            or item.get("body")
            or item.get("excerpt")
            or item.get("summary")
            or ""
        )
        desc = fetch_full_description_from_url(url, desc, "Remote1stJobs")
        location = (
            item.get("location")
            or item.get("job_location")
            or item.get("region")
            or item.get("country")
            or "Remote"
        )
        if isinstance(location, list):
            location = next((str(l) for l in location if l), "")
        elif isinstance(location, dict):
            location = location.get("name") or location.get("display_name") or "Remote"

        # salary / pay
        salary_min = item.get("salary_min") or item.get("min_salary")
        salary_max = item.get("salary_max") or item.get("max_salary")
        pay_text = item.get("salary") or item.get("pay") or item.get("compensation") or ""
        if salary_min and salary_max:
            pay_hint = f"{salary_min}-{salary_max}"
        elif salary_min:
            pay_hint = f"{salary_min}+"
        elif pay_text:
            pay_hint = str(pay_text)
        else:
            pay_hint = ""

        # job type / tags
        job_type = item.get("job_type") or item.get("type") or item.get("employment_type") or ""
        if isinstance(job_type, list):
            job_type = job_type[0] if job_type else ""

        tags_raw = item.get("tags") or item.get("categories") or item.get("skills") or []
        extra_tags = ""
        if isinstance(tags_raw, list):
            extra_tags = ", ".join(str(t) for t in tags_raw if t)
        elif isinstance(tags_raw, str):
            extra_tags = tags_raw

        row = make_row("Remote1stJobs", title, company, url, desc, str(location))
        if not row:
            continue
        if pay_hint:
            row["Pay"] = one_line(pay_hint)
        if job_type:
            row["Job Type"] = one_line(str(job_type))
        set_portal_skills(row, tags_raw)
        if extra_tags:
            existing = row.get("Tags", "")
            row["Tags"] = ", ".join(t for t in [existing, extra_tags] if t)
        if pub_date:
            row["Scraped At"] = one_line(str(pub_date))
        rows.append(row)
        if len(rows) >= limit:
            break

    return rows


def scrape_graphql_jobs(limit=50):
    """GraphQL Jobs — public GraphQL API, no key required.
    Developer/tech jobs with GraphQL focus.
    API: https://api.graphql.jobs/
    """
    rows = []
    # GraphQL jobs has a GraphQL endpoint, but also provides a simple REST-like interface
    resp = safe_get("https://api.graphql.jobs/jobs")
    if not resp:
        return rows
    try:
        data = resp.json()
    except:
        return rows
    if data is None:
        return rows
    items = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = [items]
    for item in items[:limit]:
        title = item.get("title", "")
        company = item.get("company", {})
        if isinstance(company, dict):
            company = company.get("name", "")
        url = item.get("url", "")
        desc = item.get("description", "")
        location = item.get("location", "") or item.get("locations", "")
        if isinstance(location, list):
            location = next((l for l in location if l), "")
        remote = item.get("remote", False)
        if remote:
            location = "Remote" if not location else f"{location} (Remote)"
        row = make_row("GraphQL Jobs", title, company, url, desc, location)
        if not row:
            continue
        set_portal_skills(row, item.get("tags"))
        rows.append(row)
    return rows[:limit]


def scrape_devit_jobs_uk(limit=50):
    """DevIT Jobs UK — public GraphQL API, no key required.
    UK tech jobs with GraphQL interface.
    """
    rows = []
    # DevIT Jobs provides GraphQL endpoint
    resp = safe_get("https://devitjobs.uk/graphql", params={})
    if not resp:
        # Fallback to direct endpoint if available
        resp = safe_get("https://devitjobs.uk/api/jobs")
    if not resp:
        return rows
    try:
        data = resp.json()
    except:
        return rows
    if data is None:
        return rows
    items = data.get("data", data.get("jobs", data)) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = [items]
    for item in items[:limit]:
        title = item.get("title", "")
        company = item.get("company", "")
        if isinstance(company, dict):
            company = company.get("name", "")
        url = item.get("url", "") or item.get("link", "")
        desc = item.get("description", "") or item.get("content", "")
        location = item.get("location", "") or "UK"
        remote = item.get("remote", False)
        if remote:
            location = "Remote" if not location else f"{location} (Remote)"
        row = make_row("DevIT Jobs UK", title, company, url, desc, location)
        if row:
            rows.append(row)
    return rows[:limit]


def scrape_himalayas(limit=50):
    """Himalayas public API — no key required.
    Remote jobs, all types, global coverage.
    API: https://himalayas.app/jobs/api
    Response is a list of job objects.
    """
    rows = []
    offset = 0
    while len(rows) < limit:
        resp = safe_get(
            "https://himalayas.app/jobs/api",
            params={"limit": min(20, limit - len(rows)), "offset": offset},
        )
        if not resp:
            break
        try:
            data = resp.json()
        except:
            break
        # Response is a list, not a dict with {"data": [...]}
        if not data or not isinstance(data, list):
            break
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            company = item.get("companyName", "")
            url = item.get("applicationLink", "")
            desc = item.get("description", "") or item.get("excerpt", "")
            location = "Remote"
            location_restrictions = item.get("locationRestrictions")
            if location_restrictions and isinstance(location_restrictions, list) and location_restrictions:
                location = ", ".join(location_restrictions)
            timezone_restriction = item.get("timezoneRestriction")
            if timezone_restriction and isinstance(timezone_restriction, list) and timezone_restriction:
                location = f"Remote (Timezone: {timezone_restriction[0]})"
            employment_type = item.get("employmentType", "")
            salary_min = item.get("minSalary")
            salary_max = item.get("maxSalary")
            currency = item.get("currency", "USD")
            pay_hint = ""
            if salary_min and salary_max:
                pay_hint = f"{currency} {salary_min}-{salary_max}"
            elif salary_min:
                pay_hint = f"{currency} {salary_min}+"
            row = make_row("Himalayas", title, company, url, desc, location)
            if not row:
                continue
            set_portal_skills(row, item.get("skillTags") or item.get("tags"))
            if pay_hint:
                row["Pay"] = pay_hint
            if employment_type:
                row["Job Type"] = employment_type
            rows.append(row)
            if len(rows) >= limit:
                break
        offset += 20
    return rows[:limit]


def scrape_jooble(limit=None):
    """Jooble official API — needs JOOBLE_API_KEY.
    Free registration at https://jooble.org/api/about
    Paginates and filters to the current date window (respects DATE_OVERRIDE
    for single-day scraping). Stops paginating once results go outside the window.
    """
    api_key = os.environ.get("JOOBLE_API_KEY", "").strip()
    if not api_key:
        print("  [skip] Jooble USA: set JOOBLE_API_KEY env var "
              "(free at https://jooble.org/api/about) to enable")
        return []

    win_start, win_end = get_date_window()
    print(f"  [Jooble USA] date window: {win_start} → {win_end}")

    rows = []
    page = 1

    while True:
        # Jooble uses POST: endpoint is https://jooble.org/api/<api_key>
        # Jooble sits behind Cloudflare and requires browser-like headers.
        try:
            h = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Origin": "https://jooble.org",
                "Referer": "https://jooble.org/",
            }
            resp = requests.post(
                f"https://jooble.org/api/{api_key}",
                json={"keywords": "", "location": "usa", "page": page},
                headers=h,
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [warn] Jooble USA request failed (page {page}): {e}", file=sys.stderr)
            break

        try:
            # Jooble returns UTF-8 with a BOM — decode with utf-8-sig to strip it
            data = json.loads(resp.content.decode("utf-8-sig"))
        except Exception:
            break

        if data is None:
            break

        items = data.get("jobs", []) if isinstance(data, dict) else data
        if not items:
            break

        all_too_old = True
        for item in items:
            pub_date = item.get("updated", "") or item.get("date", "")
            in_win = is_in_window(pub_date)

            if in_win is False:
                if not is_too_old(pub_date):
                    all_too_old = False  # today — skip but keep paginating
                continue
            if in_win is True:
                all_too_old = False

            title    = item.get("title", "")
            company  = item.get("company", "")
            url      = item.get("link", "")
            desc     = item.get("snippet", "") or item.get("description", "")
            desc     = fetch_full_description_from_url(url, desc, "Jooble USA")
            location = item.get("location", "")
            pay_hint = item.get("salary", "")

            row = make_row("Jooble USA", title, company, url, desc, location)
            if not row:
                continue
            if pay_hint:
                row["Pay"] = pay_hint
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)

        # Jooble results are newest-first; stop once we've passed the window
        if all_too_old:
            break

        total_count = data.get("totalCount", 0) if isinstance(data, dict) else 0
        items_per_page = len(items) or 20
        total_pages = (total_count // items_per_page) + 1 if total_count else page + 1
        if page >= total_pages:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"  [Jooble USA] fetched {len(rows)} jobs in window across {page} page(s)")
    return rows


def scrape_api_ninjas(limit=50):
    """API Ninjas Jobs API — needs API_NINJAS_API_KEY.
    Free registration at https://api-ninjas.com/
    All job types, global coverage.
    """
    api_key = os.environ.get("API_NINJAS_API_KEY", "").strip()
    if not api_key:
        print("  [skip] API Ninjas: set API_NINJAS_API_KEY env var "
              "(free at https://api-ninjas.com/) to enable")
        return []
    rows = []
    keywords = ["", "developer", "engineer", "manager", "analyst", "sales"]  # rotate keywords for variety
    for kw_idx, keyword in enumerate(keywords):
        if len(rows) >= limit:
            break
        resp = safe_get(
            "https://api.api-ninjas.com/v1/jobs",
            params={
                "keyword": keyword,
                "limit": min(limit - len(rows), 30),
            },
            headers={"X-Api-Key": api_key},
        )
        if not resp:
            continue
        try:
            data = resp.json()
        except:
            continue
        if not data:
            continue
        items = data if isinstance(data, list) else data.get("jobs", [])
        for item in items:
            title = item.get("title", "")
            company = item.get("employer", "")
            url = item.get("url", "") or item.get("link", "")
            desc = item.get("description", "") or item.get("job_description", "")
            desc = fetch_full_description_from_url(url, desc, "API Ninjas")
            location = item.get("location", "")
            job_type = item.get("job_type", "") or item.get("employment_type", "")
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            pay_hint = ""
            if salary_min and salary_max:
                pay_hint = f"${salary_min}-${salary_max}"
            elif salary_min:
                pay_hint = f"${salary_min}+"
            row = make_row("API Ninjas", title, company, url, desc, location)
            if not row:
                continue
            if pay_hint:
                row["Pay"] = pay_hint
            if job_type:
                row["Job Type"] = job_type
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows[:limit]


# --------------------------------------------------------------------------
# 4. Generic best-effort HTML scraper (used for the remaining ~50 portals)
# --------------------------------------------------------------------------

JOB_LINK_KEYWORDS = ["job", "career", "position", "opening", "vacan", "role"]

# Generic nav/UI text that sometimes gets misidentified as a job title when a
# listing or job page doesn't render the way we expect (JS-heavy pages,
# cookie-consent interstitials, etc.)
JUNK_TITLE_WORDS = {
    "menu", "home", "jobs", "job", "careers", "career", "search", "sign in",
    "log in", "login", "sign up", "signup", "register", "apply now", "apply",
    "about", "about us", "contact", "contact us", "faq", "help", "blog",
    "privacy", "privacy policy", "terms", "terms of service", "cookie",
    "cookies", "cookie policy", "compatibility", "browser", "javascript",
    "view all jobs", "back to jobs", "share", "print", "next", "previous",
}


def is_junk_title(title, portal_name):
    if not title:
        return True
    t = title.strip().lower()
    if len(t) < 3:
        return True
    if t in JUNK_TITLE_WORDS:
        return True
    # title that's just the portal/brand name (e.g. a logo link mistaken for
    # the job title) rather than an actual job title
    portal_slug = re.sub(r"[^a-z0-9]", "", portal_name.lower())
    title_slug = re.sub(r"[^a-z0-9]", "", t)
    if title_slug == portal_slug:
        return True
    return False


def find_job_links(base_url, soup, max_links=15):
    links = []
    seen = set()
    base_path = urlparse(base_url).path.rstrip("/")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        full = urljoin(base_url, href)
        if full in seen:
            continue
        parsed = urlparse(full)
        path = parsed.path.lower().rstrip("/")
        if not path or path == base_path.lower():
            continue  # skip links back to the listing page itself
        if text.strip().lower() in JUNK_TITLE_WORDS:
            continue
        last_segment = path.rsplit("/", 1)[-1]
        # require something that looks like an individual posting's slug
        # (has some length / word separators), not just "/jobs" or "/careers"
        looks_like_slug = len(last_segment) > 8 or "-" in last_segment or "_" in last_segment
        if any(kw in path for kw in JOB_LINK_KEYWORDS) and len(text) > 3 and looks_like_slug:
            links.append((text, full))
            seen.add(full)
        if len(links) >= max_links:
            break
    return links


def generic_scrape(portal_name, listing_url, limit=15):
    rows = []
    resp = safe_get(listing_url)
    if not resp:
        return rows
    soup = make_soup(resp)
    job_links = find_job_links(listing_url, soup, max_links=limit)
    if not job_links:
        print(f"  [info] {portal_name}: no obvious job links found on listing page "
              f"(likely JS-rendered or non-standard markup) - manual review needed")
        return rows
    skipped_junk = 0
    skipped_inactive = 0
    for title_guess, url in job_links:
        # Skip inactive/broken/tracking source links
        if not is_source_link_active(url):
            skipped_inactive += 1
            continue
        
        time.sleep(REQUEST_DELAY)
        page = safe_get(url)
        if not page:
            continue
        page_soup = make_soup(page)

        # Prefer structured schema.org JobPosting data when the page has it -
        # far more reliable than scraping visible text for company/location/pay.
        jd = extract_jsonld_jobposting(page_soup)
        if jd:
            fields = parse_jsonld_fields(jd)
            title = fields["title"] or title_guess
            if is_junk_title(title, portal_name):
                skipped_junk += 1
                continue
            raw_text = fields["description"] or page_soup.get_text("\n")
            row = make_row(portal_name, title, fields["company"], url, raw_text, fields["location"])
            if not row:
                skipped_junk += 1
                continue
            if fields["pay"]:
                row["Pay"] = fields["pay"]
            rows.append(row)
            continue

        # Fall back to a plain-text heuristic scrape
        h1 = page_soup.find("h1")
        title = h1.get_text(strip=True) if h1 and h1.get_text(strip=True) else title_guess
        if is_junk_title(title, portal_name):
            skipped_junk += 1
            continue
        main = page_soup.find("main") or page_soup.find("article") or page_soup.body
        raw_text = main.get_text("\n") if main else page_soup.get_text("\n")
        if len(clean_text(raw_text)) < 80:
            # too little content to be a real job posting page - probably
            # landed on a redirect/interstitial/login page instead
            skipped_junk += 1
            continue
        row = make_row(portal_name, title, "", url, raw_text)
        if row:
            rows.append(row)
        else:
            skipped_junk += 1
    if skipped_junk:
        print(f"  [info] {portal_name}: skipped {skipped_junk} link(s) that looked like "
              f"nav/UI noise rather than real job postings")
    if skipped_inactive:
        print(f"  [info] {portal_name}: skipped {skipped_inactive} inactive/broken/tracking source link(s)")
    return rows


# --------------------------------------------------------------------------
# Greenhouse ATS aggregator
# --------------------------------------------------------------------------

# Verified working company slugs on Greenhouse public API.
# Add/remove slugs here or set GREENHOUSE_COMPANY_SLUGS env var
# (comma-separated) to override at runtime.
GREENHOUSE_DEFAULT_SLUGS = [
    # Original verified slugs
    "stripe", "airbnb", "figma", "discord", "dropbox", "twilio",
    "coinbase", "robinhood", "brex", "gusto", "lattice", "asana",
    "datadog", "mongodb", "elastic", "cloudflare", "fastly", "vercel",
    "netlify", "airtable", "mixpanel", "amplitude", "pagerduty",
    "okta", "twitch", "anthropic",
    # Newly verified slugs
    "lyft", "pinterest", "reddit", "block", "affirm", "chime",
    "carta", "checkr", "blend", "samsara", "databricks", "webflow",
    "gitlab", "jetbrains", "postman", "planetscale", "hightouch",
    "mercury", "found", "remote",
]


def scrape_greenhouse(limit=None):
    """Greenhouse ATS public API — no key required.

    Queries each company slug in GREENHOUSE_DEFAULT_SLUGS (or the
    GREENHOUSE_COMPANY_SLUGS env var override) and collects jobs whose
    updated_at falls inside the current date window.

    API: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
    All listings are returned in a single response per company (no pagination).
    Direct employer application links — no middleman.
    """
    win_start, win_end = get_date_window()
    print(f"  [Greenhouse] date window: {win_start} → {win_end}")

    # Allow runtime override via env var
    env_slugs = os.environ.get("GREENHOUSE_COMPANY_SLUGS", "").strip()
    slugs = [s.strip() for s in env_slugs.split(",") if s.strip()] if env_slugs else GREENHOUSE_DEFAULT_SLUGS

    rows = []
    for slug in slugs:
        resp = safe_get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            params={"content": "true"},  # include job description
        )
        if not resp:
            print(f"    [Greenhouse/{slug}] no response — skipping")
            continue
        try:
            data = resp.json()
        except Exception:
            continue

        jobs = data.get("jobs", [])
        count_in_window = 0
        for job in jobs:
            pub_date = job.get("updated_at") or job.get("first_published") or ""
            in_win = is_in_window(pub_date)
            if not in_win:
                continue

            title    = job.get("title", "")
            company  = job.get("company_name", "") or slug.replace("-", " ").title()
            job_url  = job.get("absolute_url", "")
            location = (job.get("location") or {}).get("name", "")
            raw_desc = job.get("content", "") or ""   # only present with ?content=true
            # Greenhouse returns description as HTML — strip tags to plain text
            desc = BeautifulSoup(raw_desc, "html.parser").get_text("\n") if raw_desc else ""

            row = make_row("Greenhouse", title, company, job_url, desc, location)
            if not row:
                continue
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)
            count_in_window += 1

        if count_in_window:
            print(f"    [Greenhouse/{slug}] {count_in_window} job(s) in window (of {len(jobs)} total)")
        time.sleep(REQUEST_DELAY)

    print(f"  [Greenhouse] {len(rows)} total job(s) in window across {len(slugs)} companies")
    return rows


# --------------------------------------------------------------------------
# Template-based ATS aggregators
# --------------------------------------------------------------------------

def _env_slug_list(env_name, defaults):
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return defaults
    return [s.strip() for s in raw.split(",") if s.strip()]


def _plain_html(html):
    return BeautifulSoup(html or "", "html.parser").get_text("\n") if html else ""


def _location_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [
            value.get("name"), value.get("city"), value.get("region"),
            value.get("state"), value.get("country"), value.get("countryCode"),
            value.get("locationStr"), value.get("fullLocation"),
        ]
        return ", ".join(one_line(str(p)) for p in parts if p)
    if isinstance(value, list):
        for v in value:
            name = _location_name(v)
            if name:
                return name
        return ""
    return ""


def _date_in_current_window(value):
    in_win = is_in_window(value)
    return in_win is not False


def _compensation_text(value):
    if not value:
        return ""
    if isinstance(value, str):
        return one_line(value)
    if isinstance(value, dict):
        for key in (
            "compensationTierSummary", "compTierSummary", "summary",
            "description", "text", "display",
        ):
            if value.get(key):
                return one_line(str(value[key]))
        min_value = value.get("minValue") or value.get("min") or value.get("minimum")
        max_value = value.get("maxValue") or value.get("max") or value.get("maximum")
        currency = value.get("currency") or value.get("currencyCode") or ""
        interval = value.get("interval") or value.get("unitText") or ""
        if min_value and max_value:
            return one_line(f"{currency} {min_value}-{max_value} {interval}")
        if min_value:
            return one_line(f"{currency} {min_value}+ {interval}")
    if isinstance(value, list):
        parts = [_compensation_text(v) for v in value]
        return "; ".join(p for p in parts if p)
    return ""


def _text_from_nested(value):
    """Extract human-readable text from nested ATS JSON without dumping raw JSON."""
    if not value:
        return ""
    if isinstance(value, str):
        return _plain_html(value)
    if isinstance(value, dict):
        parts = []
        for key in (
            "text", "html", "value", "description", "jobDescription",
            "qualifications", "responsibilities", "requirements", "benefits",
        ):
            if key in value:
                text = _text_from_nested(value.get(key))
                if text:
                    parts.append(text)
        sections = value.get("sections")
        if sections:
            text = _text_from_nested(sections)
            if text:
                parts.append(text)
        return "\n\n".join(dict.fromkeys(p.strip() for p in parts if p.strip()))
    if isinstance(value, list):
        parts = [_text_from_nested(item) for item in value]
        return "\n\n".join(dict.fromkeys(p.strip() for p in parts if p.strip()))
    return ""


def _sr_job_ad_section_text(job_ad, section_name):
    """Pull plain text from a SmartRecruiters jobAd.sections entry if present."""
    if not isinstance(job_ad, dict):
        return ""
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return ""
    section = sections.get(section_name)
    if not isinstance(section, dict):
        return ""
    text = section.get("text")
    return clean_text(_plain_html(text)) if text else ""


def _smartrecruiters_description(detail):
    """Build a clean SmartRecruiters description from the detail response's
    known text-bearing fields.

    The jobDescription and qualifications sections are both plain text and are
    merged into a single description (matching how every other portal exports
    its posting text), with the qualification text appended after the main
    body. Only known text-bearing fields are read, so raw JSON/API payload
    objects never reach the description.
    """
    job_ad = detail.get("jobAd") if isinstance(detail, dict) else None
    description = _sr_job_ad_section_text(job_ad, "jobDescription")
    qualifications = _sr_job_ad_section_text(job_ad, "qualifications")

    merged_text = clean_text("\n\n".join(part for part in (description, qualifications) if part))
    if merged_text:
        return merged_text

    # Fallbacks for legacy payload shapes that lack the nested jobAd.sections
    # structure but still carry plain text under known keys.
    candidates = [
        _text_from_nested(detail.get("description")),
        _text_from_nested(detail.get("qualifications")),
        _text_from_nested(detail.get("responsibilities")),
        _text_from_nested(detail.get("requirements")),
    ]
    return clean_text("\n\n".join(p for p in candidates if p))


LEVER_DEFAULT_SLUGS = [
    "integrate", "reply", "Flex", "askfavor", "levelai",
]


def scrape_lever(limit=None):
    """Lever public postings API across configured company slugs."""
    rows = []
    slugs = _env_slug_list("LEVER_COMPANY_SLUGS", LEVER_DEFAULT_SLUGS)
    win_start, win_end = get_date_window()
    print(f"  [Lever] date window: {win_start} → {win_end}")

    for slug in slugs:
        resp = safe_get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        if not resp:
            continue
        data = safe_json(resp)
        if not isinstance(data, list):
            continue
        count_in_window = 0
        for item in data:
            pub_date = item.get("createdAt") or item.get("updatedAt")
            if pub_date and not _date_in_current_window(pub_date / 1000 if isinstance(pub_date, (int, float)) else pub_date):
                continue
            categories = item.get("categories") or {}
            list_text = "\n\n".join(
                f"{part.get('text', '')}\n{part.get('content', '')}"
                for part in item.get("lists", []) if isinstance(part, dict)
            )
            desc = "\n\n".join(
                p for p in [
                    item.get("descriptionPlain") or _plain_html(item.get("description", "")),
                    item.get("additionalPlain") or _plain_html(item.get("additional", "")),
                    _plain_html(list_text),
                ] if p
            )
            location = categories.get("location", "") if isinstance(categories, dict) else ""
            company = item.get("company") or slug.replace("-", " ").title()
            row = make_row(
                "Lever",
                item.get("text", ""),
                company,
                item.get("hostedUrl") or item.get("applyUrl") or "",
                desc,
                location,
            )
            if not row:
                continue
            if pub_date:
                row["Scraped At"] = str(parse_date(pub_date / 1000 if isinstance(pub_date, (int, float)) else pub_date) or pub_date)
            rows.append(row)
            count_in_window += 1
        if count_in_window:
            print(f"    [Lever/{slug}] {count_in_window} job(s) in window")
        time.sleep(REQUEST_DELAY)
    return rows


ASHBY_DEFAULT_SLUGS = [
    "openai", "cursor", "perplexity", "linear", "writer",
    "ramp", "cointracker",
]


def scrape_ashby(limit=None):
    """Ashby public job-board API across configured company slugs."""
    rows = []
    slugs = _env_slug_list("ASHBY_COMPANY_SLUGS", ASHBY_DEFAULT_SLUGS)
    win_start, win_end = get_date_window()
    print(f"  [Ashby] date window: {win_start} → {win_end}")

    for slug in slugs:
        resp = safe_get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            params={"includeCompensation": "true"},
        )
        if not resp:
            continue
        data = safe_json(resp)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        count_in_window = 0
        for item in jobs:
            pub_date = (
                item.get("publishedAt") or item.get("publishedDate")
                or item.get("updatedAt") or item.get("createdAt")
            )
            if pub_date and not _date_in_current_window(pub_date):
                continue
            desc = (
                item.get("descriptionPlain")
                or _plain_html(item.get("descriptionHtml") or item.get("description") or "")
            )
            row = make_row(
                "Ashby",
                item.get("title", ""),
                item.get("companyName") or slug.replace("-", " ").title(),
                item.get("jobUrl") or item.get("applyUrl") or item.get("url") or "",
                desc,
                _location_name(item.get("location") or item.get("locations")),
            )
            if not row:
                continue
            pay = _compensation_text(
                item.get("compensation")
                or item.get("compensationTierSummary")
                or item.get("compensationTiers")
            )
            if pay:
                row["Pay"] = pay
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)
            count_in_window += 1
        if count_in_window:
            print(f"    [Ashby/{slug}] {count_in_window} job(s) in window")
        time.sleep(REQUEST_DELAY)
    return rows


SMARTRECRUITERS_DEFAULT_SLUGS = [
    "Visa", "BoschGroup", "Square", "PublicisGroupe", "ServiceNow",
    "WesternDigital", "NielsenIQ", "Wolt", "Bosch", "Avalara",
]


def scrape_smartrecruiters(limit=None):
    """SmartRecruiters public company postings API."""
    rows = []
    slugs = _env_slug_list("SMARTRECRUITERS_COMPANY_SLUGS", SMARTRECRUITERS_DEFAULT_SLUGS)
    win_start, win_end = get_date_window()
    print(f"  [SmartRecruiters] date window: {win_start} → {win_end}")

    for slug in slugs:
        offset = 0
        count_in_window = 0
        while True:
            resp = safe_get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                params={"limit": 100, "offset": offset},
            )
            if not resp:
                break
            data = safe_json(resp)
            items = data.get("content") or data.get("postings") or data.get("jobs") or []
            if not items:
                break
            for item in items:
                pub_date = item.get("releasedDate") or item.get("createdOn") or item.get("updatedOn")
                if pub_date and not _date_in_current_window(pub_date):
                    continue
                detail = item
                job_id = item.get("id")
                if job_id:
                    detail_resp = safe_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}")
                    detail_json = safe_json(detail_resp) if detail_resp else None
                    if isinstance(detail_json, dict):
                        detail = {**item, **detail_json}
                company_data = detail.get("company") or {}
                company = company_data.get("name", "") if isinstance(company_data, dict) else str(company_data or "")
                location = _location_name(detail.get("location"))
                desc = _smartrecruiters_description(detail)
                # SmartRecruiters API detail responses expose:
                # - postingUrl: public human-facing job page (jobs.smartrecruiters.com)
                # - applyUrl:   same page with an application query suffix (?oga=true)
                # - ref:        raw API/JSON endpoint for the posting
                # Export the public posting page as the source link; applyUrl is
                # only a fallback when postingUrl is absent. The API/JSON endpoint
                # is never exported as a source link.
                source_url = (
                    detail.get("postingUrl")
                    or detail.get("applyUrl")
                    or ""
                )
                row = make_row(
                    "SmartRecruiters",
                    detail.get("name", ""),
                    company or slug,
                    source_url,
                    desc or detail.get("name", ""),
                    location,
                    fetch_detail=False,
                )
                if not row:
                    continue
                if pub_date:
                    row["Scraped At"] = pub_date
                rows.append(row)
                count_in_window += 1
            if len(items) < 100:
                break
            offset += 100
            time.sleep(REQUEST_DELAY)
        if count_in_window:
            print(f"    [SmartRecruiters/{slug}] {count_in_window} job(s) in window")
        time.sleep(REQUEST_DELAY)
    return rows


WORKABLE_DEFAULT_SLUGS = [
    "moonbug-entertainment", "dsn", "bandit-running",
    "gac-enterprises-llc", "metova",
]


def scrape_workable(limit=None):
    """Workable widget API across configured account slugs."""
    rows = []
    slugs = _env_slug_list("WORKABLE_COMPANY_SLUGS", WORKABLE_DEFAULT_SLUGS)
    win_start, win_end = get_date_window()
    print(f"  [Workable] date window: {win_start} → {win_end}")

    for slug in slugs:
        data = None
        for suffix in ("", "/jobs"):
            resp = safe_get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}{suffix}")
            if resp:
                data = safe_json(resp)
                if data:
                    break
        if not isinstance(data, dict):
            continue
        company = data.get("name") or data.get("company_name") or slug.replace("-", " ").title()
        jobs = data.get("jobs") or data.get("results") or data.get("postings") or []
        count_in_window = 0
        for item in jobs:
            pub_date = item.get("published_on") or item.get("created_at") or item.get("updated_at")
            if pub_date and not _date_in_current_window(pub_date):
                continue
            desc = (
                item.get("description")
                or item.get("description_html")
                or item.get("full_description")
                or item.get("department")
                or item.get("function")
                or item.get("industry")
                or item.get("shortcode")
                or ""
            )
            row = make_row(
                "Workable",
                item.get("title", ""),
                item.get("company_name") or company,
                item.get("url") or item.get("shortlink") or item.get("application_url") or "",
                _plain_html(desc),
                _location_name(item.get("location") or item.get("locations")),
            )
            if not row:
                continue
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)
            count_in_window += 1
        if count_in_window:
            print(f"    [Workable/{slug}] {count_in_window} job(s) in window")
        time.sleep(REQUEST_DELAY)
    return rows


RECRUITEE_DEFAULT_SLUGS = [
    "bunq", "personio",
]


def scrape_recruitee(limit=None):
    """Recruitee public offers API across configured company subdomains."""
    rows = []
    slugs = _env_slug_list("RECRUITEE_COMPANY_SLUGS", RECRUITEE_DEFAULT_SLUGS)
    win_start, win_end = get_date_window()
    print(f"  [Recruitee] date window: {win_start} → {win_end}")

    for slug in slugs:
        resp = safe_get(f"https://{slug}.recruitee.com/api/offers/")
        if not resp:
            continue
        data = safe_json(resp)
        jobs = data.get("offers", []) if isinstance(data, dict) else []
        count_in_window = 0
        for item in jobs:
            pub_date = item.get("published_at") or item.get("created_at") or item.get("updated_at")
            if pub_date and not _date_in_current_window(pub_date):
                continue
            company = item.get("company_name") or slug.replace("-", " ").title()
            row = make_row(
                "Recruitee",
                item.get("title", ""),
                company,
                item.get("careers_url") or item.get("url") or "",
                _plain_html(item.get("description") or item.get("body") or ""),
                _location_name(item.get("location")),
            )
            if not row:
                continue
            if pub_date:
                row["Scraped At"] = pub_date
            rows.append(row)
            count_in_window += 1
        if count_in_window:
            print(f"    [Recruitee/{slug}] {count_in_window} job(s) in window")
        time.sleep(REQUEST_DELAY)
    return rows


JOBVITE_DEFAULT_SLUGS = [
    "clearwateranalytics", "zillow", "logitech", "appliedsystems",
    "trimble", "proofpoint", "avetta", "navisite", "blackline", "calix",
]


def scrape_jobvite(limit=None):
    """Jobvite company career pages across configured company slugs."""
    rows = []
    slugs = _env_slug_list("JOBVITE_COMPANY_SLUGS", JOBVITE_DEFAULT_SLUGS)
    for slug in slugs:
        before = len(rows)
        rows.extend(generic_scrape("Jobvite", f"https://jobs.jobvite.com/{slug}/jobs", limit=25))
        found = len(rows) - before
        if found:
            print(f"    [Jobvite/{slug}] {found} job(s)")
        time.sleep(REQUEST_DELAY)
    return rows


# --------------------------------------------------------------------------
# Portal registry / dispatch
# --------------------------------------------------------------------------

SPECIAL_SCRAPERS = {
    "Remote OK": scrape_remoteok,
    "Remotive": scrape_remotive,
    "We Work Remotely": scrape_weworkremotely,
    "Working Nomads": scrape_workingnomads,
    "NoDesk": scrape_nodesk,
    "Hacker News Who's Hiring": scrape_hn_whoshiring,
    "Adzuna USA": scrape_adzuna,
    "USAJOBS": scrape_usajobs,
    # Previous new portals
    "The Muse": scrape_the_muse,
    "Jobicy": scrape_jobicy,
    "RemoteJobs.org": scrape_remotejobs_org,
    "Reed UK": scrape_reed_uk,
    "CareerJet": scrape_careerjet,
    "Arbeitnow": scrape_arbeitnow,
    "Arbeitnow UK": scrape_arbeitnow_uk,
    "Findwork": scrape_findwork,
    # New portals
    "Himalayas": scrape_himalayas,
    "Jooble USA": scrape_jooble,
    "API Ninjas Jobs": scrape_api_ninjas,
    "Remote1stJobs": scrape_remote1stjobs,
    "Greenhouse": scrape_greenhouse,
    "Lever": scrape_lever,
    "Ashby": scrape_ashby,
    "SmartRecruiters": scrape_smartrecruiters,
    "Workable": scrape_workable,
    "Recruitee": scrape_recruitee,
    "Jobvite": scrape_jobvite,
}


def load_portals(csv_path):
    portals = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            portals.append(row)
    return portals


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def post_rows_to_backend(rows, url=None, api_key=None, batch_size=None):
    """POST scraped rows to a backend API as JSON batches.

    Body shape: {"source": "smart_scraper_jobs", "jobs": [ {FIELDNAMES...}, ... ]}
    Returns (batches_sent, batches_failed).
    """
    url = url or BACKEND_API_URL
    if not url:
        print("  [skip] BACKEND_API_URL not set - skipping backend upload "
              "(rows were still written to the output CSV)")
        return 0, 0

    api_key = api_key if api_key is not None else BACKEND_API_KEY
    batch_size = batch_size or BACKEND_API_BATCH_SIZE
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    sent, failed = 0, 0
    batches = list(_chunk(rows, batch_size))
    for i, batch in enumerate(batches, start=1):
        payload = {"source": "smart_scraper_jobs", "jobs": batch}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=BACKEND_API_TIMEOUT)
            if resp.ok:
                sent += 1
                print(f"  [backend] batch {i}/{len(batches)} ({len(batch)} rows) -> {resp.status_code}")
            else:
                failed += 1
                print(f"  [backend][warn] batch {i}/{len(batches)} failed: "
                      f"{resp.status_code} {resp.text[:300]}", file=sys.stderr)
        except requests.RequestException as e:
            failed += 1
            print(f"  [backend][warn] batch {i}/{len(batches)} request error: {e}", file=sys.stderr)

    print(f"  [backend] {sent}/{len(batches)} batch(es) posted successfully "
          f"({sum(len(b) for b in batches)} rows total)")
    return sent, failed


# --------------------------------------------------------------------------
# Admin job-creation API (single job per POST, different field names)
# --------------------------------------------------------------------------

_ADMIN_SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead)\b", re.IGNORECASE)
_ADMIN_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry[- ]level)\b", re.IGNORECASE)
_ADMIN_INTERN_RE = re.compile(r"\b(intern(?:ship)?)\b", re.IGNORECASE)
_ADMIN_SALARY_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_ADMIN_HOURLY_PAY_RE = re.compile(
    r"\b(/hr|/hour|per hour|hourly|an hour|hr\b)\b", re.IGNORECASE
)
_ADMIN_ANNUAL_PAY_RE = re.compile(
    r"\b(/yr|/year|per year|annually|annual|yearly|salary)\b", re.IGNORECASE
)
_ADMIN_CURRENCY_MARKERS = [
    ("USD", re.compile(r"(\$|usd|us dollars?)\b", re.IGNORECASE)),
    ("EUR", re.compile(r"(€|eur|euros?)\b", re.IGNORECASE)),
    ("GBP", re.compile(r"(£|gbp|pounds?)\b", re.IGNORECASE)),
]
_ADMIN_EMPLOYMENT_TYPE_RE = re.compile(
    r"\b(full[- ]time|part[- ]time|contract|freelance|internship|temporary)\b", re.IGNORECASE
)
_ADMIN_EMPLOYMENT_TYPE_MAP = {
    "full-time": "Full-time", "full time": "Full-time",
    "part-time": "Part-time", "part time": "Part-time",
    "contract": "Contract", "freelance": "Freelance",
    "internship": "Internship", "temporary": "Temporary",
}


def _admin_infer_seniority(title):
    if _ADMIN_SENIOR_RE.search(title):
        return "Senior"
    if _ADMIN_INTERN_RE.search(title):
        return "Intern"
    if _ADMIN_JUNIOR_RE.search(title):
        return "Junior"
    return "Mid"


def _admin_infer_employment_type(text):
    match = _ADMIN_EMPLOYMENT_TYPE_RE.search(text)
    if not match:
        return "Full-time"
    return _ADMIN_EMPLOYMENT_TYPE_MAP.get(match.group(0).lower(), "Full-time")


def _admin_salary_details(pay):
    """Return salary min/max plus currency and unit for CSV/import.

    The internal unit is `hour`, `year`, or empty when no salary was found. If a
    source omits the unit, use a conservative value-based guess: small ranges
    are usually hourly wages, larger ranges are annual salaries.
    """
    if not pay:
        return 0, 0, "", ""
    numbers = [float(n.replace(",", "")) for n in _ADMIN_SALARY_NUM_RE.findall(pay)]
    if not numbers:
        return 0, 0, "", ""
    lower_pay = pay.lower()
    if "k" in lower_pay:
        numbers = [n * 1000 for n in numbers]
    values = [int(n) for n in numbers]
    salary_min, salary_max = min(values), max(values)

    currency = ""
    for code, pattern in _ADMIN_CURRENCY_MARKERS:
        if pattern.search(pay):
            currency = code
            break
    if not currency and salary_max:
        currency = "USD"

    if _ADMIN_HOURLY_PAY_RE.search(pay):
        unit = "hour"
    elif _ADMIN_ANNUAL_PAY_RE.search(pay):
        unit = "year"
    elif salary_max and salary_max < 1000:
        unit = "hour"
    else:
        unit = "year"
    return salary_min, salary_max, currency, unit


def _admin_salary_range(pay):
    salary_min, salary_max, _, _ = _admin_salary_details(pay)
    return salary_min, salary_max


def _admin_build_description(row):
    """Assemble the full job description from all scraped sections.

    Produces a rich, candidate-readable description by:
      1. Optionally opening with a role/company context sentence when
         ADD_DESCRIPTION_INTRO=1.
      2. Rendering every scraped section with a clear heading so candidates
         can instantly scan what the role involves, what's required, and what
         perks are on offer.
      3. Optionally padding sparse postings (< 600 chars of body content) when
         ENRICH_SPARSE_DESCRIPTIONS=1.
      4. Descriptions are kept in full — no length cap is applied, so every
         scraped section is preserved end to end.
    """
    title = (row.get("Job Title") or "").strip()
    company = (row.get("Company") or "").strip()
    location = (row.get("Location") or "").strip()
    pay = (row.get("Pay") or "").strip()

    # ── 1. Build an intro context line ──────────────────────────────────────
    intro_parts = []
    if ADD_DESCRIPTION_INTRO:
        if title and company:
            intro_parts.append(f"{company} is hiring for the role of {title}.")
        elif title:
            intro_parts.append(f"We are hiring for the role of {title}.")
        if location:
            intro_parts.append(f"Location: {location}.")
        if pay:
            intro_parts.append(f"Compensation: {pay}.")
    intro = " ".join(intro_parts)

    # ── 2. Collect all scraped sections ─────────────────────────────────────
    # Run clean_text() on each section value as a safety net — some portals
    # return HTML in their description fields (e.g. Greenhouse content field
    # may contain inline <span style="..."> fragments). clean_text strips any
    # remaining HTML tags and normalizes whitespace.
    raw_sections = [
        ("About the Role",    row.get("About the job") or row.get("Summary") or ""),
        ("Responsibilities",  row.get("Responsibilities") or ""),
        ("Requirements",      row.get("Requirements") or ""),
        ("Benefits",          row.get("Benefits") or ""),
    ]
    sections = [
        (label, clean_text(text).strip())
        for label, text in raw_sections
        if text and text.strip()
    ]

    def _join(intro_line, parts):
        chunks = []
        if intro_line:
            chunks.append(intro_line)
        for label, text in parts:
            chunks.append(f"{label}:\n{text}")
        return "\n\n".join(chunks).strip()

    body_len = sum(len(text) for _, text in sections)

    # Optional sparse-posting enrichment. Keep disabled when the CSV should
    # contain only text fetched from the portal/API.
    if ENRICH_SPARSE_DESCRIPTIONS and body_len < 600:
        combined_text = _job_text(row)
        skills = extract_required_skills(combined_text)
        extra_lines = []
        if skills:
            extra_lines.append("Key skills and technologies relevant to this role include: "
                                + ", ".join(skills) + ".")
        # Add role-based expectation lines from the title
        title_lower = (title or "").lower()
        if any(w in title_lower for w in ("engineer", "developer", "architect", "devops")):
            extra_lines.append(
                "The successful candidate will design, build, and maintain software systems, "
                "collaborate with cross-functional teams, and deliver high-quality technical solutions."
            )
        elif any(w in title_lower for w in ("analyst", "scientist", "data", "research")):
            extra_lines.append(
                "The role involves analysing data, generating insights, and communicating findings "
                "to stakeholders across the organisation."
            )
        elif any(w in title_lower for w in ("manager", "director", "lead", "head", "vp", "chief")):
            extra_lines.append(
                "You will lead a team, define strategy, manage stakeholders, and drive results "
                "aligned with company objectives."
            )
        elif any(w in title_lower for w in ("sales", "account", "business development")):
            extra_lines.append(
                "Responsibilities include prospecting, building client relationships, meeting "
                "revenue targets, and collaborating with marketing and product teams."
            )
        elif any(w in title_lower for w in ("design", "ux", "ui", "creative")):
            extra_lines.append(
                "You will create user-centered designs, collaborate with product and engineering, "
                "and iterate based on user feedback and data."
            )
        else:
            extra_lines.append(
                "The candidate will bring relevant expertise to contribute meaningfully to the team's "
                "goals and support the organisation's mission."
            )

        if extra_lines:
            pad_text = " ".join(extra_lines)
            # Append to the "About the Role" section if it exists, otherwise add new section
            if sections and sections[0][0] == "About the Role":
                sections[0] = ("About the Role", sections[0][1] + "\n\n" + pad_text)
            else:
                sections.insert(0, ("About the Role", pad_text))

    # ── 4. Full description — never capped ────────────────────────────────────
    return _join(intro, sections)


def row_to_admin_job_payload(row):
    """Maps one scraped row (see FIELDNAMES) onto the admin API's job-creation
    body shape: {company, role, platform, location, remote, salaryMin, salaryMax,
    salaryCurrency, seniority, visaSponsorship, employmentType, description,
    requiredSkills, tags, sourceLink, active}.

    visaSponsorship and active are fixed defaults (False / True) rather than
    inferred from the posting text — set by policy, not detection."""
    title = (row.get("Job Title") or "").strip()
    company = (row.get("Company") or "").strip()
    if not title or not company:
        return None

    location = normalize_location(row.get("Location") or "")
    description = _admin_build_description(row)
    salary_min, salary_max, salary_currency, _salary_unit = _admin_salary_details(row.get("Pay", ""))

    combined_text = f"{title} {description}"

    # requiredSkills is populated only from skills explicitly present in the
    # posting text or in portal-provided structured tags. No role-title guessing
    # and no hardcoded fallback skills are injected.
    required_skills = extract_required_skills(row.get("Portal Skills") or "") \
        or extract_required_skills(combined_text)
    # tags: resume-matching keywords from the description — no overlap with requiredSkills, min 7 max 15
    # Pass the assembled description as extra_text so padded/enriched content feeds tag extraction,
    # but strip the intro context line (first line) which is for human readability, not keyword matching.
    desc_lines = description.split("\n", 1)
    desc_body = desc_lines[1].strip() if len(desc_lines) > 1 else description
    tags = extract_resume_tags(row, required_skills=required_skills, min_tags=7, max_tags=15,
                               extra_text=desc_body)

    return {
        "company": company,
        "role": title,
        "platform": row.get("Portal", ""),
        "location": location,
        "remote": "remote" in location.lower(),
        "salaryMin": salary_min,
        "salaryMax": salary_max,
        "salaryCurrency": salary_currency,
        "seniority": _admin_infer_seniority(title),
        "visaSponsorship": False,
        "employmentType": _admin_infer_employment_type(combined_text),
        "description": description or title,
        "requiredSkills": required_skills,
        "tags": tags,
        "sourceLink": row.get("URL", ""),
        "active": True,
    }


def row_to_requested_format(row):
    payload = row_to_admin_job_payload(row)
    if payload is None:
        return None
    payload["visaSponsorship"] = False
    payload["active"] = True
    return payload


def _requested_csv_value(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, (dict, bool)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def write_requested_format_csv(rows, out_path):
    written = 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=REQUESTED_FIELDNAMES, quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for row in rows:
            payload, _reason = output_payload_for_row(row)
            if payload is None:
                continue
            writer.writerow({
                field: _requested_csv_value(payload.get(field, ""))
                for field in REQUESTED_FIELDNAMES
            })
            written += 1
    return written


def write_requested_format_json(rows, out_path):
    payloads = []
    for row in rows:
        payload, _reason = output_payload_for_row(row)
        if payload is not None:
            payloads.append(payload)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payloads, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Job deduplication
#
# Ensures no similar jobs remain in the output across several signals:
#   - URL                (same posting / re-scrape, normalized)
#   - role + company     (same job cross-posted to different portals)
#   - title / role       (near-identical wording)
#   - company + location (same employer + office, e.g. slightly reworded titles)
#
# Dedup is split into two passes:
#   1. EXACT  – identical normalized URL, or identical (company, title) key.
#   2. FUZZY  – token-overlap (Jaccard) similarity over normalized title,
#              company, and location; a posting is dropped when it is similar
#              enough to an already-kept row. Requires company (or URL/title)
#              overlap so different roles at the same company are not merged.
# ---------------------------------------------------------------------------

_DEDUP_LOW_WORDS = {
    "a", "an", "the", "and", "or", "of", "for", "in", "on", "at", "to", "with",
    "job", "role", "position", "company", "inc", "llc", "ltd", "co", "group",
}

_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)


def _dedup_text(value):
    """Normalize a text field for use inside a dedup KEY (never for display).

    The same job is frequently re-posted with cosmetic differences in the title:
        "Social Worker- HUD/VASH"          vs "Social Worker - HUD/VASH"
        "Security Specialist-Direct Hire"  vs "SECURITY SPECIALIST - DIRECT HIRE"
        "Nurse (Deputy Group Director)"    vs "Nurse (Deputy Group Director )"
    Comparing those raw strings produced different keys for the same posting, so
    a re-posted requisition slipped through cross-run dedup. Lowercasing,
    collapsing each punctuation run to a single space, and squeezing whitespace
    makes those variants equal while still keeping genuinely different titles
    apart, because token ORDER is preserved ("Nurse A B" != "B Nurse A").
    """
    s = unicodedata.normalize("NFKC", str(value or "")).lower()
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_key(text, drop_stopwords=True):
    """Lowercase, strip punctuation/whitespace, collapse to a token set.

    Returns a set of significant tokens used for comparisons. Removing common
    filler words (the, of, for, inc, llc, etc.) makes two phrasings of the same
    title/company compare as equal even when minor filler words differ.
    """
    s = _PUNCT_RE.sub(" ", one_line(str(text or "")))
    s = re.sub(r"\s+", " ", s).strip().lower()
    tokens = set(s.split())
    if drop_stopwords:
        tokens = {t for t in tokens if t and t not in _DEDUP_LOW_WORDS}
    return tokens


def _norm_url(url):
    """Normalize a URL for dedup: lowercase, strip scheme/www, query, fragment,
    and trailing slash so the same posting reached via different mirrors/
    parameters collapses to one key."""
    if not url:
        return ""
    u = one_line(url).strip().lower()
    u = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", u)        # drop scheme
    u = re.sub(r"^/+", "", u)
    u = u.replace("www.", "", 1)                        # drop leading www.
    u = u.split("#")[0].split("?")[0]                   # drop fragment + query
    u = u.rstrip("/")
    return u


def _token_jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _rows_are_similar(row_a, row_b, title_threshold=0.8, company_threshold=0.7,
                      location_threshold=0.6):
    """Return True when two rows are 'the same/similar job'.

    Requires a strong company (or URL) match plus a title match, so distinct
    openings at one employer are kept. Location strengthens the match but is
    not required on its own (many postings omit location)."""
    comp_a = _norm_key(row_a.get("Company") or row_a.get("company"))
    comp_b = _norm_key(row_b.get("Company") or row_b.get("company"))
    title_a = _norm_key(row_a.get("Job Title") or row_a.get("role"))
    title_b = _norm_key(row_b.get("Job Title") or row_b.get("role"))

    # Same company + near-identical title => same job.
    company_ok = comp_a and comp_b and _token_jaccard(comp_a, comp_b) >= company_threshold
    if company_ok and _token_jaccard(title_a, title_b) >= title_threshold:
        return True

    # Same located role title even when company barely overlaps but location agrees.
    loc_a = _norm_key(row_a.get("Location") or row_a.get("location"))
    loc_b = _norm_key(row_b.get("Location") or row_b.get("location"))
    location_ok = loc_a and loc_b and _token_jaccard(loc_a, loc_b) >= location_threshold

    # Strong title overlap + company overlap (even weak) + matching location.
    title_sim = _token_jaccard(title_a, title_b)
    if title_sim >= title_threshold and company_ok and location_ok:
        return True

    return False


def deduplicate_rows(rows, fuzzy=True):
    """Deduplicate scraped rows by URL, role+company, and fuzzy similarity.

    Returns the list of unique rows, preserving first-seen order. When fuzzy
    is True, near-duplicate titles with the same company (or company+location)
    are also collapsed. Exact URL/company+title matches are always removed
    regardless of `fuzzy`.
    """
    urls_seen = set()
    keys_seen = set()
    kept = []                 # list of (original_index, row)
    kept_by_index = {}        # original_index -> row (for fuzzy comparison)

    for i, row in enumerate(rows):
        url = _norm_url(row.get("URL") or row.get("sourceLink"))
        company = (row.get("Company") or row.get("company") or "").strip().lower()
        title = (row.get("Job Title") or row.get("role") or "").strip().lower()
        ct_key = f"{company}::{title}" if company and title else ""

        # 1. Exact: same normalized URL.
        if url:
            if url in urls_seen:
                continue
            urls_seen.add(url)

        # 2. Exact: same (company, title).
        if ct_key:
            if ct_key in keys_seen:
                continue
            keys_seen.add(ct_key)

        # 3. Fuzzy: similar to an already-kept row.
        if fuzzy:
            if any(_rows_are_similar(row, kept_by_index[kept_i]) for kept_i, _ in kept):
                continue

        kept.append((i, row))
        kept_by_index[i] = row

    return [r for _, r in kept]


def _admin_dedup_keys(row):
    """Returns every key that identifies this row as a duplicate: the scraped
    URL (catches re-scraping the same posting; unprefixed, matching the format
    already stored by earlier runs) AND company+role (catches the same job
    cross-posted to multiple portals under different URLs). A row is a
    duplicate if ANY of its keys was already sent.

    Always returns at least one key — falls back to a hash of title+portal
    so jobs with no URL and no company are still tracked and never re-sent.
    """
    keys = []
    url = (row.get("URL") or "").strip().lower()
    if url:
        keys.append(url)

    company = (row.get("Company") or "").strip().lower()
    title = (row.get("Job Title") or "").strip().lower()
    if company and title:
        keys.append(f"{company}::{title}")

    # Fallback: if we still have no keys (no URL, no company, or no title),
    # use whatever fields we have so the job is never silently re-sent.
    if not keys:
        portal = (row.get("Portal") or "").strip().lower()
        fallback = f"{portal}::{company}::{title}".strip(":").lower()
        if fallback:
            keys.append(fallback)

    return keys


def _load_admin_sent_keys(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_admin_sent_keys(path, keys):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f)


def _load_scrape_seen(path=None):
    """Load the set of already-seen job keys from the cross-run dedup file."""
    path = path or SCRAPE_SEEN_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_scrape_seen(keys, path=None):
    """Persist the updated set of seen job keys to the cross-run dedup file."""
    path = path or SCRAPE_SEEN_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f)


def _scrape_seen_keys(row):
    """Return dedup keys for a row.

    Two separate key types:
    1. URL key   — exact same URL means same job regardless of other fields.
    2. Composite key — company + title + location all combined. A company can
       post different jobs (different titles) and those are allowed through.
       Only the exact same company + same job title + same location is a duplicate.

    A row is a duplicate only if its URL key matches OR its composite key matches.
    """
    keys = []

    # Key 1: normalized URL (same posting URL = definite duplicate)
    url = _norm_url(row.get("URL") or row.get("sourceLink") or "")
    if url:
        keys.append(f"url::{url}")

    # Key 2: composite of company + title + location (all three must match).
    # All three go through _dedup_text() so a re-post that only differs in
    # punctuation/spacing/case ("Social Worker- HUD/VASH" vs "Social Worker -
    # HUD/VASH") still hashes to the same key. Writer (_scrape_seen_keys) and
    # reader (_prior_run_keys_from_csvs) MUST normalize identically or dedup
    # silently stops matching.
    company  = _dedup_text(row.get("Company")  or row.get("company"))
    title    = _dedup_text(row.get("Job Title") or row.get("role"))
    location = _dedup_text(normalize_location(row.get("Location") or row.get("location")))
    if company and title:
        # Include location in key so same company+title in different locations
        # are treated as distinct jobs
        composite = f"{company}::{title}::{location}"
        keys.append(f"composite::{composite}")

    # Fallback: nothing useful at all — use portal+company+title so at least
    # something is tracked
    if not keys:
        portal = _dedup_text(row.get("Portal") or row.get("platform"))
        fallback = f"fallback::{portal}::{company}::{title}".strip(":: ")
        if fallback:
            keys.append(fallback)

    return keys


def _prior_run_keys_from_csvs(exclude_path=None):
    """Build url::/composite:: dedup keys from all previously written dated job
    CSVs in the scraper directory.

    This makes cross-run dedup STRICT and self-healing: even if
    scraped_seen.json is reset/lost, a run still refuses to emit any posting
    that an earlier date already captured — URL match first, then
    company + role + location. The current run's own output file is excluded so
    the same date can be re-scraped/overwritten.
    """
    keys = set()
    here = os.path.dirname(os.path.abspath(__file__))
    exclude = os.path.normpath(exclude_path or "")
    exclude_date = None
    if exclude_path:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(exclude_path))
        if m:
            exclude_date = m.group(1)
    candidates = []
    for name in os.listdir(here):
        if not name.lower().endswith(".csv"):
            continue
        if name.startswith(".~lock"):
            continue
        base = name.lower()
        if base == "jobs.csv" or base.startswith("jobs_") or base.startswith("jobs_output_"):
            candidates.append(name)
    for name in sorted(candidates):
        path = os.path.normpath(os.path.join(here, name))
        if path == exclude:
            continue
        if exclude_date:
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
            if date_m and date_m.group(1) == exclude_date:
                # Derived files for the date currently being scraped (e.g. the
                # updated *_clean.csv copy) must not count as prior runs.
                continue
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or "sourceLink" not in reader.fieldnames:
                    continue
                for row in reader:
                    url = _norm_url(row.get("sourceLink") or "")
                    company = _dedup_text(row.get("company"))
                    role = _dedup_text(row.get("role"))
                    location = _dedup_text(normalize_location(row.get("location")))
                    if url:
                        keys.add(f"url::{url}")
                    if company and role:
                        keys.add(f"composite::{company}::{role}::{location}")
        except Exception:
            # A malformed CSV must not break a scrape; skip it and keep going.
            continue
    return keys


def cross_run_dedup(rows, seen_keys):
    """Filter out rows already seen in a previous run.

    A row is rejected only if:
    - Its URL exactly matches a previously seen URL (URL check first), OR
    - Its company + job title + location all match a previously seen combination.

    Same company with a different job title → allowed through.
    Returns (new_rows, skipped_count).
    """
    new_rows = []
    skipped = 0
    for row in rows:
        keys = _scrape_seen_keys(row)
        if any(k in seen_keys for k in keys):
            skipped += 1
            continue
        new_rows.append(row)
    return new_rows, skipped


def post_rows_to_admin_api(rows, url=None, token=None, timeout=None, state_path=None):
    """POSTs each scraped row individually to the admin job-creation API.

    Unlike post_rows_to_backend (which batches rows into the internal AI
    engine's /jobs/ingest shape), this API takes one job object per request,
    authenticated via the x-jobs-key header, and has no known server-side
    dedup, so a local record of already-sent jobs (state_path) is used to
    skip repeats across runs. Returns (jobs_sent, jobs_skipped, jobs_failed).
    """
    url = url or ADMIN_API_URL
    if not url:
        print("  [skip] ADMIN_API_URL not set - skipping admin API upload "
              "(rows were still written to the output CSV)")
        return 0, 0, 0

    token = token if token is not None else ADMIN_API_TOKEN
    timeout = timeout or ADMIN_API_TIMEOUT
    state_path = state_path or ADMIN_API_STATE_PATH
    headers = {"Content-Type": "application/json"}
    if token:
        headers["x-jobs-key"] = token

    sent_keys = _load_admin_sent_keys(state_path)
    sent, skipped, failed = 0, 0, 0
    for row in rows:
        keys = _admin_dedup_keys(row)
        if any(k in sent_keys for k in keys):
            skipped += 1
            continue
        payload = row_to_admin_job_payload(row)
        if payload is None:
            continue
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.ok:
                sent += 1
                # Store ALL possible dedup keys for this job so future runs
                # can match it regardless of which fields are present.
                # Explicitly build every key variant rather than relying solely
                # on _admin_dedup_keys output (which may omit URL or CR key
                # if a field was blank at scrape time but present later).
                all_keys = set(keys)
                raw_url = (row.get("URL") or "").strip().lower()
                if raw_url:
                    all_keys.add(raw_url)
                company = (payload.get("company") or "").strip().lower()
                role = (payload.get("role") or "").strip().lower()
                if company and role:
                    all_keys.add(f"{company}::{role}")
                sent_keys.update(all_keys)
                _save_admin_sent_keys(state_path, sent_keys)
                print(f"  [admin-api][ok] {payload['role']!r} @ {payload['company']!r} "
                      f"-> {resp.status_code} {resp.text[:300]}")
            else:
                failed += 1
                print(f"  [admin-api][warn] {payload['role']!r} @ {payload['company']!r} "
                      f"failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        except requests.RequestException as e:
            failed += 1
            print(f"  [admin-api][warn] {payload['role']!r} request error: {e}", file=sys.stderr)
        time.sleep(REQUEST_DELAY)

    print(f"  [admin-api] {sent} job(s) posted, {skipped} already sent previously, "
          f"{failed} failed (of {sent + skipped + failed} scraped)")
    return sent, skipped, failed


def run(csv_path, out_path, only=None, post_to_backend=False, post_to_admin=False,
        admin_post_batch_size=100, requested_out_path=None, technical_only=None,
        window_date=None, enable_cross_run_dedup=True, since=None, until=None):
    if since:
        set_date_range(since, until)
    elif window_date:
        set_date_override(window_date)
    win_start, win_end = get_date_window()
    if technical_only is None:
        technical_only = TECHNICAL_ONLY
    if win_start == win_end:
        print(f"Date window: {win_start} (single day override)")
    elif DATE_RANGE is not None:
        print(f"Date window: {win_start} → {win_end} (catch-up since last run)")
    else:
        print(f"Date window: {win_start} → {win_end} (previous 5 days, today excluded)")
    if technical_only:
        print("[technical] prioritizing technical (software/IT) jobs in output "
              "(non-technical jobs are kept)")
    portals = load_portals(csv_path)
    all_rows = []
    posted_up_to = 0
    for p in portals:
        name = p["portal_name"]
        method = p["scrape_method"]
        url = p["url"]

        if only and name not in only:
            continue

        print(f"[{name}] method={method} url={url}")

        if method == "dead":
            print("  [skip] no longer a live source")
            continue

        try:
            if name in SPECIAL_SCRAPERS:
                # Pass no limit — each scraper paginates until the date window is exhausted
                rows = SPECIAL_SCRAPERS[name]()
            elif method == "api_key_required":
                print("  [skip] api_key_required portal with no handler implemented")
                rows = []
            else:
                rows = generic_scrape(name, url)
        except Exception as e:
            print(f"  [error] {name} failed: {e}", file=sys.stderr)
            rows = []

        print(f"  -> {len(rows)} rows")
        all_rows.extend(rows)

        if post_to_admin and (len(all_rows) - posted_up_to) >= admin_post_batch_size:
            batch = all_rows[posted_up_to:]
            print(f"  [admin-api] posting batch of {len(batch)} rows "
                  f"(scraped {len(all_rows)} total so far)")
            post_rows_to_admin_api(batch)
            posted_up_to = len(all_rows)

        time.sleep(REQUEST_DELAY)

    # Deduplicate rows within this run by URL, company+role, and fuzzy
    # similarity across title/company/location (catches near-identical and
    # cross-portal reposts of the same job).
    unique_rows = deduplicate_rows(all_rows, fuzzy=True)
    duplicates_removed = len(all_rows) - len(unique_rows)

    if duplicates_removed > 0:
        print(f"\n[dedup] Removed {duplicates_removed} duplicate row(s) "
              f"(by URL, company+role, and/or fuzzy title/company/location)")

    # Cross-run dedup: skip jobs already seen in any previous scrape run.
    # Keys are persisted in scraped_seen.json between runs and are ALSO rebuilt
    # from every previously written dated CSV, so a posting that appeared on
    # any earlier date is never re-emitted — URL match first, then
    # company + role + location.
    # Disable with --no-cross-run-dedup to force re-scrape all jobs.
    seen_keys = _load_scrape_seen() if enable_cross_run_dedup else set()
    if enable_cross_run_dedup:
        seen_keys |= _prior_run_keys_from_csvs(exclude_path=out_path)
        unique_rows, cross_run_skipped = cross_run_dedup(unique_rows, seen_keys)
        if cross_run_skipped > 0:
            print(f"[cross-run dedup] Skipped {cross_run_skipped} job(s) already seen in a previous run")
    else:
        cross_run_skipped = 0
        print("[cross-run dedup] Disabled — all scraped jobs will be written to CSV")
    print(f"[dedup] Writing {len(unique_rows)} new unique rows to {out_path}")

    if technical_only and unique_rows:
        # Stable sort: technical jobs first, non-technical jobs after, so the
        # output prioritizes technical roles without dropping any postings.
        sorted_rows = sorted(unique_rows, key=lambda r: 0 if is_technical_job(r) else 1)
        tech_n = sum(1 for r in sorted_rows if is_technical_job(r))
        unique_rows = sorted_rows
        print(f"[technical] prioritized {tech_n} technical jobs first "
              f"({len(unique_rows) - tech_n} non-technical kept after)")

    before_link_filter = len(unique_rows)
    unique_rows = [
        row for row in unique_rows
        if is_source_link_active(row.get("URL") or row.get("sourceLink") or "")
    ]
    inactive_removed = before_link_filter - len(unique_rows)
    if inactive_removed:
        print(f"[link-check] Removed {inactive_removed} inactive/broken/tracking source link(s)")

    before_output_quality_filter = len(unique_rows)
    output_ready_rows = []
    missing_skills_removed = 0
    expired_removed = 0
    missing_fields_removed = 0
    raw_json_removed = 0
    for row in unique_rows:
        payload, reason = output_payload_for_row(row)
        if payload is not None:
            output_ready_rows.append(row)
        elif reason == "missing_required_skills":
            missing_skills_removed += 1
        elif reason == "expired":
            expired_removed += 1
        elif reason == "missing_required_fields":
            missing_fields_removed += 1
        elif reason == "raw_json_description":
            raw_json_removed += 1
    unique_rows = output_ready_rows
    output_quality_removed = before_output_quality_filter - len(unique_rows)
    if output_quality_removed:
        print(
            "[quality] Removed "
            f"{output_quality_removed} row(s): "
            f"{missing_skills_removed} missing requiredSkills, "
            f"{expired_removed} expired, "
            f"{missing_fields_removed} missing company/job title, "
            f"{raw_json_removed} raw JSON description"
        )

    written = write_requested_format_csv(unique_rows, out_path)
    dropped = len(unique_rows) - written

    # Persist newly seen keys so the next run skips these jobs
    new_keys = set()
    for row in unique_rows:
        new_keys.update(_scrape_seen_keys(row))
    _save_scrape_seen(seen_keys | new_keys)

    print(f"\nDone. {written} rows written to {out_path} "
          f"({duplicates_removed} within-run duplicate(s) removed, "
          f"{cross_run_skipped} cross-run duplicate(s) skipped, "
          f"{inactive_removed} inactive/broken link(s) removed, "
          f"{missing_skills_removed} missing requiredSkills removed, "
          f"{expired_removed} expired job(s) removed, "
          f"{raw_json_removed} raw JSON description row(s) removed, "
          f"{dropped} row(s) dropped for missing company/job title)")
    if dropped:
        print("[warn] dropped rows had no company or job title, so they were not emitted")

    if requested_out_path:
        write_requested_format_csv(unique_rows, requested_out_path)
        print(f"Requested-format CSV written to {requested_out_path}")

    if post_to_backend:
        post_rows_to_backend(unique_rows)

    if post_to_admin and len(all_rows) > posted_up_to:
        final_batch = all_rows[posted_up_to:]
        print(f"  [admin-api] posting final batch of {len(final_batch)} row(s)")
        post_rows_to_admin_api(final_batch)

    return all_rows


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scrape USA jobs from multiple portals. "
            "Default mode: previous 5-day rolling window (excludes today and yesterday). "
            "Single-day mode: pass --date YYYY-MM-DD to scrape only that specific date. "
            "You can also set DATE_OVERRIDE at the top of this file instead of using --date."
        )
    )
    parser.add_argument("--portals-csv", default=os.path.join(os.path.dirname(__file__), "portals.csv"))
    parser.add_argument("--out", default="",
                        help="output CSV path (default: jobs_YYYY-MM-DD.csv when --date is set, "
                             "otherwise jobs_output.csv)")
    parser.add_argument("--only", nargs="*", default=None, help="restrict to these portal names")
    parser.add_argument("--post-backend", action="store_true",
                         help="also post to BACKEND_API_URL (our own AI engine)")
    parser.add_argument("--post-admin", action="store_true",
                         help="post scraped jobs to ADMIN_API_URL (off by default - CSV only)")
    parser.add_argument("--requested-out", default="",
                         help="also write a second copy of the requested admin job CSV")
    parser.add_argument("--technical-only", dest="technical_only", action="store_true", default=None,
                        help="prioritize technical jobs first in output (default: on, see TECHNICAL_ONLY)")
    parser.add_argument("--include-nontechnical", dest="technical_only", action="store_false",
                        help="do not prioritize technical jobs (keep original ordering)")
    parser.add_argument("--date", default="",
                        help=(
                            "Scrape only postings published on this single date. "
                            "Format: YYYY-MM-DD  (e.g. --date 2026-09-08). "
                            "Overrides the default 5-day rolling window. "
                            "Output file is automatically named jobs_YYYY-MM-DD.csv unless --out is set."
                        ))
    parser.add_argument("--since", default="",
                        help="catch-up mode: scrape every posting from this date (YYYY-MM-DD) through "
                             "--until (default today). Cross-run dedup drops anything already scraped.")
    parser.add_argument("--until", default="", help="last day of the --since range (default: today, UTC)")
    parser.add_argument("--no-cross-run-dedup", dest="cross_run_dedup", action="store_false",
                        default=True,
                        help=(
                            "Disable cross-run deduplication. By default, jobs already seen in "
                            "a previous scrape run are skipped (tracked in scraped_seen.json). "
                            "Use this flag to force re-scrape all jobs regardless of history."
                        ))
    args = parser.parse_args()

    # Resolve output path: auto-name by date when --date is used and --out is not set
    date_str = args.date.strip() if args.date else ""
    if args.out:
        out_path = args.out
    elif date_str:
        out_path = os.path.join(os.path.dirname(__file__), f"jobs_{date_str}.csv")
    else:
        out_path = os.path.join(os.path.dirname(__file__), "jobs_output.csv")

    run(
        args.portals_csv, out_path,
        set(args.only) if args.only else None,
        post_to_backend=args.post_backend,
        post_to_admin=args.post_admin,
        requested_out_path=args.requested_out or None,
        technical_only=args.technical_only,
        window_date=date_str or None,
        enable_cross_run_dedup=args.cross_run_dedup,
        since=args.since.strip() or None,
        until=args.until.strip() or None,
    )


if __name__ == "__main__":
    main()
