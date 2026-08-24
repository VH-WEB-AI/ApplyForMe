import csv
import io
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import get_db
from app.db.models.leads import ResearchLead
from app.schemas.api import (
    LeadBulkEnrichRequest,
    LeadIngestRequest,
    LeadStatusUpdateRequest,
    ResearchLeadResponse,
    ResearchLeadRow,
)
from app.services.apollo_client import (
    ApolloError,
    ApolloNotConfigured,
    ApolloPlanError,
    ApolloUnavailable,
    match_person,
)
from app.services.apollo_client import health as apollo_health
from app.services.lead_enrichment import enrich_lead, enrich_leads
from app.services.lead_ingest import ingest_lead_rows, row_to_lead_kwargs
from app.services.lead_qualification import (
    LeadDraft,
    checklist_failures,
    normalize_linkedin_url,
    score_lead,
)

router = APIRouter(prefix="/leads", tags=["leads"])

# SOP s20, in sheet order -- also the header row of the CSV export.
SHEET_COLUMNS = [
    ("Email", "email"),
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("LinkedIn URL", "linkedin_url"),
    ("Direct Number", "direct_number"),
    ("City", "city"),
    ("State", "state"),
    ("University", "university"),
    ("Degree", "degree"),
    ("Graduation Year", "graduation_year"),
    ("Current/Target Role", "target_role"),
    ("Skills", "skills"),
    ("F-1 Signal", "f1_signal"),
    ("OPT Signal", "opt_signal"),
    ("STEM OPT Signal", "stem_opt_signal"),
    ("CPT Signal", "cpt_signal"),
    ("Open-to-Work Signal", "open_to_work_signal"),
    ("Indian Identity Publicly Stated", "indian_identity"),
    ("Job Search Evidence", "job_search_evidence"),
    ("Evidence/Post URL", "evidence_url"),
    ("Email Source", "email_source"),
    ("Phone Source", "phone_source"),
    ("Lead Priority", "lead_priority"),
    ("Date Researched", "date_researched"),
    ("Researcher Name", "researcher_name"),
    ("Contact Status", "contact_status"),
    ("Notes", "notes"),
]


@router.get("", response_model=list[ResearchLeadResponse])
def list_leads(
    priority: str | None = Query(None, description="A+, A, B or C"),
    state: str | None = None,
    contact_status: str | None = None,
    include_rejected: bool = False,
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db),
) -> list[ResearchLead]:
    """Lists researched leads, newest first. Rejected rows (SOP s22) are hidden
    unless explicitly asked for, so the outreach team never sees recruiters."""
    query = select(ResearchLead).order_by(ResearchLead.created_at.desc())
    if priority:
        query = query.where(ResearchLead.lead_priority == priority)
    if state:
        query = query.where(ResearchLead.state == state)
    if contact_status:
        query = query.where(ResearchLead.contact_status == contact_status)
    if not include_rejected:
        query = query.where(ResearchLead.is_rejected.is_(False))
    return list(db.scalars(query.limit(limit)).all())


@router.post("/ingest")
def ingest_leads(body: LeadIngestRequest, db: Session = Depends(get_db)) -> dict:
    """Receives master-sheet rows, derives every SOP signal, grades each lead
    A+/A/B/C, and merges repeats into the existing row by LinkedIn URL."""
    result = ingest_lead_rows(db, body.leads)
    return {"received": len(body.leads), **result}


@router.post("/validate")
def validate_lead(row: ResearchLeadRow) -> dict:
    """Dry-runs a single row through the SOP s29 checklist without storing it,
    so a researcher can see what is missing before submitting."""
    kwargs = row_to_lead_kwargs(row)
    if kwargs is None:
        raise HTTPException(
            status_code=400, detail="A valid linkedin.com/in/... profile URL is required."
        )
    draft = LeadDraft(kwargs)
    priority, reasons = score_lead(draft)
    failures = checklist_failures(draft)
    return {
        "linkedinKey": kwargs["linkedin_key"],
        "leadPriority": priority,
        "reasons": reasons,
        "checklistFailures": failures,
        "isRejected": kwargs["is_rejected"],
        "rejectionReason": kwargs["rejection_reason"],
        "readyToSubmit": not failures and not kwargs["is_rejected"],
    }


@router.get("/duplicate")
def check_duplicate(linkedin_url: str, db: Session = Depends(get_db)) -> dict:
    """SOP s19 -- check a profile against the master sheet before researching it."""
    key = normalize_linkedin_url(linkedin_url)
    if not key:
        raise HTTPException(
            status_code=400, detail="A valid linkedin.com/in/... profile URL is required."
        )
    existing = db.scalar(select(ResearchLead).where(ResearchLead.linkedin_key == key))
    return {
        "linkedinKey": key,
        "isDuplicate": existing is not None,
        "leadId": existing.id if existing else None,
        "contactStatus": existing.contact_status if existing else None,
    }


# --- Apollo entitlement probe ------------------------------------------------

# A verdict we could not establish is retried soon; a settled one is trusted for
# apollo_status_cache_seconds.
_TRANSIENT_STATUS_TTL_SECONDS = 30.0

_apollo_status_cache: tuple[float, dict] | None = None


def reset_apollo_status_cache() -> None:
    """Clears the memoised verdict. Call after changing APOLLO_API_KEY; tests use it."""
    global _apollo_status_cache
    _apollo_status_cache = None


