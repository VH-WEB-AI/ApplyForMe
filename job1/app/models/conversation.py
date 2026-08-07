import uuid
import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, UUIDPK

settings = get_settings()


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Conversation(Base, UUIDPK, TimestampMixin):
    """A Career Copilot chat thread."""

    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.created_at"
    )


class ConversationMessage(Base, UUIDPK, TimestampMixin):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, values_callable=lambda e: [m.value for m in e]))
    content: Mapped[str] = mapped_column(Text)
    # Embedding for RAG-based conversation memory retrieval
    embedding: Mapped[list] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
