"""Keyword Extractor: lightweight, deterministic keyword frequency extraction."""

import re
from collections import Counter

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "that", "this",
    "it", "we", "you", "your", "our", "their", "will", "shall", "can", "should",
    "would", "have", "has", "had", "not", "no", "if", "than", "then", "so", "also",
    "into", "such", "these", "those", "over", "under", "per", "via", "including",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#./-]{1,}")


def _tokenize(text: str) -> list[str]:
    # Trailing '.'/'-' are usually sentence punctuation, not part of the token
    # (an inner '.' is kept so things like "node.js" survive intact).
    return [w.lower().rstrip("./-") for w in _WORD_RE.findall(text)]


def extract_keywords(text: str, top_n: int = 30) -> list[str]:
    words = [w for w in _tokenize(text) if w not in _STOPWORDS and len(w) > 2]
    counts = Counter(words)
    return [word for word, _ in counts.most_common(top_n)]


def keyword_overlap_score(resume_text: str, target_text: str) -> float:
    """Fraction of target keywords also present in the resume text, in [0, 1]."""
    target_keywords = set(extract_keywords(target_text, top_n=50))
    if not target_keywords:
        return 0.0
    resume_words = set(_tokenize(resume_text))
    matched = target_keywords & resume_words
    return len(matched) / len(target_keywords)
