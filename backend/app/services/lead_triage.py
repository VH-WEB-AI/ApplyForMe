"""Signal-first triage: qualify a researched profile *before* spending a credit.

The pipeline's original order was population-first -- source recent graduates
from PDL, enrich them, then have a researcher look for job-search signals. That
wastes money, because `checklist_failures` rejects any lead without an
Open-to-Work signal and `score_lead` caps it at C. A perfect PDL record scores C
and cannot be submitted, so most enrichment spend lands on leads that were never
submittable.

This module inverts it. The researcher arrives with what only a human can get --
the candidate's own stated wording, its URL and its date -- and enrichment runs
only on profiles that already clear qualification. Every credit is then spent on
someone capable of reaching grade A or B.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from app.services.lead_qualification import (
    LeadDraft,
    checklist_failures,
    extract_visa_signals,
    has_open_to_work_signal,
    is_software_role,
    normalize_linkedin_url,
    recruiter_rejection_reason,
    score_lead,
)

logger = logging.getLogger(__name__)


@dataclass
class ResearchInput:
    """What a researcher collects by hand. Only `linkedin_url` and `evidence_text`
    are load-bearing; the rest fills s29's required columns."""

    linkedin_url: str
    evidence_text: str = ""
    evidence_url: str = ""
    evidence_date: date | None = None
    researcher_name: str = ""
    target_role: str = ""
    state: str = ""


@dataclass
class TriageResult:
    linkedin_key: str
    qualified: bool
    rejections: list[str] = field(default_factory=list)
    values: dict = field(default_factory=dict)
    grade: str | None = None
    grade_reasons: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)


def qualify(entry: ResearchInput, *, today: date | None = None) -> TriageResult:
    """Decides whether a researched profile is worth enriching. Spends nothing.

    Rejects on the three things that make a lead unsubmittable no matter what a
    vendor later adds: an unusable LinkedIn URL (SOP s14), a recruiter or job-ad
    profile (s22), and a missing Open-to-Work signal (s11, and a hard
    `checklist_failures` item). Role is checked too, but a blank role is left to
    enrichment rather than rejected -- PDL fills `job_title` reliably.
    """
    today = today or date.today()
    rejections: list[str] = []

    linkedin_key = normalize_linkedin_url(entry.linkedin_url)
    if not linkedin_key:
        rejections.append("LinkedIn URL is not a valid personal profile URL (SOP s14).")

    recruiter = recruiter_rejection_reason(entry.evidence_text)
    if recruiter:
        rejections.append(recruiter)

    if not has_open_to_work_signal(entry.evidence_text):
        rejections.append(
            "No Open-to-Work / job-search signal in the stated text (SOP s11): the lead "
            "would fail the s29 checklist and cannot score above C."
        )

    if entry.target_role and not is_software_role(entry.target_role):
        rejections.append(f"Role {entry.target_role!r} is not an assigned Software/IT category (SOP s3).")

    signals = extract_visa_signals(entry.evidence_text)
    values = {
        "linkedin_url": entry.linkedin_url,
        "linkedin_key": linkedin_key,
        "target_role": entry.target_role,
        "state": entry.state,
        "open_to_work_signal": has_open_to_work_signal(entry.evidence_text),
        "job_search_evidence": entry.evidence_text or None,
        "evidence_url": entry.evidence_url or None,
        "evidence_date": entry.evidence_date,
        "researcher_name": entry.researcher_name,
        "date_researched": today,
        # s29 requires the candidate's own wording behind any visa flag.
        "visa_statement": entry.evidence_text if any(signals.values()) else None,
        **signals,
    }

    return TriageResult(
        linkedin_key=linkedin_key,
        qualified=not rejections,
        rejections=rejections,
        values=values,
    )


# Columns PDL may fill. Anything a researcher already entered wins -- the SOP
# treats researched data as authoritative over a vendor guess.
_PDL_FILLABLE = (
    "first_name", "last_name", "email", "city", "state",
    "university", "degree", "graduation_year", "target_role", "skills",
)


def merge_enrichment(result: TriageResult, enriched: dict | None) -> TriageResult:
    """Folds a PDL record into a qualified result without overwriting research.

    Guards the identity rule the same way lead_enrichment does: if the vendor
    supplies a LinkedIn URL and it does not normalise to the researched one, the
    record describes someone else and none of it is used (SOP s15/s17).
    """
    if not enriched:
        return result

    vendor_key = normalize_linkedin_url(enriched.get("linkedin_url") or "")
    if vendor_key and result.linkedin_key and vendor_key != result.linkedin_key:
        result.rejections.append(
            f"Enrichment identity mismatch: researched {result.linkedin_key}, vendor returned {vendor_key}."
        )
        return result

    for key in _PDL_FILLABLE:
        incoming = enriched.get(key)
        if incoming in (None, "", []):
            continue
        if not result.values.get(key):
            result.values[key] = incoming

    return result


def grade(result: TriageResult, *, today: date | None = None) -> TriageResult:
    """Scores a merged result and records what still blocks submission."""
    today = today or date.today()
    values = dict(result.values)
    values.setdefault("is_software_role", is_software_role(values.get("target_role") or ""))
    for column in (
        "email", "email_source", "direct_number", "phone_source", "city", "state",
        "university", "degree", "graduation_year", "skills", "indian_identity",
        "first_name", "last_name",
    ):
        values.setdefault(column, None)
    values.setdefault("indian_identity", "unknown")

    draft = LeadDraft(values)
    result.grade, result.grade_reasons = score_lead(draft, today=today)
    result.checklist = checklist_failures(draft)
    result.values = values
    return result
