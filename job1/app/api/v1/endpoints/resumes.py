import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.resume import Resume, ResumeStatus
from app.models.user import User
from app.orchestrator.ai_orchestrator import ai_orchestrator
from app.orchestrator.context_manager import context_manager
from app.schemas.resume import ResumeOut
from app.shared_services.embedding_service import embedding_service
from app.shared_services.resume_parser import resume_parser
from app.workers.tasks.resume_tasks import process_resume_task

settings = get_settings()
logger = get_logger(__name__)
router = APIRouter(prefix="/resumes", tags=["resumes"])

ROOT_DIR = Path(__file__).resolve().parents[4]
UPLOAD_DIR = ROOT_DIR / "data" / "uploads" / "resumes"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


async def _process_resume_inline(resume_id: str) -> None:
    """Process resume inline (used when Celery worker is not running)."""
    from app.db.session import db_session_ctx
    async with db_session_ctx() as db:
        result = await db.execute(select(Resume).where(Resume.id == uuid.UUID(resume_id)))
        resume = result.scalar_one()

        resume.status = ResumeStatus.PARSING
        await db.flush()

        raw_text = resume_parser.parse(resume.file_path)
        resume.raw_text = raw_text
        resume.status = ResumeStatus.PARSED
        await db.flush()

        orchestrator_response = await ai_orchestrator.dispatch(
            intent="score_resume",
            payload={"resume_text": raw_text},
            db=db,
            user_id=resume.user_id,
            load_context=False,
        )
        analysis = orchestrator_response.result

        resume.ats_score = analysis["ats_score"]
        resume.resume_score = analysis["resume_score"]
        resume.extracted_skills = analysis["extracted_skills"]
        resume.suggestions = analysis["suggestions"]
        resume.structured_data = {
            "work_history": analysis["work_history"],
            "education": analysis["education"],
        }
        resume.status = ResumeStatus.SCORED
        resume.embedding = await embedding_service.get_embedding(raw_text)
        await context_manager.invalidate(resume.user_id)
        await db.flush()
        logger.info("resume_processed_inline", resume_id=resume_id)


def _dispatch_resume_task(resume_id: str) -> bool:
    """Try to send to Celery; return False if broker is unreachable."""
    try:
        process_resume_task.delay(resume_id)
        return True
    except Exception as exc:
        logger.warning("celery_unavailable_falling_back_to_inline", error=str(exc))
        return False


@router.post("/upload", response_model=ResumeOut, status_code=201)
async def upload_resume(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationFailedError(f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}")

    resume_id = uuid.uuid4()
    dest_path = UPLOAD_DIR / f"{resume_id}{ext}"
    contents = await file.read()
    dest_path.write_bytes(contents)

    resume = Resume(
        id=resume_id,
        user_id=user.id,
        file_name=file.filename,
        file_path=str(dest_path),
        status=ResumeStatus.UPLOADED,
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)
    await context_manager.invalidate(user.id)

    # Try Celery first; fall back to inline async processing if broker is down
    if not _dispatch_resume_task(str(resume.id)):
        background_tasks.add_task(_process_resume_inline, str(resume.id))

    return resume


@router.get("/{resume_id}", response_model=ResumeOut)
async def get_resume(resume_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id))
    resume = result.scalar_one_or_none()
    if not resume:
        raise NotFoundError("Resume not found")
    return resume


@router.get("", response_model=list[ResumeOut])
async def list_resumes(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc())
    )
    return result.scalars().all()
