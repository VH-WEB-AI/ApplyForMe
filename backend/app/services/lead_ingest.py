"""Maps master-sheet lead rows (SOP s20) onto ResearchLead rows.

The SOP's duplicate rule (s19) is the interesting part: the normalized LinkedIn
URL is the primary identifier, and a repeat submission must *fill in missing
fields on the existing row* rather than create a second one.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.leads import ResearchLead
from app.schemas.api import ResearchLeadRow
from app.services.lead_qualification import (
    LeadDraft,
    extract_visa_signals,
    has_open_to_work_signal,
    is_software_role,
    normalize_linkedin_url,
    recruiter_rejection_reason,
    score_lead,
)

# Fields a later submission is allowed to fill in when the stored row is blank.
_MERGEABLE_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "direct_number",
    "city",
    "state",
    "university",
    "degree",
    "graduation_year",
    "target_role",
    "skills",
    "visa_statement",
    "job_search_evidence",
    "evidence_url",
    "evidence_date",
    "email_source",
    "phone_source",
    "date_researched",
    "researcher_name",
    "notes",
)


def row_to_lead_kwargs(row: ResearchLeadRow, *, today: date | None = None) -> dict | None:
    """Builds ResearchLead column values from a sheet row, deriving every signal
    the SOP says must come from stated text rather than a researcher's judgement.

    Returns None when the row has no usable LinkedIn profile URL -- without the
    dedup key there is no safe way to store it.
    """
    today = today or date.today()
    linkedin_key = normalize_linkedin_url(row.linkedin_url)
    if not linkedin_key:
        return None

    # Everything the candidate publicly stated, pooled for signal detection.
    stated_text = " ".join(
        filter(None, [row.visa_statement, row.job_search_evidence, row.target_role])
    )
    visa = extract_visa_signals(" ".join(filter(None, [row.visa_statement, row.job_search_evidence])))

    kwargs = dict(
        first_name=row.first_name.strip(),
        last_name=row.last_name.strip(),
        email=(row.email or "").strip() or None,
        direct_number=(row.direct_number or "").strip() or None,
        linkedin_url=row.linkedin_url.strip(),
        linkedin_key=linkedin_key,
        city=row.city,
        state=row.state,
        university=row.university,
        degree=row.degree,
        graduation_year=row.graduation_year,
        target_role=row.target_role.strip(),
        skills=row.skills,
        is_software_role=is_software_role(f"{row.target_role} {' '.join(row.skills)}"),
        # A researcher's checkbox can only ever confirm what the stated text
        # already says; it can never create a signal on its own (SOP s23 rule 3).
        f1_signal=visa["f1_signal"] and (row.f1_signal or visa["opt_signal"] or visa["cpt_signal"]),
        opt_signal=visa["opt_signal"],
        stem_opt_signal=visa["stem_opt_signal"],
        cpt_signal=visa["cpt_signal"],
        visa_statement=row.visa_statement.strip(),
        open_to_work_signal=row.open_to_work_signal or has_open_to_work_signal(stated_text),
        job_search_evidence=row.job_search_evidence.strip(),
        evidence_url=row.evidence_url,
        evidence_date=row.evidence_date,
        indian_identity=row.indian_identity,
        email_source=row.email_source,
        phone_source=row.phone_source,
        source=row.source,
        date_researched=row.date_researched or today,
        researcher_name=row.researcher_name.strip(),
        contact_status=row.contact_status,
        notes=row.notes.strip(),
    )

    rejection = recruiter_rejection_reason(f"{row.target_role} {row.job_search_evidence} {row.notes}")
    kwargs["is_rejected"] = bool(rejection)
    kwargs["rejection_reason"] = rejection

    priority, _reasons = score_lead(LeadDraft(kwargs), today=today)
    kwargs["lead_priority"] = priority
    return kwargs


def _merge_missing(existing: ResearchLead, kwargs: dict) -> bool:
    """Fills blank fields on an existing lead from a newer submission (SOP s19).

    Returns True if anything changed. Never overwrites a value that is already
    populated -- a re-research pass adds information, it doesn't replace it.
    """
    changed = False
    for field in _MERGEABLE_FIELDS:
        new_value = kwargs.get(field)
        if new_value in (None, "", [], 0):
            continue
        if not getattr(existing, field, None):
            setattr(existing, field, new_value)
            changed = True

    # Signals are monotonic: once a candidate has publicly stated OPT, a later
    # row that simply didn't record it shouldn't clear the flag.
    for flag in ("f1_signal", "opt_signal", "stem_opt_signal", "cpt_signal", "open_to_work_signal"):
        if kwargs.get(flag) and not getattr(existing, flag):
            setattr(existing, flag, True)
            changed = True

    if kwargs.get("indian_identity") == "yes" and existing.indian_identity != "yes":
        existing.indian_identity = "yes"
        changed = True

    if changed:
        existing.is_software_role = existing.is_software_role or kwargs.get("is_software_role", False)
        existing.lead_priority = score_lead(existing)[0]
    return changed


def ingest_lead_rows(
    db: Session, rows: list[ResearchLeadRow], *, today: date | None = None
) -> dict:
    """Creates a ResearchLead per new row and merges repeats into the existing row.

    Returns counts of created / updated / skipped, plus the rejected subset so
    the caller can show researchers which rows failed SOP s22.
    """
    created = 0
    updated = 0
    skipped = 0
    rejected: list[str] = []
    seen: dict[str, ResearchLead] = {}

    for row in rows:
        kwargs = row_to_lead_kwargs(row, today=today)
        if kwargs is None:
            skipped += 1
            continue

        key = kwargs["linkedin_key"]
        existing = seen.get(key) or db.scalar(
            select(ResearchLead).where(ResearchLead.linkedin_key == key)
        )
        if existing is not None:
            if _merge_missing(existing, kwargs):
                updated += 1
            else:
                skipped += 1
            seen[key] = existing
            continue

        lead = ResearchLead(**kwargs)
        db.add(lead)
        db.flush()  # assign the id so a later duplicate in the same batch merges
        seen[key] = lead
        created += 1
        if lead.is_rejected:
            rejected.append(lead.linkedin_key)

    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "rejected": rejected}
