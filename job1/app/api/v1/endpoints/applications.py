import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.application import Application, ApplicationStatus
from app.models.user import User

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("")
async def list_applications(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Application).where(Application.user_id == user.id))
    return result.scalars().all()


@router.patch("/{application_id}/status")
async def update_status(
    application_id: uuid.UUID,
    status: ApplicationStatus,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Application).where(Application.id == application_id, Application.user_id == user.id)
    )
    application = result.scalar_one_or_none()
    if not application:
        raise NotFoundError("Application not found")

    application.status = status
    application.timeline = [*application.timeline, {"status": status.value}]
    await db.commit()
    await db.refresh(application)
    return application
