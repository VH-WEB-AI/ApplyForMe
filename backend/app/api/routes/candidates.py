from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models.core import CandidateProfile, User
from app.schemas.api import CreateCandidateRequest, CreateCandidateResponse

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.post("", response_model=CreateCandidateResponse)
def create_candidate(body: CreateCandidateRequest, db: Session = Depends(get_db)) -> CreateCandidateResponse:
    user = User(email=body.email, full_name=body.full_name)
    db.add(user)
    db.flush()

    profile = CandidateProfile(
        user_id=user.id,
        target_role=body.target_role,
        target_industry=body.target_industry,
        experience_level=body.experience_level,
        visa_status=body.visa_status,
        location=body.location,
        desired_salary_min=body.desired_salary_min,
        desired_salary_max=body.desired_salary_max,
        linkedin_url=body.linkedin_url,
        github_url=body.github_url,
        portfolio_url=body.portfolio_url,
    )
    db.add(profile)
    db.commit()

    return CreateCandidateResponse(userId=user.id, candidateId=profile.id)
