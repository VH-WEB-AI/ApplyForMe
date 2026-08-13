from app.engines.career_copilot.resume_rag import ResumeRAG


def test_weak_areas_query_is_treated_as_resume_query():
    rag = ResumeRAG()

    assert rag.is_resume_query("What are weak areas in my resume?")
    assert rag.is_resume_query("What gaps should I fix?")


def test_broad_review_query_keeps_resume_order():
    rag = ResumeRAG()
    resume_text = "\n\n".join(
        [
            "Contact\ncandidate@example.com\n" + ("contact details " * 80),
            "Summary\nBackend engineer building APIs.\n" + ("summary details " * 80),
            "Experience\nBuilt FastAPI services.\n" + ("experience details " * 80),
            "Projects\nCreated RAG prototype.\n" + ("project details " * 80),
        ]
    )

    chunks = rag._select_chunks(resume_text, "What weak areas should I improve?", max_chunks=3)

    assert len(chunks) == 3
    assert chunks[0].startswith("Contact\ncandidate@example.com")
    assert chunks[1].startswith("Summary\nBackend engineer building APIs.")
    assert chunks[2].startswith("Experience\nBuilt FastAPI services.")
