"""What makes a good job tag, and where to find them.

A tag exists so a job board can filter and search: "python", "social work", "park ranger".
The keyword extractor (YAKE) the scraper used to fill the column returns whatever phrases
repeat in a posting, and in a federal announcement that is boilerplate: "application
materials", "next lower grade", "served 52 weeks", "make assumptions regarding", "written".

A candidate tag must pass is_good_tag() AND be grounded: it has to contain a word from the
domain vocabulary (skills, taxonomy keyword phrases, the job's own title words). Real tags
come from, in order: skills named in the text, taxonomy keyword phrases found in the text,
the cleaned job title, the taxonomy labels, and the job's attributes (seniority, type, remote).
"""
import json
import os
import re

from job_categories import TAXONOMY

_HERE = os.path.dirname(os.path.abspath(__file__))
MAX_WORDS = 3

STOPWORDS = set("""
a an the and or but nor of in on at to for from by with without within into onto over under about
as is are was were be been being am do does did done have has had having will would shall should
can could may might must this that these those it its they them their there here which who whom
whose what when where why how not no yes all any some most many much more less least each every
other another such own same than then so very just also only both either neither one two three
last past previous next first second third lower higher upper least basic general various certain
etc per via vs
""".split())

# Verbs that open boilerplate sentences ("provide valuable training", "make assumptions").
LEADING_VERBS = set("""
make provide recruit demonstrate demonstrates ensure ensures perform performs assist assists support
supports develop develops maintain maintains manage manages lead leads serve serves receive receives
apply applies submit submits include includes require requires need needs use uses work works
help helps follow follows complete completes review reviews prepare prepares conduct conducts
offer offers issue issues direct directs oversee oversees assume assumes advise advises
""".split())

# -ing / -ed openers that are legitimate fields rather than sentence fragments.
ALLOWED_ING = {"nursing", "marketing", "accounting", "engineering", "programming", "consulting",
               "banking", "manufacturing", "advertising", "counseling", "training", "teaching",
               "recruiting", "staffing", "trading", "lending", "underwriting", "coaching",
               "bookkeeping", "purchasing", "planning", "welding", "plumbing", "housekeeping",
               "firefighting", "caregiving", "tutoring", "editing", "publishing", "streaming"}

# Boilerplate that describes the hiring process, not the work.
BOILERPLATE = re.compile(
    r"applica|qualif|requirement|eligib|vesting|citizen|selection|\bgrade\b|\bweeks?\b|assum|"
    r"appointment|vacanc|announcement|resume|equal opportunit|benefit|salary|compensation|"
    r"\byou\b|\byour\b|\bour\b|\bwe\b|candidate|incumbent|\bduties\b|responsib|posting|"
    r"valuable|following|listed|regarding|competenc|\bwritten\b|\bspoken\b|\boral\b|"
    r"\betc\b|please|contact us|submit|applicable|series|hiring|retirement|insurance|"
    r"holiday|telework|relocation|probation|duty station|background (check|investigation)|"
    r"drug test|direct deposit|selective service|\bpay\b|\bpercent\b|\byears?\b|\bmonths?\b|\bdays?\b|"
    r"equivalent|experience|knowledge of|ability|skills? (and|&)|including|conscious|rewards|\bmilitary\b|\bphysical\b",
    re.I)

# Words too generic to be a tag on their own.
GENERIC_SINGLE = set("""
work works working team teams job jobs role roles position positions company employer
opportunity opportunities application applications candidate employee employees
experience skills ability support service services management manager specialist assistant
officer analyst coordinator associate senior junior lead staff principal director
supervisory general office program project business operations development
""".split())

# Words in a job title that say nothing about the work.
TITLE_NOISE = set("""
senior sr junior jr lead staff principal supervisory assistant associate entry level mid
seasonal temporary permanent intern internship trainee student non supervisory multilevel
direct hire authority pathways recent graduates bargaining unit open continuous remote hybrid telework exempt
multi small medium large the and of for in at to
""".split()) | STOPWORDS

_SKILLS_PATH = os.path.join(_HERE, "data", "skills_taxonomy.json")


# Skill aliases that are also ordinary words ("rest periods", "spring", "go to").
AMBIGUOUS_ALIASES = {"rest", "spring", "swift", "go", "word", "express", "unity", "flow", "rust"}


