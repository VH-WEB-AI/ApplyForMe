from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.engines.resume_intelligence import engine as resume_engine  # noqa: F401 -- registers the engine
from app.orchestrator.orchestrator import orchestrator

router = APIRouter(prefix="/resume", tags=["resume"])


@router.post("/analyze")
async def analyze_resume(
    candidate_id: int | None = Form(None),
    target_role: str | None = Form(None),
    target_industry: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    file_bytes = await file.read()
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
    db: Session = Depends(get_db),
) -> dict:
    """Same full analysis as /analyze (deterministic scores + LLM recommendations/
    missing_skills/rewrite_suggestions/tags) but candidate_id-less and unconditionally
    so: nothing is persisted (ResumeVersion.candidate_id is a NOT NULL FK, so there's
    no row to write to), regardless of whether a caller tries to pass one."""
    file_bytes = await file.read()
    payload = {
        "candidate_id": None,
        "file_bytes": file_bytes,
        "filename": file.filename,
        "target_role": target_role,
        "target_industry": target_industry,
    }
    return orchestrator.handle_request("resume_intelligence", db, payload)
