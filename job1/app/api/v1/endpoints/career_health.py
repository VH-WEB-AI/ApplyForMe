from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.orchestrator.ai_orchestrator import ai_orchestrator

router = APIRouter(prefix="/career-health", tags=["career-health"])


@router.get("")
async def get_career_health(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    response = await ai_orchestrator.dispatch(
        intent="career_health",
        payload={},
        db=db,
        user_id=user.id,
    )
    return response.result
