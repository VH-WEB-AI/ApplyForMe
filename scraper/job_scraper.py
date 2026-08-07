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
import time
import json
import argparse
import unicodedata
from html import unescape as html_unescape
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
    TRANSFORMERS_AVAILABLE = True
except ImportError:
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


def extract_required_skills(text):
    """Finds every taxonomy skill mentioned in `text`, canonical names, de-duplicated."""
    if not text:
        return []
    lookup = _load_skills_taxonomy()
    lower_text = text.lower()
    found = set()
    for alias, canonical in lookup.items():
        pattern = r"(?<![a-z0-9+#.])" + re.escape(alias) + r"(?![a-z0-9+#])"
        if re.search(pattern, lower_text):
            found.add(canonical)
    return sorted(found)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobResearchBot/1.0; "
                  "+https://example.com/bot-info)"
}
TIMEOUT = 15
REQUEST_DELAY = 1.0  # be polite between requests

FIELDNAMES = [
    "Portal", "Job Title", "Company", "URL", "About the job", "Pay",
    "Location", "Summary", "Requirements", "Responsibilities", "Benefits",
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
# The admin API rejects descriptions over 8000 chars (seen live: "description must be
# shorter than or equal to 8000 characters"); trim with margin rather than hit the exact limit.
ADMIN_API_DESCRIPTION_MAX_LEN = 7900

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
    r"(https?://t\.co/|#\w+|\bCA:\s*[A-Za-z0-9]{20,}|\bATH MC\b|\bLIQ\b|"
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
        sections["Summary"] = about[:600]
    if not about:
        about = sections["Summary"]

    pay_match = PAY_RE.search(text)
    pay = pay_match.group(0).strip() if pay_match else ""

    location = location_hint
    if not location:
        loc_match = LOCATION_HINTS.search(text)
        location = loc_match.group(0).strip() if loc_match else ""

    return {
        "About the job": about[:4000],
        "Pay": pay,
        "Location": location,
        "Summary": sections["Summary"][:1000],
        "Requirements": sections["Requirements"][:2000],
        "Responsibilities": sections["Responsibilities"][:2000],
        "Benefits": sections["Benefits"][:1000],
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


def normalize_location(text):
    text = one_line(text)
    return text.strip(" ,")


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


def make_row(portal, title, company, url, raw_text, location_hint=""):
    parsed = parse_sections(raw_text, title, location_hint)
    row = {
        "Portal": one_line(portal),
        "Job Title": one_line(title),
        "Company": one_line(company),
        "URL": one_line(url),
        "Scraped At": now_iso(),
    }
    row.update(parsed)
    row["Location"] = normalize_location(row.get("Location", ""))
    row = refine_row_with_llm(row)
    return row if should_keep_row(row) else None


def safe_get(url, params=None, headers=None):
    """GET a URL and force correct decoding.

    requests only trusts the charset declared in the Content-Type header; if
    a server doesn't declare one (common for JSON APIs and many HTML pages)
    requests silently falls back to Latin-1 for .text, which corrupts every
    non-ASCII character (curly quotes, em-dashes, accented names, etc.) and
    is the #1 cause of "garbled CSV" output. We override that here.
    """
    try:
        h = dict(HEADERS)
        if headers:
            h.update(headers)
        resp = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
        resp.raise_for_status()
        declared = (resp.headers.get("content-type") or "").lower()
        if "charset" not in declared:
            # no explicit charset from the server -> don't trust requests'
            # Latin-1 default, decode the raw bytes as UTF-8 (the overwhelming
            # norm on the modern web) with a safe fallback.
            resp.encoding = "utf-8"
        return resp
    except requests.RequestException as e:
        print(f"  [warn] request failed for {url}: {e}", file=sys.stderr)
        return None


def safe_json(resp):
    """Parse JSON directly off the raw response bytes (bypasses any charset
    guessing entirely) so API responses can never get mangled."""
    try:
        return json.loads(resp.content.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None


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
    feed = feedparser.parse(feed_url)
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        url = entry.get("link", "")
        desc = entry.get("summary", "") or entry.get("description", "")
        company = ""
        # "Company: Title" is a common WWR/Working Nomads title pattern
        if ":" in title:
            maybe_company, maybe_title = title.split(":", 1)
            if len(maybe_company) < 40:
                company, title = maybe_company.strip(), maybe_title.strip()
        row = make_row(portal_name, title, company, url, desc)
        if row:
            rows.append(row)
    return rows


def scrape_weworkremotely(limit=50):
    # WWR publishes an RSS feed per category; the combined/all-jobs feed:
    return scrape_rss("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss", limit)


def scrape_workingnomads(limit=50):
    return scrape_rss("Working Nomads", "https://www.workingnomads.com/jobs.rss", limit)


def scrape_nodesk(limit=50):
    return scrape_rss("NoDesk", "https://nodesk.co/remote-jobs/feed/", limit)


# --------------------------------------------------------------------------
# 3. Official APIs that require a free key (env vars) - implemented but
#    skipped automatically if the key isn't set.
# --------------------------------------------------------------------------

def scrape_adzuna(limit=50):
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("  [skip] Adzuna USA: set ADZUNA_APP_ID / ADZUNA_APP_KEY env vars "
              "(free at https://developer.adzuna.com/) to enable")
        return []
    rows = []
    resp = safe_get(
        "https://api.adzuna.com/v1/api/jobs/us/search/1",
        params={"app_id": app_id, "app_key": app_key, "results_per_page": min(limit, 50),
                "content-type": "application/json"},
    )
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows
    for item in data.get("results", [])[:limit]:
        title = item.get("title", "")
        company = (item.get("company") or {}).get("display_name", "")
        url = item.get("redirect_url", "")
        desc = item.get("description", "")
        location = (item.get("location") or {}).get("display_name", "")
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        pay_hint = f"${salary_min:.0f}-${salary_max:.0f}" if salary_min and salary_max else ""
        row = make_row("Adzuna USA", title, company, url, desc, location)
        if not row:
            continue
        if pay_hint:
            row["Pay"] = pay_hint
        rows.append(row)
    return rows


def scrape_usajobs(limit=50):
    api_key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not api_key or not email:
        print("  [skip] USAJOBS: set USAJOBS_API_KEY / USAJOBS_EMAIL env vars "
              "(free at https://developer.usajobs.gov/) to enable")
        return []
    rows = []
    resp = safe_get(
        "https://data.usajobs.gov/api/search",
        params={"ResultsPerPage": min(limit, 50)},
        headers={"Authorization-Key": api_key, "User-Agent": email, "Host": "data.usajobs.gov"},
    )
    if not resp:
        return rows
    data = safe_json(resp)
    if data is None:
        return rows
    items = data.get("SearchResult", {}).get("SearchResultItems", [])
    for item in items[:limit]:
        d = item.get("MatchedObjectDescriptor", {})
        title = d.get("PositionTitle", "")
        company = d.get("OrganizationName", "")
        url = d.get("PositionURI", "")
        desc = d.get("UserArea", {}).get("Details", {}).get("JobSummary", "") or d.get("QualificationSummary", "")
        locations = d.get("PositionLocationDisplay", "")
        pay = ""
        remun = d.get("PositionRemuneration", [])
        if remun:
            pay = f"${remun[0].get('MinimumRange','')}-${remun[0].get('MaximumRange','')} {remun[0].get('RateIntervalCode','')}"
        row = make_row("USAJOBS", title, company, url, desc, locations)
        if not row:
            continue
        if pay.strip("$- "):
            row["Pay"] = pay
        rows.append(row)
    return rows


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
    for title_guess, url in job_links:
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
_ADMIN_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry[- ]level|intern(?:ship)?)\b", re.IGNORECASE)
_ADMIN_VISA_RE = re.compile(r"visa sponsorship|will sponsor|sponsors? visa|sponsorship available", re.IGNORECASE)
_ADMIN_SALARY_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")
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
    if _ADMIN_JUNIOR_RE.search(title):
        return "Junior"
    return "Mid"


def _admin_infer_employment_type(text):
    match = _ADMIN_EMPLOYMENT_TYPE_RE.search(text)
    if not match:
        return "Full-time"
    return _ADMIN_EMPLOYMENT_TYPE_MAP.get(match.group(0).lower(), "Full-time")


def _admin_salary_range(pay):
    if not pay:
        return None, None
    numbers = [float(n.replace(",", "")) for n in _ADMIN_SALARY_NUM_RE.findall(pay)]
    if not numbers:
        return None, None
    if "k" in pay.lower():
        numbers = [n * 1000 for n in numbers]
    values = [int(n) for n in numbers]
    return min(values), max(values)


def _admin_build_description(row):
    parts = [row.get("About the job") or row.get("Summary", "")]
    if row.get("Responsibilities"):
        parts.append("Responsibilities:\n" + row["Responsibilities"])
    if row.get("Requirements"):
        parts.append("Requirements:\n" + row["Requirements"])
    if row.get("Benefits"):
        parts.append("Benefits:\n" + row["Benefits"])
    return "\n\n".join(part for part in parts if part).strip()


def row_to_admin_job_payload(row):
    """Maps one scraped row (see FIELDNAMES) onto the admin API's job-creation
    body shape: {company, role, platform, location, remote, salaryMin, salaryMax,
    seniority, visaSponsorship, employmentType, description, requiredSkills,
    sourceLink, active}."""
    title = (row.get("Job Title") or "").strip()
    company = (row.get("Company") or "").strip()
    if not title or not company:
        return None

    location = row.get("Location") or ""
    description = _admin_build_description(row)[:ADMIN_API_DESCRIPTION_MAX_LEN]
    salary_min, salary_max = _admin_salary_range(row.get("Pay", ""))
    combined_text = f"{title} {description}"

    return {
        "company": company,
        "role": title,
        "platform": row.get("Portal", ""),
        "location": location,
        "remote": "remote" in location.lower(),
        "salaryMin": salary_min,
        "salaryMax": salary_max,
        "seniority": _admin_infer_seniority(title),
        "visaSponsorship": bool(_ADMIN_VISA_RE.search(combined_text)),
        "employmentType": _admin_infer_employment_type(combined_text),
        "description": description or title,
        "requiredSkills": extract_required_skills(combined_text),
        "sourceLink": row.get("URL", ""),
        "active": True,
    }


def _admin_dedup_key(row):
    """Prefers the scraped URL (most specific); falls back to company+role
    for portals where a stable per-posting URL isn't available."""
    url = (row.get("URL") or "").strip().lower()
    if url:
        return url
    company = (row.get("Company") or "").strip().lower()
    title = (row.get("Job Title") or "").strip().lower()
    return f"{company}::{title}"


def _load_admin_sent_keys(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def _save_admin_sent_keys(path, keys):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f)


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
        key = _admin_dedup_key(row)
        if key in sent_keys:
            skipped += 1
            continue
        payload = row_to_admin_job_payload(row)
        if payload is None:
            continue
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.ok:
                sent += 1
                sent_keys.add(key)
                _save_admin_sent_keys(state_path, sent_keys)
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


def run(csv_path, out_path, limit_per_portal, only=None, post_to_backend=False, post_to_admin=True):
    portals = load_portals(csv_path)
    all_rows = []
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
                rows = SPECIAL_SCRAPERS[name](limit_per_portal)
            elif method == "api_key_required":
                # already routed via SPECIAL_SCRAPERS above if implemented;
                # anything else in this bucket without a handler is skipped
                print("  [skip] api_key_required portal with no handler implemented")
                rows = []
            else:
                rows = generic_scrape(name, url, limit_per_portal)
        except Exception as e:
            print(f"  [error] {name} failed: {e}", file=sys.stderr)
            rows = []

        print(f"  -> {len(rows)} rows")
        all_rows.extend(rows)
        time.sleep(REQUEST_DELAY)

    # utf-8-sig writes a BOM, which is what makes Excel (Windows in
    # particular) reliably detect UTF-8 instead of misreading accented
    # characters as ANSI/CP1252 garbage. csv.QUOTE_MINIMAL + newline=""
    # (required by the csv module) keeps every multi-line field correctly
    # quoted so Excel/Sheets treat it as one cell instead of spilling rows.
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=FIELDNAMES, quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for row in all_rows:
            writer.writerow(normalize_row_row_values(row))

    print(f"\nDone. {len(all_rows)} total rows written to {out_path}")

    if post_to_backend:
        post_rows_to_backend(all_rows)

    if post_to_admin:
        post_rows_to_admin_api(all_rows)

    return all_rows


def main():
    parser = argparse.ArgumentParser(description="Scrape jobs from multiple portals.")
    parser.add_argument("--portals-csv", default=os.path.join(os.path.dirname(__file__), "portals.csv"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "jobs_output.csv"))
    parser.add_argument("--limit", type=int, default=25, help="max jobs to pull per portal")
    parser.add_argument("--only", nargs="*", default=None, help="restrict to these portal names")
    parser.add_argument("--post-backend", action="store_true",
                         help="also post to BACKEND_API_URL (our own AI engine) - off by default, "
                              "job data now flows to ADMIN_API_URL only")
    parser.add_argument("--no-post-admin", action="store_true",
                         help="skip posting to ADMIN_API_URL even if it's configured")
    args = parser.parse_args()
    run(
        args.portals_csv, args.out, args.limit,
        set(args.only) if args.only else None,
        post_to_backend=args.post_backend,
        post_to_admin=not args.no_post_admin,
    )


if __name__ == "__main__":
    main()
