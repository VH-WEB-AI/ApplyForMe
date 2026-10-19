"""One pass that makes a scraped CSV fit for the admin intake.

Three guarantees, in order:
  1. UNIQUE     - one row per sourceLink and per company::role. The intake
                  dedups on both keys, so duplicates here are rows that would
                  silently vanish server-side; better to drop them knowingly and
                  keep the richest copy of each job.
  2. TAXONOMY   - industry, category and subCategory on every row.
  3. TAGS >= 7  - tags are the board's only filter, so a job below the floor is
                  effectively unfindable. Existing tags are kept and topped up,
                  never replaced.

Run with the venv interpreter — YAKE lives there:
    .venv/bin/python prepare_jobs.py --csv delaware_1.csv --out delaware_1_final.csv
"""
import argparse
import ast
import csv
import json
import os
import re
import sys

import tag_quality as tq
from job_categories import classify
from job_industries import classify_industry

csv.field_size_limit(10 ** 7)

MIN_TAGS = 7
MAX_TAGS, MAX_TAG_LEN = 100, 50
DESCRIPTION_SCAN_CHARS = 4000

_SKILLS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "skills_taxonomy.json")

# Refusals and apologies the LLM tagger leaked into the tags column on some
# rows. They are not tags and must never reach the board.
_JUNK_TAG_PATTERNS = re.compile(
    r"(i can'?t|i cannot|i'?m sorry|as an ai|unable to|not able to|no tags|n/?a|none|unknown"
    r"|here (are|is)|sure[,!]|okay|null)",
    re.I,
)

# Words that describe every posting ever written and so filter nothing.
_FILLER_TAGS = {
    "job", "jobs", "role", "roles", "position", "positions", "career", "careers",
    "work", "working", "team", "teams", "company", "employer", "opportunity",
    "opportunities", "candidate", "candidates", "applicant", "employee",
    "employees", "experience", "responsibilities", "requirements", "qualified",
    "qualifications", "full time", "part time", "apply", "hiring", "join",
    "benefits", "compensation", "salary", "equal opportunity", "eeo", "years",
    "ability", "skills", "strong", "excellent", "including", "etc", "new",
    "the", "and", "or", "for", "with", "you", "we", "our", "your",
}


