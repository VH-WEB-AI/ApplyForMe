from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import CandidateProfile, User
from app.orchestrator.context_manager import context_manager
from app.schemas.user import CandidateProfileOut, CandidateProfileUpdate, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.get("/me/profile", response_model=CandidateProfileOut)
async def get_my_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    return result.scalar_one()


@router.put("/me/profile", response_model=CandidateProfileOut)
async def update_my_profile(
    payload: CandidateProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    profile = result.scalar_one()

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    await context_manager.invalidate(user.id)  # profile changed -> stale context cache
    return profile
