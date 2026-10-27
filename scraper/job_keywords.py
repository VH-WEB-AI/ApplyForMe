"""Builds a job's `tags` and `requiredSkills` so they can match resume tags.

The matching backend scores a job by the fraction of the JOB's tags found, as exact lowercase
strings, in the resume's tags. Resume tags are skills/tools, job titles actually held, and a few
domain words ("python", "aws", "technical lead", "nursing"). So a job tag helps only if a resume
could carry it, and every tag a resume can't carry (hiring-process phrases, "mid level",
"federal government", a city) lowers the score. Nothing here uses an LLM or any paid key.

  tags           lowercase, spaces. Keywords from three places:
                   description - skills and tools named in the duties/requirements, acronyms
                                 (SQL, CPA, HIPAA), tool names, up to 5 domain terms
                   role        - the job title as written, and without its seniority word
                   address     - city and state (and "remote")
  requiredSkills Title Case, same vocabulary: the description's skills, tools and domain terms

Only the job's own content is read: benefits, equal-opportunity, how-to-apply and eligibility
text is skipped, which is where a keyword extractor finds "application materials" and
"next lower grade". Skills come from two vocabularies: the backend's skills taxonomy (same
canonical names its skill extractor uses) and data/skill_vocab_ext.json (see build_skill_vocab.py).

    build_matching_fields(row, sub_category, category) -> (tags, required_skills)
"""
import json
import os
import re

import tag_quality as tq

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_TAGS = 7
MAX_TAGS = 20
MAX_SKILLS = 12
MAX_DOMAIN = 5

# A line is a heading when it is short and either ends with ':' or is Title Case / UPPER.
_SKIP_HEADING = re.compile(
    r"benefit|perks|compensation|salary|pay (range|transparency)|equal (employment )?opportunit|\beeo\b|"
    r"how to apply|apply (now|today)|about (us|the company|\w+ inc)|who we are|our (culture|values|mission|story)|"
    r"what we offer|why (join|work)|legal|privacy|notice|veterans?|eligib|conditions of employment|"
    r"required documents|how you will be evaluated|security|agency contact|next steps|travel|"
    r"work schedule|supervisory status|promotion potential|relocation|telework|disclaimer|accommodation|"
    r"total rewards|our (team|commitment)|diversity|inclusion", re.I)
_SKIP_LINE = re.compile(
    r"equal (employment )?opportunit|e-verify|veterans? preference|selective service|u\.?s\.? citizen|"
    r"background (check|investigation)|drug (test|screen)|401\(?k\)?|health insurance|paid time off|"
    r"salary range|base pay|total rewards|apply (now|today|online)|click (on|here)|reasonable accommodation|"
    r"direct hire|probationary|\bpay plan\b|\bgrade\b.*\bstep\b|equivalent to the", re.I)

# Words that begin a title's qualifier, not the work.
_SENIORITY = re.compile(r"^(senior|sr\.?|staff|principal|lead|junior|jr\.?|entry[- ]level|supervisory)\s+", re.I)
_TITLE_NOISE = re.compile(
    r"\([^)]*\)|\b(gs|nf|na|nj|wg|ws|wl|wb)[- ]?\d+(/\d+)?\b|\b\d{2,}\b|\bseasonal\b|\btemporary\b|\bpermanent\b|"
    r"\bpart[- ]time\b|\bfull[- ]time\b|\bintern(ship)?\b|\bremote\b|\bhybrid\b|\bdirect[- ]hire( authority)?\b|"
    r"\bmulti[- ]?level\b|\bentry level\b|\bnon[- ]supervisory\b|\bNTE\b|\bnew grad\b", re.I)

# Taxonomy keywords too broad or odd to say what a job is about.
_GENERIC_DOMAIN = {"science", "sales", "management", "program", "programs", "data", "real", "note", "priority",
                   "engineering", "operations", "service", "services", "customer member service", "support",
                   "analysis", "research", "development", "planning", "training", "administration"}
# Names that are also everyday words/agency acronyms: counted only with a telltale word nearby.
_NEEDS_CONTEXT = {"sap": ("erp", "hana", "s/4", "abap", "fico", "netweaver", "sap s4", "sap ecc", "sap module")}
_AMBIGUOUS = {"go", "rest", "spring", "swift", "word", "express", "unity", "flow", "rust", "r", "c"}
# Real words that mean nothing as a tag unless the title says so.
_DENY_UNLESS_TITLE = tq.DENY_UNLESS_TITLE | {"design", "operations", "government", "engineering", "education", "science"}


