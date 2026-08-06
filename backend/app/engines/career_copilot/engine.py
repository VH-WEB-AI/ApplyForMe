from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.career_health import CareerHealthSnapshot
from app.db.models.conversation import Conversation, ConversationMessage
from app.db.models.core import CandidateProfile
from app.db.models.jobs import Application, JobMatch
from app.db.models.resume import ResumeScore, ResumeVersion
from app.engines.career_copilot import analysis
from app.engines.career_copilot.schemas import COPILOT_LLM_JSON_SCHEMA, CopilotLLMOutput
from app.orchestrator.engine_base import Engine
from app.orchestrator.registry import register_engine
from app.services import embedding_generator
from app.services.document_chunking import chunk_text
from app.services.prompt_builder import PromptSpec

SYSTEM_PROMPT = (
    "You are the Career Copilot inside ApplyForMe's Career Command Center. You are not "
    "a general-purpose chatbot: you answer using the candidate's own data below, and "
    "nothing else."
)

BUSINESS_RULES = [
    "Never invent resume content, applications, interviews, or scores not present in the context.",
    "If a piece of context needed to answer is marked unavailable, say so and suggest how "
    "the candidate can provide it (e.g. upload a resume) instead of guessing.",
    "Use the conversation history to answer follow-up questions naturally, without repeating "
    "yourself unnecessarily.",
]

MAX_HISTORY_MESSAGES = 10
RAG_TOP_K = 3


def _get_or_create_conversation(db: Session, candidate_id: int, conversation_id: int | None) -> Conversation:
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id)
        if conversation is not None and conversation.candidate_id == candidate_id:
            return conversation
    conversation = Conversation(candidate_id=candidate_id)
    db.add(conversation)
    db.flush()
    return conversation


def _ensure_resume_chunks_embedded(db: Session, resume_version: ResumeVersion) -> None:
    chunks = chunk_text(resume_version.raw_text, chunk_size=300, overlap=50)
    for index, chunk in enumerate(chunks):
        embedding_generator.get_or_create_embedding(
            db,
            owner_type="resume_chunk",
            owner_id=analysis.resume_chunk_owner_id(resume_version.id, index),
            text=chunk,
            store_content=True,
        )


class CareerCopilotEngine(Engine):
    name = "career_copilot"
    response_schema = CopilotLLMOutput

    def gather_context(self, db: Session, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = payload["candidate_id"]
        question = payload["question"]

        profile = db.get(CandidateProfile, candidate_id)
        if profile is None:
            raise ValueError(f"Candidate profile {candidate_id} not found.")

        conversation = _get_or_create_conversation(db, candidate_id, payload.get("conversation_id"))
        prior_messages = list(
            db.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation.id)
                .order_by(ConversationMessage.id.desc())
                .limit(MAX_HISTORY_MESSAGES)
            ).all()
        )
        prior_messages.reverse()

        resume_version = db.scalar(
            select(ResumeVersion)
            .where(ResumeVersion.candidate_id == candidate_id)
            .order_by(ResumeVersion.id.desc())
        )
        resume_score_row = None
        retrieved_snippets: list[str] = []
        if resume_version is not None:
            resume_score_row = db.scalar(
                select(ResumeScore)
                .where(ResumeScore.resume_version_id == resume_version.id)
                .order_by(ResumeScore.id.desc())
            )
            _ensure_resume_chunks_embedded(db, resume_version)
            top_chunks = embedding_generator.top_k_similar(
                db,
                owner_type="resume_chunk",
                owner_id_range=analysis.resume_chunk_owner_id_range(resume_version.id),
                query_text=question,
                k=RAG_TOP_K,
            )
            retrieved_snippets = [chunk.content for chunk in top_chunks]

        career_health = db.scalar(
            select(CareerHealthSnapshot)
            .where(CareerHealthSnapshot.candidate_id == candidate_id)
            .order_by(CareerHealthSnapshot.id.desc())
        )
        job_matches = list(
            db.scalars(
                select(JobMatch)
                .where(JobMatch.candidate_id == candidate_id)
                .order_by(JobMatch.match_score.desc())
                .limit(3)
            ).all()
        )
        has_application_row = (
            db.scalar(select(Application).where(Application.candidate_id == candidate_id).limit(1))
            is not None
        )

        return {
            "candidate_id": candidate_id,
            "conversation_id": conversation.id,
            "question": question,
            "intent": analysis.detect_intent(question),
            "prior_messages": [{"role": m.role, "content": m.content} for m in prior_messages],
            "retrieved_snippets": retrieved_snippets,
            "has_resume": resume_version is not None,
            "resume_score": resume_score_row.resume_score if resume_score_row else None,
            "ats_score": resume_score_row.ats_score if resume_score_row else None,
            "has_career_health": career_health is not None,
            "career_health_score": career_health.overall_score if career_health else None,
            "weak_areas": career_health.weak_areas if career_health else [],
            "has_job_matches": bool(job_matches),
            "top_job_matches": [
                {"jobPostingId": jm.job_posting_id, "matchScore": jm.match_score}
                for jm in job_matches
            ],
            "has_applications": has_application_row,
        }

    def build_prompt_spec(self, context: dict[str, Any]) -> PromptSpec:
        instructions = (
            f"The candidate's intent appears to be '{context['intent']}'. Answer their question: "
            f"\"{context['question']}\"\n"
            "Use the candidate context and retrieved resume snippets below. If a needed piece of "
            "context is marked unavailable (has_resume/has_career_health/has_job_matches/"
            "has_applications is false), tell the candidate what's missing instead of guessing."
        )
        return PromptSpec(
            system_prompt=SYSTEM_PROMPT,
            business_rules=BUSINESS_RULES,
            engine_instructions=instructions,
            json_schema=COPILOT_LLM_JSON_SCHEMA,
            candidate_context={
                "has_resume": context["has_resume"],
                "resume_score": context["resume_score"],
                "ats_score": context["ats_score"],
                "has_career_health": context["has_career_health"],
                "career_health_score": context["career_health_score"],
                "weak_areas": context["weak_areas"],
                "has_job_matches": context["has_job_matches"],
                "top_job_matches": context["top_job_matches"],
                "has_applications": context["has_applications"],
            },
            extra_context={
                "conversation_history": context["prior_messages"],
                "retrieved_resume_snippets": context["retrieved_snippets"],
            },
        )

    def postprocess(
        self,
        db: Session,
        payload: dict[str, Any],
        context: dict[str, Any],
        llm_output: CopilotLLMOutput,
    ) -> dict[str, Any]:
        db.add(
            ConversationMessage(
                conversation_id=context["conversation_id"],
                role="user",
                content=context["question"],
                intent=context["intent"],
                context_used={},
            )
        )
        db.add(
            ConversationMessage(
                conversation_id=context["conversation_id"],
                role="assistant",
                content=llm_output.answer,
                intent=context["intent"],
                context_used={
                    "has_resume": context["has_resume"],
                    "has_career_health": context["has_career_health"],
                    "has_job_matches": context["has_job_matches"],
                    "retrieved_snippets": context["retrieved_snippets"],
                },
            )
        )
        db.flush()

        return {
            "conversationId": context["conversation_id"],
            "intent": context["intent"],
            "answer": llm_output.answer,
            "followUpSuggestions": llm_output.follow_up_suggestions,
        }


register_engine(CareerCopilotEngine())
