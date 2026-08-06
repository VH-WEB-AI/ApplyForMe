from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin

EMBEDDING_DIM = 1536  # text-embedding-3-small


class EmbeddingRecord(TimestampMixin, Base):
    """Generic embedding store used for resumes, job descriptions, skills, and conversations.

    `content` holds the original chunk text so RAG retrieval (Career Copilot) can return
    the matched passage itself, not just a vector; it is unused for whole-document
    embeddings (resume/job) where the source row already holds the full text.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(50), index=True)  # resume | job | resume_chunk
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