def _load_set(filename):
    try:
        with open(os.path.join(HERE, "data", filename), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


ACRONYMS_KNOWN = _load_set("acronyms_known.json")
BOILERPLATE_TOKENS = _load_set("boilerplate_tokens.json")      # learned by build_skill_vocab.py


def _load_vocabulary():
    """{lowercase alias: Canonical Name} from the backend taxonomy and the learned vocabulary."""
    vocab = {}
    for filename in ("skills_taxonomy.json", "skill_vocab_ext.json", "tools_known.json"):
        try:
            with open(os.path.join(HERE, "data", filename), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for canonical, aliases in data.items():
            for alias in list(aliases) + [canonical]:
                key = str(alias).strip().lower()
                if len(key) > 1 and key not in _AMBIGUOUS:
                    vocab.setdefault(key, canonical)
    return vocab


VOCAB = _load_vocabulary()

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]*(?:/[a-z0-9+#.]+)*")


def tokens(text):
    return [t.rstrip(".") for t in _TOKEN.findall((text or "").lower().replace("-", " "))]


def _key(phrase):
    return " ".join(tokens(phrase))


_SKILL_KEYS = {}
for _alias, _canonical in VOCAB.items():
    _SKILL_KEYS.setdefault(_key(_alias), _canonical)
_DOMAIN_KEYS = {_key(p): p for p in tq.DOMAIN_PHRASES}
_DOMAIN_CATS = {}
for _cat, _sub, _phrases in tq.TAXONOMY:
    for _ph in _phrases:
        _DOMAIN_CATS.setdefault(_key(_ph), set()).add(_cat)


def _fits_category(key, category, n, in_title):
    """A function/domain word counts for a job only if it belongs to the job's own category, is in
    the title, or is repeated (3+): "real estate" on a wildlife technician or "law enforcement" on a
    food-service worker is one stray paragraph, not what the job is."""
    cats = _DOMAIN_CATS.get(key)
    return not cats or not category or category in cats or in_title or n >= 3


_MAX_N = max(len(k.split()) for k in list(_SKILL_KEYS) + list(_DOMAIN_KEYS))


def count_phrases(text):
    """{normalised phrase: occurrences} for every vocabulary / domain phrase in the text."""
    toks = tokens(text)
    counts = {}
    for n in range(1, min(_MAX_N, 5) + 1):
        for i in range(len(toks) - n + 1):
            key = " ".join(toks[i:i + n])
            if key in _SKILL_KEYS or key in _DOMAIN_KEYS:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _is_heading(line):
    text = line.strip()
    if not text or len(text) > 70 or text.endswith((".", ",", ";")):
        return False
    if text.endswith(":"):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z&/'-]*", text)
    return 0 < len(words) <= 8 and all(w[0].isupper() for w in words)


def job_content(description):
    """The text that describes the work: headed sections about benefits, legal notices,
    eligibility and how to apply are dropped, and so are boilerplate lines in what remains."""
    kept, skipping = [], False
    for line in (description or "").splitlines():
        if _is_heading(line):
            skipping = bool(_SKIP_HEADING.search(line))
            if skipping:
                continue
        if skipping or _SKIP_LINE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def title_forms(role):
    """The title as a resume would hold it: as written, and without its seniority word."""
    text = _TITLE_NOISE.sub(" ", role or "")
    text = re.split(r"\s+[-–—|]\s+|,|/", text)[0]
    text = re.sub(r"\bsr\.?\b", "senior", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9+#& ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text or len(text.split()) > 6:
        return []
    forms = [text]
    plain = _SENIORITY.sub("", text).strip()
    if plain and plain != text and len(plain.split()) >= 1 and plain not in tq.GENERIC_SINGLE:
        forms.append(plain)
    return forms


def _display(name):
    if name != name.lower():
        return name                                  # canonical vocabulary spelling ("AWS", "Node.js")
    return " ".join(w if w in ("&", "of", "and") else w.capitalize() for w in name.split())


def find_skills(content, title, min_count=1, counts=None, category=""):
    """[(Canonical Name, count)] for vocabulary skills named in the content or title."""
    counts = counts if counts is not None else count_phrases(content)
    title_counts = count_phrases(title)
    found = {}
    for key, canonical in _SKILL_KEYS.items():
        in_title = key in title_counts
        n = counts.get(key, 0) + (3 if in_title else 0)
        if not n or (canonical.lower() in _DENY_UNLESS_TITLE and not in_title):
            continue
        if not _fits_category(key, category, n, in_title):
            continue
        if key in _NEEDS_CONTEXT and not in_title and not any(
                re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", content.lower()) for c in _NEEDS_CONTEXT[key]):
            continue
        # a one-word vocabulary entry is often an everyday word ("sales", "science", "banking"):
        # it needs a second mention unless the title says it
        if len(key.split()) == 1 and canonical not in tq.SKILLS.values() and n < 2:
            continue
        if n >= min_count:
            found[canonical] = max(found.get(canonical, 0), n)
    ordered = sorted(found.items(), key=lambda kv: -kv[1])
    names = [name for name, _ in ordered]
    return [(n, c) for n, c in ordered if not any(n != o and n.lower() in o.lower().split() for o in names)]


def domain_terms(counts, title, min_count=2, limit=MAX_DOMAIN, category=""):
    title_counts = count_phrases(title)
    scored = []
    for key, phrase in _DOMAIN_KEYS.items():
        n = counts.get(key, 0)
        in_title = key in title_counts
        if (phrase in _DENY_UNLESS_TITLE or phrase in _GENERIC_DOMAIN) and not in_title:
            continue
        if not _fits_category(key, category, n, in_title):
            continue
        need = min_count + 1 if len(phrase.split()) == 1 else min_count
        if n >= need or in_title:
            scored.append((in_title, n, phrase))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = []
    for _t, _n, phrase in scored:
        if phrase not in out and not any(phrase != o and phrase in o for o in out):
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


# Acronyms and capitalised names that are not skills.
_ACRONYM_DENY = set("""
EEO EOE USA US OPM GS NF NA WG FLSA ADA HR CEO CFO COO CTO VP SVP EVP LLC INC LLP USD PTO FTE OTE ET EST PST
CST MST AM PM II III IV DC AND THE FOR OR NOT ALL ANY NO YES FAQ PDF ID OK URL FAX TTY TDD DOD DOJ DHS VA VHA
AFB NAF NAFI MWR NG ANG AGR CONUS OCONUS ROTC GI SES ATS AI LGBTQ DEI ERG ERGS LTD CO CORP NASA FBI CIA NSA
USPS IRS SEC FDA CDC HHS DOE DOT EPA USDA GAO OIG FEMA TSA CBP ICE USCIS ATF DEA ISO MON TUE WED THU FRI SAT SUN
JAN FEB MAR APR JUN JUL AUG SEP SEPT OCT NOV DEC NY CA TX FL WA OR VA MD PA NJ OH MI IL GA NC SC TN AZ CO UT MA
""".split())
_CAP_DENY = set("""
The A An And Or Of In On At To For With From By As If We You Our Your They Their This That These Those It Its
Department Agency Office Division Bureau Service Services United States American America National Federal
Government Employer Equal Opportunity Position Title Company Team Role Job Candidate Applicant Applicants
Experience Education Skills Responsibilities Requirements Qualifications Duties Summary Overview Benefits
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August
September October November December Please Note Must Will Can May Should Would Could Also Other Such Each
New Senior Junior Lead Staff Principal Associate Assistant Manager Director Specialist Analyst Engineer
""".split()) | {w.capitalize() for w in tq.US_STATES.values()}


def _tool_names(content, role, company, platform=""):
    """Acronyms (SQL, CPA, HIPAA) and tool names (Anaplan, Tableau): capitalised words mid-sentence
    that repeat. Returns [(display, count)]."""
    skip = {w.lower() for w in re.findall(r"[A-Za-z]+", company or "")} | {w.lower() for w in re.findall(r"[A-Za-z]+", role or "")}
    acr, caps = {}, {}
    for line in content.splitlines():
        if not line.strip() or line.upper() == line:      # blank or an ALL-CAPS heading line
            continue
        for m in re.finditer(r"\b[A-Z][A-Z0-9&+]{1,5}\b", line):
            w = m.group()
            if w not in _ACRONYM_DENY and w.lower() not in skip and w.lower() not in BOILERPLATE_TOKENS:
                acr[w] = acr.get(w, 0) + 1
        for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{3,}[A-Za-z0-9]*)\b", line):
            w = m.group(1)
            if (w not in _CAP_DENY and w.lower() not in skip and w.lower() not in tq.STOPWORDS
                    and w.lower() not in BOILERPLATE_TOKENS):
                caps[w] = caps.get(w, 0) + 1
    out = [(w, n) for w, n in acr.items()
           if w.lower() not in tq.STOPWORDS
           and w.lower() not in _NEEDS_CONTEXT
           and (w.lower() in ACRONYMS_KNOWN or w.lower() in _SKILL_KEYS
                or (platform != "USAJOBS" and len(w) >= 3 and n >= 3))]
    out += [(w, n) for w, n in caps.items() if n >= 3 and w.lower() not in _SKILL_KEYS]
    return sorted(out, key=lambda kv: -kv[1])


def address_keywords(location, remote=False):
    """City and state of the job's address (and "remote"): 'Bellevue, WA' -> bellevue, washington."""
    tags = list(tq.location_tags(location))
    if str(remote).strip().lower() in ("true", "1", "yes") or "remote" in (location or "").lower():
        tags.append("remote")
    return tags


def build_matching_fields(row, sub_category="", category=""):
    role = (row.get("role") or "").strip()
    content = job_content(row.get("description") or "")
    counts = count_phrases(content)
    titles = title_forms(role)

    skills = [name for name, _n in find_skills(content, role, counts=counts, category=category)][:MAX_SKILLS]
    domain = domain_terms(counts, role, category=category)
    tools = _tool_names(content, role, row.get("company"), row.get("platform"))
    address = address_keywords(row.get("location"), row.get("remote"))

    tags, seen = [], set()

    def add(tag):
        tag = re.sub(r"\s+", " ", re.sub(r"[_\-/]+", " ", str(tag).lower())).strip()
        if tag and tag not in seen and 1 < len(tag) <= 50:
            seen.add(tag)
            tags.append(tag)

    for name in skills:                       # description: skills and tools named in the duties
        add(name)
    for w, n in tools[:6]:                    # description: acronyms and tool names (SQL, CPA, Anaplan)
        add(w)
    for t in titles:                          # role: the title as written, and without "senior"
        add(t)
    for d in domain:                          # description: function/domain terms
        add(d)
    for a_ in address:                        # address: city, state, remote
        add(a_)

    # The 7-tag minimum is met with progressively weaker tags, strongest first, so a thin job keeps
    # every real keyword and only the tail is padding.
    def pad(candidates):
        for c in candidates:
            if len(tags) >= MIN_TAGS:
                return
            add(c)

    pad([name for name, _n in find_skills(content, role, min_count=1, counts=counts, category=category)])
    pad(domain_terms(counts, role, min_count=1, limit=MAX_DOMAIN, category=category))
    pad([l for l in (sub_category, category) if l and not l.lower().startswith("other")
         and "not specified" not in l.lower()])                                                # taxonomy function labels
    pad([w for w in tq.title_words(role) if len(w) >= 4 and not any(w in t.split() for t in tags)])
    pad([w for w in tq.title_words(role) if len(w) >= 4])
    pad([row.get("seniority"), row.get("employmentType")])
    pad([row.get("industry")] if (row.get("industry") or "").lower() != "other" else [])
    pad(["federal government"] if (row.get("platform") or "") == "USAJOBS" else [])
    pad(["united states"])

    required = []
    for name in skills + [w for w, _n in tools[:6]] + domain[:3]:
        display = _display(name) if name == name.lower() else name
        if display.lower() not in {r.lower() for r in required}:
            required.append(display)
    if not required:                          # nothing in the text matched a vocabulary: use the job's function
        for name in ([titles[-1]] if titles else []) + [sub_category]:
            if name and not name.lower().startswith("other") and "not specified" not in name.lower():
                display = _display(name.lower())
                if display.lower() not in {r.lower() for r in required}:
                    required.append(display)
    return tags[:MAX_TAGS], required[:MAX_SKILLS]
