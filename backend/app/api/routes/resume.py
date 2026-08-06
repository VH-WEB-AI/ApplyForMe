from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.engines.resume_intelligence import engine as resume_engine  # noqa: F401 -- registers the engine
from app.orchestrator.orchestrator import orchestrator

router = APIRouter(prefix="/resume", tags=["resume"])


@router.post("/analyze")
async def analyze_resume(
    candidate_id: int = Form(...),
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
