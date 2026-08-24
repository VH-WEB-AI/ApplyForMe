"""Enriches a researched lead with Apollo contact data.

The SOP is emphatic that enrichment must not blend two people together (s15
"Never copy contact information from a different person with a similar name",
s17 "Confirm profile match"). So every Apollo result is identity-checked against
the stored lead *before* any contact field is written, and a failed check writes
nothing at all rather than writing what it can.
"""

import logging

from sqlalchemy.orm import Session

from app.db.models.leads import ResearchLead
from app.services.apollo_client import BULK_MATCH_MAX, ApolloError, bulk_match, match_person
from app.services.lead_qualification import normalize_linkedin_url

logger = logging.getLogger(__name__)


def verify_identity(lead: ResearchLead, person: dict) -> tuple[bool, str]:
    """Decides whether an Apollo record is the same human as the stored lead.

    Returns (matched, reason). The LinkedIn URL is authoritative when Apollo
    supplies one; otherwise we require a surname match plus one corroborating
    signal (state or employer), which is the weakest evidence the SOP tolerates.
    """
    apollo_key = normalize_linkedin_url(person.get("linkedin_url") or "")
    if apollo_key and lead.linkedin_key:
        if apollo_key == lead.linkedin_key:
            return True, "LinkedIn URL matches"
        return False, (
            f"LinkedIn URL mismatch: lead is {lead.linkedin_key}, Apollo returned {apollo_key}"
        )

    lead_last = (lead.last_name or "").strip().lower()
    apollo_last = (person.get("last_name") or "").strip().lower()
    if not lead_last or lead_last != apollo_last:
        return False, "No LinkedIn URL on the Apollo record and surnames do not match"

    lead_state = (lead.state or "").strip().lower()
    apollo_state = (person.get("state") or "").strip().lower()
    if lead_state and apollo_state and lead_state == apollo_state:
        return True, "Surname and state match (no LinkedIn URL on the Apollo record)"

    return False, "Surname matches but nothing corroborates it; refusing to attach contact data"


def _professional_email(person: dict) -> str | None:
    """Apollo's `email` is the work address; `personal_emails` is a separate list
    we deliberately do not request (see apollo_client.match_person)."""
    email = (person.get("email") or "").strip()
    if not email or email.lower().startswith("email_not_unlocked"):
        return None
    return email


def _apply_match(lead: ResearchLead, person: dict | None) -> dict:
    """Identity-checks one Apollo record against one lead and fills blank fields.

    Does not commit -- the caller decides, so a bulk run can write once for the
    whole batch. Never overwrites data a researcher already entered.
    """
    if person is None:
        return {"status": "no_match", "detail": "Apollo has no record for this profile.", "fieldsFilled": []}

    matched, reason = verify_identity(lead, person)
    if not matched:
        logger.warning("Apollo identity check failed for lead %s: %s", lead.id, reason)
        return {"status": "identity_mismatch", "detail": reason, "fieldsFilled": []}

    filled: list[str] = []

    email = _professional_email(person)
    if email and not lead.email:
        lead.email = email
        lead.email_source = "Apollo"
        filled.append("email")

    # Non-contact fields are safe to backfill too, now that identity is confirmed.
    for lead_field, apollo_field in (("city", "city"), ("state", "state")):
        value = (person.get(apollo_field) or "").strip()
        if value and not getattr(lead, lead_field):
            setattr(lead, lead_field, value)
            filled.append(lead_field)

    if not lead.target_role:
        title = (person.get("title") or "").strip()
        if title:
            lead.target_role = title
            filled.append("target_role")

    return {
        "status": "enriched" if filled else "nothing_to_fill",
        "detail": reason,
        "fieldsFilled": filled,
        "apolloEmailStatus": person.get("email_status"),
    }


def enrich_lead(db: Session, lead: ResearchLead, *, commit: bool = True) -> dict:
    """Fills blank fields on `lead` from Apollo. Never overwrites existing data.

    Returns a report describing what happened, so the caller can show the
    researcher why a lead was or wasn't enriched.
    """
    try:
        person = match_person(
            linkedin_url=lead.linkedin_url or None,
            first_name=lead.first_name,
            last_name=lead.last_name,
        )
    except ApolloError as exc:
        return {"status": "error", "detail": str(exc), "fieldsFilled": []}

    report = _apply_match(lead, person)

    if commit and report["fieldsFilled"]:
        db.commit()
        db.refresh(lead)

    return report


def _lead_detail(lead: ResearchLead) -> dict | None:
    """Builds the bulk_match entry for a lead, or None if it cannot be matched."""
    detail = {
        "linkedin_url": (lead.linkedin_url or "").strip(),
        "first_name": (lead.first_name or "").strip(),
        "last_name": (lead.last_name or "").strip(),
    }
    if not detail["linkedin_url"] and not (detail["first_name"] and detail["last_name"]):
        return None
    return {k: v for k, v in detail.items() if v}


def enrich_leads(db: Session, leads: list[ResearchLead], *, commit: bool = True) -> dict[int, dict]:
    """Enriches many leads in batches of ten, returning {lead_id: report}.

    Apollo bills bulk_match per person exactly as match_person does, so this is
    not cheaper in credits -- it is ten times fewer HTTP calls against a 600/hr
    rate limit, which is what makes enriching a day's research practical.

    A batch that fails reports the error against every lead in it and the run
    continues, so one bad chunk does not abandon the rest.
    """
    reports: dict[int, dict] = {}
    pending: list[tuple[ResearchLead, dict]] = []

    for lead in leads:
        detail = _lead_detail(lead)
        if detail is None:
            reports[lead.id] = {
                "status": "skipped",
                "detail": "Lead has neither a LinkedIn URL nor both names; nothing to match on.",
                "fieldsFilled": [],
            }
            continue
        pending.append((lead, detail))

    filled_any = False
    for start in range(0, len(pending), BULK_MATCH_MAX):
        chunk = pending[start : start + BULK_MATCH_MAX]
        try:
            people = bulk_match([detail for _, detail in chunk])
        except ApolloError as exc:
            for lead, _ in chunk:
                reports[lead.id] = {"status": "error", "detail": str(exc), "fieldsFilled": []}
            continue

        for (lead, _), person in zip(chunk, people):
            report = _apply_match(lead, person)
            reports[lead.id] = report
            filled_any = filled_any or bool(report["fieldsFilled"])

    if commit and filled_any:
        db.commit()

    return reports