def _load_skills():
    try:
        with open(_SKILLS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    vocab = {}
    for canonical, aliases in (data.items() if isinstance(data, dict) else []):
        vocab[canonical.lower()] = canonical.lower()
        for alias in (aliases if isinstance(aliases, list) else []):
            if isinstance(alias, str) and len(alias.strip()) > 2 and alias.strip().lower() not in AMBIGUOUS_ALIASES:
                vocab[alias.strip().lower()] = canonical.lower()
    return vocab


SKILLS = _load_skills()

# Taxonomy keyword phrases ("social work", "software engineer", "wound care"): real domain terms.
DOMAIN_PHRASES = sorted({phrase.lower() for _c, _s, phrases in TAXONOMY for phrase in phrases
                         if len(phrase) > 3 and not BOILERPLATE.search(phrase)}, key=len, reverse=True)
_DOMAIN_RE = [(p, re.compile(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])")) for p in DOMAIN_PHRASES]
DOMAIN_WORDS = ({w for p in DOMAIN_PHRASES for w in re.findall(r"[a-z]{4,}", p)}
                | {w for k in SKILLS for w in re.findall(r"[a-z]{3,}", k)}) - STOPWORDS - GENERIC_SINGLE


def normalize(tag):
    return re.sub(r"\s+", " ", re.sub(r"[_\-/]", " ", str(tag)).strip().lower())[:50].strip()


def is_good_tag(tag, extra_domain_words=frozenset()):
    """True for a short, noun-like, grounded phrase."""
    tag = normalize(tag)
    words = tag.split()
    if not words or len(words) > MAX_WORDS or len(tag) < 3:
        return False
    if tag in SKILLS:
        return True
    if any(ch.isdigit() for ch in tag) or BOILERPLATE.search(tag):
        return False
    if words[0] in STOPWORDS or words[-1] in STOPWORDS or words[0] in LEADING_VERBS:
        return False
    if re.search(r"(ing|ed)$", words[0]) and words[0] not in ALLOWED_ING:
        return False
    if len(words) == 1 and (tag in GENERIC_SINGLE or len(tag) < 4):
        return False
    if sum(1 for w in words if w in STOPWORDS) > 0:
        return False
    # grounding: at least one word must belong to the domain vocabulary or the job's own title
    return any(w in DOMAIN_WORDS or w in extra_domain_words for w in words)


def title_words(role):
    """The words of the job title that name the work (no grades, seniority or locations)."""
    text = re.sub(r"\([^)]*\)", " ", role or "")
    text = re.sub(r"\b(gs|nf|na|nj|wg|ws|wl|o|e)[- ]?\d+\b|\b\d+\b", " ", text, flags=re.I)
    text = text.split(" - ")[0]
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z+#&.]*", text.lower()) if w not in TITLE_NOISE]


def title_tags(role):
    words = title_words(role)
    tags = []
    if 1 < len(words) <= MAX_WORDS:
        tags.append(" ".join(words))
    for w in words:
        if len(w) >= 4 and w not in GENERIC_SINGLE:
            tags.append(w)
    return tags


DOMAIN_SET = set(DOMAIN_PHRASES)

# Real words that mean nothing as a tag unless the job title says so (driver's license, soft skills).
DENY_UNLESS_TITLE = {"driver", "communication", "communications", "leadership", "recruitment",
                     "formulation", "university", "teamwork", "collaboration", "hire", "authority",
                     "united", "institution", "controller", "problem solving", "time management"}
OPENING_CHARS = 2500       # duties/summary; the tail of a posting is eligibility and benefits boilerplate


def is_vocab(tag):
    return tag in SKILLS or tag in DOMAIN_SET


def domain_tags(text, title="", limit=8, min_count=2, skill_min_count=2):
    """Skills and taxonomy phrases that really describe the job: named in the title, or repeated
    in the text. One stray mention in a benefits paragraph ("driver", "human resources") is not
    a tag."""
    title = (title or "").lower()
    skill_text = text.lower()
    text = skill_text[:OPENING_CHARS]
    scored = []
    for alias, canonical in SKILLS.items():
        if len(alias) > 2:
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            n = len(re.findall(pattern, skill_text))
            if canonical in DENY_UNLESS_TITLE and not re.search(pattern, title):
                continue
            if n >= skill_min_count or re.search(pattern, title):
                scored.append((bool(re.search(pattern, title)), n, canonical))
    for phrase, pattern in _DOMAIN_RE:
        n = len(pattern.findall(text))
        if phrase in DENY_UNLESS_TITLE and not pattern.search(title):
            continue
        if n >= min_count or pattern.search(title):
            scored.append((bool(pattern.search(title)), n, phrase))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = []
    for _in_title, _n, tag in scored:
        if tag not in out and not any(tag != o and tag in o for o in out):
            out.append(tag)
        if len(out) >= limit:
            break
    return out


US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts",
    "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "washington dc",
}
_STATE_NAMES = set(US_STATES.values())


def location_tags(location):
    """City and state of 'Bellevue, WA' / 'Bellevue, Washington': real filter terms."""
    tags = []
    for part in [p.strip() for p in re.split(r",", location or "") if p.strip()]:
        low = part.lower()
        if low in {"usa", "us", "united states", "remote", "multiple locations"} or "remote" in low \
                or "united states" in low or low.startswith("us ") or low == "u.s.":
            continue
        if part.upper() in US_STATES:
            tags.append(US_STATES[part.upper()])
        elif low in _STATE_NAMES or re.fullmatch(r"[a-z .'-]{3,30}", low):
            tags.append(low)
    return tags
