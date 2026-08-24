"""Encodes the Web Research SOP's qualification rules for job-seeker leads.

Everything here is deterministic text matching over what a candidate *publicly
stated*. Nothing infers immigration status or nationality -- the SOP (s10, s13,
s23) forbids that, so a missing signal stays False/"unknown" rather than being
guessed from a name, university, or photo.
"""

import re
from datetime import date, timedelta
from urllib.parse import urlsplit

# --- SOP s14: clean, canonical LinkedIn URL as the duplicate key -------------

_LINKEDIN_PATH_RE = re.compile(r"/in/([^/?#]+)")


def normalize_linkedin_url(url: str) -> str:
    """Reduces a profile URL to a canonical `linkedin.com/in/<slug>` dedup key.

    Strips tracking params (?utm_source=, ?trk=), the locale subdomain, trailing
    slashes, and case. Returns "" if this isn't a personal profile URL.
    """
    if not url:
        return ""
    candidate = url.strip()
    if "//" not in candidate:
        candidate = "https://" + candidate
    parts = urlsplit(candidate)
    if "linkedin.com" not in parts.netloc.lower():
        return ""
    match = _LINKEDIN_PATH_RE.search(parts.path)
    if not match:
        return ""
    return f"linkedin.com/in/{match.group(1).strip().lower()}"


# --- SOP s3: is this a Software/IT target role? ------------------------------

_SOFTWARE_TERMS = (
    "software",
    "sde",
    "full stack",
    "fullstack",
    "backend",
    "back end",
    "frontend",
    "front end",
    "web developer",
    "application developer",
    "programmer",
    "java developer",
    "python developer",
    ".net developer",
    "react developer",
    "node.js developer",
    "nodejs developer",
    "angular developer",
    "mobile developer",
    "android developer",
    "ios developer",
    "data analyst",
    "data engineer",
    "data scientist",
    "machine learning",
    "ml engineer",
    "ai engineer",
    "genai",
    "business intelligence",
    "devops",
    "cloud engineer",
    "aws engineer",
    "azure engineer",
    "site reliability",
    "sre",
    "systems engineer",
    "cybersecurity",
    "security engineer",
    "qa engineer",
    "automation engineer",
    "business analyst",
    "systems analyst",
    "database developer",
    "salesforce developer",
)


def is_software_role(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in _SOFTWARE_TERMS)


# --- SOP s22: profiles that must never be entered as job-seeker leads --------

_RECRUITER_TERMS = (
    "recruiter",
    "recruiting",
    "recruitment",
    "bench sales",
    "talent acquisition",
    "staffing",
    "account manager",
    "sourcer",
    "hiring for",
    "we are hiring",
    "now hiring",
)


def recruiter_rejection_reason(text: str) -> str:
    """Returns a rejection reason if the profile text reads as a recruiter or a
    job advert rather than a job seeker, else ""."""
    lowered = (text or "").lower()
    for term in _RECRUITER_TERMS:
        if term in lowered:
            return f"Rejected per SOP s22: profile text matches recruiter/job-post term {term!r}."
    return ""


# --- SOP s10: work-authorization signals, read only from stated text ---------

_F1_RE = re.compile(r"\bf[-\s]?1\b", re.IGNORECASE)
_OPT_RE = re.compile(r"\b(?:initial\s+)?opt(?:\s+ead)?\b", re.IGNORECASE)
_STEM_OPT_RE = re.compile(r"\bstem[-\s]?opt(?:\s+ead)?\b", re.IGNORECASE)
_CPT_RE = re.compile(r"\bcpt\b", re.IGNORECASE)


def extract_visa_signals(statement: str) -> dict[str, bool]:
    """Reads F-1/OPT/STEM OPT/CPT flags out of text the candidate publicly stated.

    Pass only the candidate's own words (headline, About, their post) -- never a
    researcher's guess. STEM OPT implies OPT; both imply the person is on F-1.
    """
    text = statement or ""
    stem_opt = bool(_STEM_OPT_RE.search(text))
    opt = stem_opt or bool(_OPT_RE.search(text))
    cpt = bool(_CPT_RE.search(text))
    f1 = bool(_F1_RE.search(text)) or opt or cpt
    return {"f1_signal": f1, "opt_signal": opt, "stem_opt_signal": stem_opt, "cpt_signal": cpt}


# --- SOP s11: open-to-work / job-search signals ------------------------------

_OPEN_TO_WORK_TERMS = (
    "open to work",
    "opentowork",
    "actively seeking",
    "actively looking",
    "looking for opportunities",
    "seeking opportunities",
    "seeking full-time",
    "seeking full time",
    "available immediately",
    "looking for full-time",
    "looking for full time",
    "looking for my next opportunity",
    "seeking new grad",
    "seeking full-time employment",
    "looking for software",
    "seeking internship",
    "job search",
)