def _read_status_cache() -> dict | None:
    if _apollo_status_cache is None:
        return None
    expires_at, payload = _apollo_status_cache
    if time.monotonic() >= expires_at:
        return None
    return {**payload, "cached": True}


def _store_status(payload: dict) -> dict:
    global _apollo_status_cache
    ttl = (
        _TRANSIENT_STATUS_TTL_SECONDS
        if payload["status"] == "unknown"
        else get_settings().apollo_status_cache_seconds
    )
    _apollo_status_cache = (time.monotonic() + ttl, payload)
    return {**payload, "cached": False}


def _status(status: str, *, configured: bool, available: bool, detail: str) -> dict:
    return {
        "configured": configured,
        "enrichmentAvailable": available,
        "status": status,
        "detail": detail,
    }


@router.get("/apollo/status")
def apollo_status() -> dict:
    """Reports whether Apollo enrichment is usable, so the UI can hide the button
    instead of failing per-lead.

    `auth/health` passes on every plan -- a Basic/Trial key authenticates fine and
    still 403s every people endpoint -- so only calling a people endpoint proves
    entitlement. That probe is billable and rate-limited (600/hr), hence the
    cache: this must not fire once per page render.

    `status` is the field to branch on:
      not_configured -- no APOLLO_API_KEY; enrichment is off by choice
      auth_failed    -- the key is rejected; fix the credential
      plan_blocked   -- the key works, the plan does not include people endpoints
      available      -- enrichment will work
      unknown        -- Apollo was unreachable; verdict withheld, not assumed good
    """
    cached = _read_status_cache()
    if cached is not None:
        return cached

    try:
        apollo_health()
    except ApolloNotConfigured:
        return _store_status(
            _status("not_configured", configured=False, available=False, detail="APOLLO_API_KEY is not set.")
        )
    except ApolloUnavailable as exc:
        return _store_status(_status("unknown", configured=True, available=False, detail=str(exc)))
    except ApolloPlanError as exc:
        return _store_status(_status("plan_blocked", configured=True, available=False, detail=str(exc)))
    except ApolloError as exc:
        return _store_status(_status("auth_failed", configured=True, available=False, detail=str(exc)))

    # A name that cannot match keeps the probe free: Apollo bills on a returned
    # person, and reveal_personal_emails stays off.
    try:
        match_person(first_name="Zzzq", last_name="Nonexistentperson")
    except ApolloPlanError as exc:
        return _store_status(_status("plan_blocked", configured=True, available=False, detail=str(exc)))
    except ApolloUnavailable as exc:
        return _store_status(_status("unknown", configured=True, available=False, detail=str(exc)))
    except ApolloError:
        # A no-match or a 422 still proves the endpoint is entitled and reachable.
        pass

    return _store_status(
        _status("available", configured=True, available=True, detail="Apollo people enrichment is available.")
    )


@router.post("/{lead_id}/enrich")
def enrich_one_lead(lead_id: int, db: Session = Depends(get_db)) -> dict:
    """Fills blank contact fields on a lead from Apollo, after confirming the
    Apollo record is the same person (SOP s15/s17)."""
    lead = db.get(ResearchLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail=f"No lead with id {lead_id}.")
    return {"leadId": lead_id, **enrich_lead(db, lead)}


@router.post("/enrich")
def enrich_many_leads(body: LeadBulkEnrichRequest, db: Session = Depends(get_db)) -> dict:
    """Enriches several leads in one pass, ten per Apollo call.

    Declared before /{lead_id} routes would shadow it, and kept a POST because it
    spends credits. Unknown ids are reported rather than 404ing the whole batch --
    a researcher selecting rows in a UI should not lose the run to one stale id.
    """
    if not body.lead_ids:
        return {"enriched": 0, "reports": {}}

    ordered_ids = list(dict.fromkeys(body.lead_ids))
    found = db.scalars(select(ResearchLead).where(ResearchLead.id.in_(ordered_ids))).all()
    by_id = {lead.id: lead for lead in found}

    reports: dict[int, dict] = {
        lead_id: {"status": "not_found", "detail": f"No lead with id {lead_id}.", "fieldsFilled": []}
        for lead_id in ordered_ids
        if lead_id not in by_id
    }
    reports.update(enrich_leads(db, [by_id[i] for i in ordered_ids if i in by_id]))

    return {
        "enriched": sum(1 for r in reports.values() if r["status"] == "enriched"),
        "reports": {str(k): reports[k] for k in ordered_ids if k in reports},
    }


@router.patch("/{lead_id}", response_model=ResearchLeadResponse)
def update_lead_status(
    lead_id: int, body: LeadStatusUpdateRequest, db: Session = Depends(get_db)
) -> ResearchLead:
    lead = db.get(ResearchLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail=f"No lead with id {lead_id}.")
    lead.contact_status = body.contact_status
    if body.notes is not None:
        lead.notes = body.notes
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/export")
def export_leads(
    priority: str | None = None,
    include_rejected: bool = False,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Streams the master sheet back out as CSV in the SOP s20 column order."""
    query = select(ResearchLead).order_by(ResearchLead.lead_priority, ResearchLead.id)
    if priority:
        query = query.where(ResearchLead.lead_priority == priority)
    if not include_rejected:
        query = query.where(ResearchLead.is_rejected.is_(False))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for header, _ in SHEET_COLUMNS])
    for lead in db.scalars(query).all():
        writer.writerow([_cell(getattr(lead, attr)) for _, attr in SHEET_COLUMNS])
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="applyforme_leads.csv"'},
    )


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)
