import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.conversation import Conversation, ConversationMessage, MessageRole
from app.models.resume import Resume, ResumeStatus
from app.models.user import User
from app.orchestrator.ai_orchestrator import ai_orchestrator
from app.schemas.copilot import CopilotMessageRequest, CopilotMessageResponse
from app.shared_services.embedding_service import embedding_service

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post("/message", response_model=CopilotMessageResponse)
async def send_message(
    payload: CopilotMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Get or create conversation
    if payload.conversation_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == payload.conversation_id, Conversation.user_id == user.id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("Conversation not found")

        latest_resume_result = await db.execute(
            select(Resume)
            .where(Resume.user_id == user.id, Resume.status == ResumeStatus.SCORED)
            .order_by(Resume.created_at.desc())
            .limit(1)
        )
        latest_resume = latest_resume_result.scalar_one_or_none()
        if latest_resume and latest_resume.created_at > conversation.created_at:
            conversation = Conversation(user_id=user.id, title=payload.message[:80])
            db.add(conversation)
            await db.flush()
    else:
        conversation = Conversation(user_id=user.id, title=payload.message[:80])
        db.add(conversation)
        await db.flush()

    # Pull recent history for prompt context (last 10 messages)
    history_result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(10)
    )
    history_msgs = list(reversed(history_result.scalars().all()))
    history_text = "\n".join(f"[{m.role.value}] {m.content}" for m in history_msgs)

    # Store the user's message (embedding generated for future RAG retrieval)
    user_embedding = await embedding_service.get_embedding(payload.message)
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=payload.message,
        embedding=user_embedding,
    )
    db.add(user_msg)
    await db.commit()

    # Dispatch to Engine 4 via orchestrator
    response = await ai_orchestrator.dispatch(
        intent="chat",
        payload={
            "db": db,
            "user_id": user.id,
            "message": payload.message,
            "conversation_history": history_text,
        },
        db=db,
        user_id=user.id,
    )

    reply = response.result["reply"]
    reply_embedding = await embedding_service.get_embedding(reply)
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=reply,
        embedding=reply_embedding,
    )
    db.add(assistant_msg)
    await db.commit()

    return CopilotMessageResponse(
        conversation_id=conversation.id,
        reply=reply,
        suggested_actions=response.result.get("suggested_actions", []),
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(ConversationMessage)
        .join(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user.id)
        .order_by(ConversationMessage.created_at)
    )
    return [{"role": m.role.value, "content": m.content, "created_at": m.created_at} for m in result.scalars()]
