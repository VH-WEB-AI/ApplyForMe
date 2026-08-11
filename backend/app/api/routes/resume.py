from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.engines.resume_intelligence import analysis
from app.engines.resume_intelligence import engine as resume_engine  # noqa: F401 -- registers the engine
from app.orchestrator.orchestrator import orchestrator
from app.services.resume_parser import parse_resume

router = APIRouter(prefix="/resume", tags=["resume"])


def _deterministic_analysis(file_bytes: bytes, filename: str, target_role: str | None, target_industry: str | None) -> dict:
    """Shared by /analyze (when no candidate_id is given) and /ats-check: the
    deterministic scores only, no DB write, no LLM call."""
    parsed = parse_resume(file_bytes, filename)
    target_text = f"{target_role or ''} {target_industry or ''}".strip()
    result = analysis.analyze(parsed.sections, raw_text=parsed.raw_text, target_role_text=target_text)

    return {
        "resumeScore": result.resume_score,
        "atsScore": result.ats_score,
        "sectionScores": result.section_scores,
        "weakSections": result.weak_sections,
        "totalExperienceYears": result.total_experience_years,
        "education": result.education,
        "certifications": result.certifications,
    }


@router.post("/analyze")
async def analyze_resume(
    candidate_id: int | None = Form(None),
    target_role: str | None = Form(None),
    target_industry: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    file_bytes = await file.read()

    # No candidate_id -> same contract as /ats-check: deterministic scores
    # only, nothing persisted (ResumeVersion.candidate_id is a NOT NULL FK,
    # so there's no row to write to without one), no LLM call.
    if candidate_id is None:
        return _deterministic_analysis(file_bytes, file.filename, target_role, target_industry)

    payload = {
        "candidate_id": candidate_id,
        "file_bytes": file_bytes,
        "filename": file.filename,
        "target_role": target_role,
        "target_industry": target_industry,
    }
    return orchestrator.handle_request("resume_intelligence", db, payload)


@router.post("/ats-check")
async def check_ats(
    target_role: str | None = Form(None),
    target_industry: str | None = Form(None),
    file: UploadFile = File(...),
) -> dict:
    """Deterministic ATS/resume scoring with no candidate, no DB write, and no
    LLM call -- just upload a file and get scores back. target_role/industry
    are optional and only sharpen the keyword-overlap component if given."""
    file_bytes = await file.read()
    return _deterministic_analysis(file_bytes, file.filename, target_role, target_industry)
