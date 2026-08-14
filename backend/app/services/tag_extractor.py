"""Tag Extractor: JobPosting.tags extraction, plus the resume<->job overlap score.

extract_tags() -- lightweight, open-source keyphrase extraction via YAKE
(statistical, no model download, CPU-only -- see docs/SCORING_LOGIC.md).
Used for JobPosting.tags (job_ingest.py): ingestion runs in bulk (hundreds
to thousands of postings per scraper run), where a per-job OpenAI call
would be materially slower and cost real money at that volume.

ResumeVersion.tags used to have its own dedicated OpenAI call here
(extract_tags_openai); that's been folded into the Resume Intelligence
engine's single structured LLM call (app/engines/resume_intelligence/engine.py)
so tag extraction, scoring, and the rest of the analysis all come from one
coherent read of the resume instead of two separate calls that could disagree.

Caveat: resume tags (LLM, clean semantic phrases like "backend developer")
and job tags (YAKE, raw n-grams) come from stylistically different
extractors. tag_overlap_score() does exact string matching, so this
asymmetry can suppress overlap that would otherwise be obvious to a human
(e.g. resume tag "python" vs job tag "python developer" don't match as
strings even though they clearly should). Worth revisiting if match quality
looks off in practice -- either upgrading job tags too (at the
ingestion-cost tradeoff above) or moving to fuzzy/substring matching in
tag_overlap_score() instead of exact set intersection.
"""

import yake

_EXTRACTOR = yake.KeywordExtractor(lan="en", n=2, top=30, dedupLim=0.9)


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


def tag_overlap_score(resume_tags: list[str], job_tags: list[str]) -> float:
    """Fraction of the job's tags also present in the resume's tags, in [0, 1]."""
    if not job_tags:
        return 0.0
    job_set = {t.lower() for t in job_tags}
    resume_set = {t.lower() for t in resume_tags}
    return len(job_set & resume_set) / len(job_set)
