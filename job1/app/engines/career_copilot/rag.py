"""
RAG retrieval for the Career Copilot: pulls the most relevant past
conversation messages (pgvector cosine search) to ground the model's
response in the candidate's own conversation history.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationMessage
from app.shared_services.embedding_service import embedding_service


class ConversationRAG:
    async def retrieve_relevant_context(
        self, db: AsyncSession, user_id: uuid.UUID, query: str, top_k: int = 5
    ) -> list[str]:
        query_embedding = await embedding_service.get_embedding(query)
        from app.models.conversation import Conversation

        base_stmt = (
            select(ConversationMessage)
            .join(ConversationMessage.conversation)
            .where(ConversationMessage.embedding.is_not(None), Conversation.user_id == user_id)
        )

        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = base_stmt.order_by(ConversationMessage.embedding.cosine_distance(query_embedding)).limit(top_k)
            result = await db.execute(stmt)
            messages = result.scalars().all()
            return [f"[{m.role.value}] {m.content}" for m in messages]

        result = await db.execute(base_stmt)
        messages = result.scalars().all()
        messages = sorted(
            messages,
            key=lambda message: embedding_service.cosine_similarity(query_embedding, message.embedding or []),
            reverse=True,
        )[:top_k]
        return [f"[{m.role.value}] {m.content}" for m in messages]


conversation_rag = ConversationRAG()
