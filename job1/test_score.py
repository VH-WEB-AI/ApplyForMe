import asyncio
import uuid
from app.db.session import db_session_ctx
from app.models.resume import Resume, ResumeStatus
from sqlalchemy import select, desc

async def check_latest():
    async with db_session_ctx() as db:
        result = await db.execute(select(Resume).order_by(desc(Resume.created_at)).limit(1))
        r = result.scalar_one_or_none()
        if r:
            print(f"status={r.status.value} ats={r.ats_score} skills={r.extracted_skills[:3]}")
        else:
            print("no resumes found")

asyncio.run(check_latest())