def _load_skill_vocabulary():
    try:
        with open(_SKILLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    # {"Python": [...aliases]} -> {alias_lower: canonical}
    vocabulary = {}
    for canonical, aliases in (data.items() if isinstance(data, dict) else []):
        vocabulary[canonical.lower()] = canonical
        for alias in (aliases if isinstance(aliases, list) else []):
            if isinstance(alias, str) and alias.strip():
                vocabulary[alias.strip().lower()] = canonical
    return vocabulary


SKILL_VOCABULARY = _load_skill_vocabulary()

_yake_extractor = None


def _yake_tags(text, limit):
    """Keyphrases as the last resort. YAKE on a job posting surfaces the
    boilerplate's frequent n-grams as often as the actual work, which is why it
    runs only after the vocabulary and taxonomy passes have had their turn."""
    global _yake_extractor
    if not text.strip():
        return []
    if _yake_extractor is None:
        try:
            import yake
        except ImportError:
            return []
        _yake_extractor = yake.KeywordExtractor(lan="en", n=2, top=40, dedupLim=0.9)
    try:
        keywords = _yake_extractor.extract_keywords(text[:DESCRIPTION_SCAN_CHARS])
    except Exception:
        return []
    return [phrase.lower() for phrase, _score in keywords][:limit]


def parse_tags(value):
    # The CSV carries tags as a string ("['a', 'b']" or "a, b"); the API returns
    # a real JSON array. Both paths call this, so accept either.
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = (value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (ValueError, SyntaxError):
            pass
    return [part.strip() for part in raw.split(",") if part.strip()]


def _normalize_tag(tag):
    tag = re.sub(r"\s+", " ", str(tag).replace("_", " ").replace("-", " ")).strip().lower()
    return tag[:MAX_TAG_LEN].strip()


def _is_usable(tag, company_words):
    if len(tag) < 2 or len(tag) > MAX_TAG_LEN:
        return False
    if tag in _FILLER_TAGS or _JUNK_TAG_PATTERNS.search(tag):
        return False
    if tag.isdigit():
        return False
    # A tag that is just the employer's name filters nothing on a board already
    # showing the employer.
    return tag not in company_words


def _add(tags, seen, candidate, company_words):
    tag = _normalize_tag(candidate)
    if not tag or tag in seen or not _is_usable(tag, company_words):
        return
    seen.add(tag)
    tags.append(tag)


TARGET_TAGS = 15


def build_tags(row, industry, category, sub_category):
    """Tags a board can actually filter on. Every candidate must pass tag_quality.is_good_tag
    (short, noun-like, grounded in the domain vocabulary or the job's own title) -- the scraper's
    own tags column and YAKE are mostly boilerplate ("application materials", "next lower
    grade"), so they are filtered like everything else. Order: tags already on the row that
    pass, skills/keyphrases found in the text, the cleaned title, the taxonomy labels, the job's
    attributes, and keyphrases only as a last resort."""
    company = (row.get("company") or "").strip()
    company_words = {w for w in _normalize_tag(company).split() if len(w) > 2}
    company_words.add(_normalize_tag(company))

    role = (row.get("role") or "").strip()
    description = (row.get("description") or "").strip()
    text = f"{role}\n{description}"[:DESCRIPTION_SCAN_CHARS]
    own_words = set(tq.title_words(role))

    tags, seen = [], set()
    title_text = " ".join(tq.title_words(role))

    def add(candidate, check=True):
        tag = _normalize_tag(candidate)
        if tag and tag not in seen and _is_usable(tag, company_words) \
                and (not check or (tq.is_good_tag(tag, own_words)
                                   and (tq.is_vocab(tag) or tag in title_text))):
            seen.add(tag)
            tags.append(tag)

    def add_label(label):
        label = (label or "").strip()
        if label and not label.lower().startswith("other") and "not specified" not in label.lower():
            add(label, check=False)

    in_it = category == "Information Technology & Software"
    for tag in tq.domain_tags(text, role, skill_min_count=1 if in_it else 2):
        add(tag, check=False)
    for tag in parse_tags(row.get("tags")):      # the scraper's own tags: kept only when real
        add(tag)
    title_phrase = tq.title_tags(role)
    for tag in title_phrase[:1]:
        add(tag, check=False)
    for label in (sub_category, category, industry):
        add_label(label)
    for attribute in (row.get("seniority"), row.get("employmentType")):
        add(attribute or "", check=False)
    if str(row.get("remote") or "").strip().lower() in ("true", "1", "yes"):
        add("remote", check=False)
    if (row.get("platform") or "").strip() == "USAJOBS":
        add("federal government", check=False)
    if len(tags) < MIN_TAGS:                     # thin posting: relax the repeat requirement
        for tag in tq.domain_tags(text, role, min_count=1, skill_min_count=1):
            add(tag, check=False)
            if len(tags) >= MIN_TAGS:
                break
    if len(tags) < MIN_TAGS:                     # then the job's own title words, then where it is
        for tag in title_phrase[1:] + tq.location_tags(row.get("location")):
            add(tag, check=False)
            if len(tags) >= MIN_TAGS:
                break
    if len(tags) < MIN_TAGS:
        for phrase in _yake_tags(text.lower(), MIN_TAGS * 4):
            add(phrase)
            if len(tags) >= MIN_TAGS:
                break
    if len(tags) < MIN_TAGS:                     # last padding: title words however generic, then the country
        label_words = [w for label in (sub_category, category, industry)
                       for w in re.findall(r"[a-zA-Z]{5,}", label or "") if w.lower() not in ("other", "general", "specified", "government")]
        for tag in tq.title_words(role) + label_words + ["united states"]:
            add(tag, check=False)
            if len(tags) >= MIN_TAGS:
                break
    return tags[:TARGET_TAGS]


def row_quality(row):
    """Which copy of a duplicated job to keep: the one carrying more tags, then
    the longer description."""
    return (len(parse_tags(row.get("tags"))), len(row.get("description") or ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-tags", type=int, default=MIN_TAGS)
    args = parser.parse_args()

    if os.path.abspath(args.csv) == os.path.abspath(args.out):
        sys.exit("--out must differ from --csv")

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [(n or "").lstrip("﻿").strip().strip('"') for n in (reader.fieldnames or [])]
        rows = [{(k or "").lstrip("﻿").strip().strip('"'): v for k, v in r.items()} for r in reader]

    total = len(rows)

    # --- 1. unique -------------------------------------------------------
    # key -> index into `order`, so replacing the winner for a key is a direct
    # assignment. An earlier version stored the row itself and located it with
    # order.index(), which is O(n) per hit and raises once a row has already
    # been swapped out of its slot — a three-way collision crashed the run.
    position = {}
    order = []
    dropped_invalid = 0
    for row in rows:
        company = (row.get("company") or "").strip()
        role = (row.get("role") or "").strip()
        if not company or not role:
            dropped_invalid += 1
            continue
        url = (row.get("sourceLink") or "").strip().lower()
        location = (row.get("location") or "").strip().lower()
        # The posting URL identifies a job; two rows sharing one are the same
        # job stored twice. company::role alone is NOT an identity: CVS lists
        # "Pharmacy Technician" at 93 different stores, and collapsing those
        # deletes 92 real jobs. Location is what separates them, so it belongs
        # in the key — and it is only needed when there is no URL to key on.
        keys = [url] if url else [f"{company.lower()}::{role.lower()}::{location}"]

        hit = next((k for k in keys if k in position), None)
        if hit is None:
            index = len(order)
            order.append(row)
        else:
            index = position[hit]
            if row_quality(row) > row_quality(order[index]):
                order[index] = row
        for key in keys:
            position.setdefault(key, index)

    deduped = order
    duplicates = total - dropped_invalid - len(deduped)

    # --- 2. taxonomy + 3. tags ------------------------------------------
    topped_up = 0
    for row in deduped:
        category, sub_category, _score = classify(
            role=row.get("role"), tags=row.get("tags"),
            description=row.get("description"), company=row.get("company"),
            employment_type=row.get("employmentType"), remote=row.get("remote"),
        )
        industry, _source = classify_industry(
            company=row.get("company"), description=row.get("description"), category=category,
        )
        before = len(parse_tags(row.get("tags")))
        tags = build_tags(row, industry, category, sub_category)
        if before < args.min_tags:
            topped_up += 1
        row["industry"] = industry
        row["category"] = category
        row["subCategory"] = sub_category
        row["tags"] = ", ".join(tags)

    out_fields = [f for f in fieldnames if f not in ("industry", "category", "subCategory")]
    out_fields += ["industry", "category", "subCategory"]

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    below = sum(1 for r in deduped if len(parse_tags(r["tags"])) < args.min_tags)
    print(f"{args.csv} -> {args.out}")
    print(f"  read {total} | invalid {dropped_invalid} | duplicates dropped {duplicates} | kept {len(deduped)}")
    print(f"  tags topped up on {topped_up} rows | still below {args.min_tags}: {below}")


if __name__ == "__main__":
    main()
