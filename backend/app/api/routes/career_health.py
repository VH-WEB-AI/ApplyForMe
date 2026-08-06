from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.engines.career_health import engine as career_health_engine  # noqa: F401 -- registers the engine
from app.orchestrator.orchestrator import orchestrator

router = APIRouter(prefix="/career-health", tags=["career-health"])


@router.get("/{candidate_id}")
def get_career_health(candidate_id: int, db: Session = Depends(get_db)) -> dict:
    return orchestrator.handle_request("career_health", db, {"candidate_id": candidate_id})
