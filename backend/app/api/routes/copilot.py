from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.engines.career_copilot import engine as career_copilot_engine  # noqa: F401 -- registers the engine
from app.orchestrator.orchestrator import orchestrator
from app.schemas.api import CopilotAskRequest

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post("/ask")
def ask_copilot(body: CopilotAskRequest, db: Session = Depends(get_db)) -> dict:
    # candidate_id is optional on the request shape but not actually
    # skippable here -- there's no resume/history to ground an answer in
    # without one.
    if body.candidate_id is None:
        raise HTTPException(status_code=400, detail="candidate_id is required to ask the copilot.")
    payload = {
        "candidate_id": body.candidate_id,
        "conversation_id": body.conversation_id,
        "question": body.question,
    }
    return orchestrator.handle_request("career_copilot", db, payload)
