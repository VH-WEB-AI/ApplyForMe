"""Tag Extractor: lightweight, open-source keyphrase extraction via YAKE
(statistical, no model download, CPU-only -- see docs/SCORING_LOGIC.md).

Tags are computed once at creation time (resume upload / job ingest) and
stored on the row, so Job Match compares small precomputed tag lists on
every match instead of re-running extraction over full text each time.

extract_tags_openai() is a separate, LLM-based path used only where better
tag quality is worth the added API cost/latency (see its docstring) --
everything stored for Job Match still goes through the free extract_tags().
"""

import yake

from app.services.json_formatter import parse_llm_json
from app.services.llm_gateway import chat_completion

_EXTRACTOR = yake.KeywordExtractor(lan="en", n=2, top=30, dedupLim=0.9)

_OPENAI_TAGS_SYSTEM_PROMPT = (
    "You extract concise resume tags: skills, technologies, tools, and role "
    "keywords a recruiter would search for. Do not include company names, "
    "person names, or generic words. Return JSON: {\"tags\": [...]}."
)


def extract_tags(text: str, max_tags: int = 15) -> list[str]:
    """Extracts up to max_tags keyphrases, most relevant first.

    YAKE's own score is inverted (lower = more relevant) -- callers never see
    it; this returns plain lowercased keyphrase strings ready for storage/
    comparison. Returns [] for empty input or if extraction ever errors out,
    since a tagging failure should degrade matching, not break resume
    upload or job ingest.
    """
    if not text or not text.strip():
        return []
    try:
        keywords = _EXTRACTOR.extract_keywords(text)
    except Exception:
        return []
    return [keyword.lower() for keyword, _score in keywords[:max_tags]]


def extract_tags_openai(text: str, max_tags: int = 15) -> list[str]:
    """LLM-based tag extraction -- an explicit per-call OpenAI cost/latency
    tradeoff for cleaner tags than YAKE's raw n-gram statistics (which can
    fragment a company/institution name across several tags). Used only by
    /resume/ats-check and /resume/analyze's no-candidate-id fallback, both
    of which accept this as the price of better quality here.

    Falls back to [] on any failure (bad JSON, API error) rather than
    raising, so a flaky/rate-limited LLM call degrades tags, not the whole
    response.
    """
    if not text or not text.strip():
        return []
    try:
        result = chat_completion(
            _OPENAI_TAGS_SYSTEM_PROMPT,
            text[:6000],
            json_mode=True,
            temperature=0.0,
        )
        tags = parse_llm_json(result.content).get("tags", [])
        return [str(t).strip().lower() for t in tags if str(t).strip()][:max_tags]
    except Exception:
        return []


def tag_overlap_score(resume_tags: list[str], job_tags: list[str]) -> float:
    """Fraction of the job's tags also present in the resume's tags, in [0, 1]."""
    if not job_tags:
        return 0.0
    job_set = {t.lower() for t in job_tags}
    resume_set = {t.lower() for t in resume_tags}
    return len(job_set & resume_set) / len(job_set)