_IMMEDIATE_TERMS = ("available immediately", "immediate joiner", "immediately available")


def has_open_to_work_signal(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in _OPEN_TO_WORK_TERMS)


# --- SOP s12: recency of the job-search evidence -----------------------------

RECENCY_BUCKETS = (7, 30, 60, 90)


def evidence_age_days(evidence_date: date | None, *, today: date) -> int | None:
    if evidence_date is None:
        return None
    return (today - evidence_date).days


def is_recent_evidence(evidence_date: date | None, *, today: date, within_days: int = 90) -> bool:
    age = evidence_age_days(evidence_date, today=today)
    return age is not None and 0 <= age <= within_days


# --- SOP s9: recent graduate ------------------------------------------------


def is_recent_graduate(graduation_year: int | None, *, today: date) -> bool:
    """A recent grad is someone who finished within the last two years or is
    graduating within the next year (SOP s9 prioritises 2025/2026 grads and
    students graduating soon)."""
    if graduation_year is None:
        return False
    return today.year - 2 <= graduation_year <= today.year + 1


# --- SOP s21: lead scoring ---------------------------------------------------


class LeadDraft:
    """Attribute view over a dict of ResearchLead column values, so a row can be
    graded and checklisted before it is persisted."""

    def __init__(self, values: dict):
        self.__dict__.update(values)




def score_lead(lead, *, today: date | None = None) -> tuple[str, list[str]]:
    """Assigns A+/A/B/C and returns the reasons behind the grade.

    Takes anything with the ResearchLead attributes (the model or a pydantic
    payload), so callers can grade before persisting.
    """
    today = today or date.today()

    software = bool(getattr(lead, "is_software_role", False))
    opt = bool(lead.opt_signal) or bool(lead.stem_opt_signal)
    open_to_work = bool(lead.open_to_work_signal)
    recent_grad = is_recent_graduate(lead.graduation_year, today=today)
    in_us = bool(lead.state)
    evidence_date = getattr(lead, "evidence_date", None)
    fresh_post = is_recent_evidence(evidence_date, today=today, within_days=30)
    urgent = fresh_post or any(
        term in (lead.job_search_evidence or "").lower() for term in _IMMEDIATE_TERMS
    )

    reasons: list[str] = []
    if software:
        reasons.append("Software/IT role")
    if recent_grad:
        reasons.append("Recent U.S. graduate")
    if opt:
        reasons.append("OPT/STEM OPT publicly stated")
    if open_to_work:
        reasons.append("Open-to-Work signal")
    if fresh_post:
        reasons.append("Job-search post within 30 days")
    if urgent and not fresh_post:
        reasons.append("Immediate availability stated")

    if software and recent_grad and opt and open_to_work and urgent:
        return "A+", reasons
    if software and recent_grad and opt and open_to_work and in_us:
        return "A", reasons
    if software and in_us and open_to_work:
        reasons.append("Work authorization still to verify")
        return "B", reasons

    missing = []
    if not open_to_work:
        missing.append("Open-to-Work status unclear")
    if not (opt or lead.cpt_signal):
        missing.append("OPT/CPT status unclear")
    if not in_us:
        missing.append("U.S. location unclear")
    if lead.graduation_year is None:
        missing.append("Graduation information missing")
    return "C", reasons + missing


# --- SOP s29: the pre-submission checklist ----------------------------------

REQUIRED_FIELDS = ("linkedin_url", "target_role", "researcher_name", "date_researched")


def checklist_failures(lead) -> list[str]:
    """Returns the SOP s29 checklist items this lead does not satisfy."""
    failures: list[str] = []
    for field in REQUIRED_FIELDS:
        if not getattr(lead, field, None):
            failures.append(f"{field} is required before submission")
    if not normalize_linkedin_url(lead.linkedin_url or ""):
        failures.append("LinkedIn URL is not a valid personal profile URL")
    if not getattr(lead, "is_software_role", False):
        failures.append("Target role is not in an assigned Software/IT category")
    if not lead.open_to_work_signal:
        failures.append("No current job-search / Open-to-Work signal")
    if lead.indian_identity == "yes" and not (lead.job_search_evidence or lead.evidence_url):
        failures.append("Indian identity marked yes without recorded public evidence")
    if (lead.opt_signal or lead.stem_opt_signal or lead.cpt_signal) and not lead.visa_statement:
        failures.append("Visa signal recorded without the candidate's stated wording")
    if lead.email and not lead.email_source:
        failures.append("Email recorded without a source")
    if lead.direct_number and not lead.phone_source:
        failures.append("Direct number recorded without a source")
    if not lead.state:
        failures.append("State is required")
    return failures
