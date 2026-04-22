"""Shared text normalisation helpers for retrieval modules."""

from __future__ import annotations

import re
from typing import Any, List, Tuple

WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-/]{2,}")
WS_RE = re.compile(r"\s+")
LEADING_QUERY_NOISE_RE = re.compile(
    r"^(?:please\s+|kindly\s+|can you\s+|could you\s+|would you\s+|will you\s+|"
    r"tell me(?: about)?\s+|show me\s+|explain(?: to me)?\s+|describe\s+|give me\s+|provide\s+|"
    r"what(?: is| are| about)?\s+|how(?: does| do| did| can| should| is| are)\s+|"
    r"why(?: does| do| is| are)\s+)",
    re.IGNORECASE,
)
TRAILING_QUERY_NOISE_RE = re.compile(r"\b(?:please|thanks?|thank you|work|works|working)\b[?.!:\s]*$", re.IGNORECASE)
STOPWORDS = {
    "about",
    "after",
    "all",
    "also",
    "and",
    "does",
    "from",
    "how",
    "into",
    "that",
    "the",
    "their",
    "then",
    "these",
    "this",
    "those",
    "what",
    "when",
    "where",
    "which",
    "with",
    "work",
}
SUFFIXES = ("ingly", "edly", "ing", "ed", "ies", "s")


def clean_retrieval_text(text: Any) -> str:
    """Return a whitespace-normalised text form."""

    return WS_RE.sub(" ", str(text or "").strip())


def canonical_retrieval_term(term: str) -> str:
    """Return a canonical retrieval term for matching and grading."""

    cleaned = str(term or "").strip().lower()
    if len(cleaned) <= 3:
        return cleaned
    for suffix in SUFFIXES:
        if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 3:
            if suffix == "ies":
                cleaned = f"{cleaned[:-3]}y"
            else:
                cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def retrieval_keywords(text: Any) -> Tuple[str, ...]:
    """Return canonical deduplicated terms, including stopwords when present."""

    seen = set()
    keywords: List[str] = []
    for raw in WORD_RE.findall(clean_retrieval_text(text).lower()):
        parts = [raw] + [part for part in re.split(r"[-_/]", raw) if len(part) >= 3]
        for part in parts:
            canonical = canonical_retrieval_term(part)
            if len(canonical) < 3 or canonical in seen:
                continue
            keywords.append(canonical)
            seen.add(canonical)
    return tuple(keywords)


def retrieval_query_terms(text: Any) -> Tuple[str, ...]:
    """Return preferred query terms, falling back to all canonical keywords."""

    preferred: List[str] = []
    seen_preferred = set()
    fallback = retrieval_keywords(text)
    for canonical in fallback:
        if canonical in STOPWORDS or canonical in seen_preferred:
            continue
        preferred.append(canonical)
        seen_preferred.add(canonical)
    return tuple(preferred or fallback)


def trim_retrieval_stopword_edges(text: Any, *, min_terms: int = 2) -> str:
    """Trim stopwords from the edges of a phrase without collapsing it entirely."""

    words = [word for word in clean_retrieval_text(text).split(" ") if word]
    while len(words) > max(1, int(min_terms)) and canonical_retrieval_term(words[0]) in STOPWORDS:
        words.pop(0)
    while len(words) > max(1, int(min_terms)) and canonical_retrieval_term(words[-1]) in STOPWORDS:
        words.pop()
    return " ".join(words).strip()


def core_retrieval_phrase(text: Any, *, min_terms: int = 2) -> str:
    """Strip conversational scaffolding from a retrieval phrase."""

    cleaned = clean_retrieval_text(text).strip(" ?.!:")
    if not cleaned:
        return ""
    cleaned = cleaned.replace('"', " ").replace("'", " ")
    cleaned = LEADING_QUERY_NOISE_RE.sub("", cleaned).strip()
    cleaned = TRAILING_QUERY_NOISE_RE.sub("", cleaned).strip()
    cleaned = trim_retrieval_stopword_edges(cleaned, min_terms=min_terms)
    cleaned = cleaned.strip(" ?.!:")
    keywords = retrieval_keywords(cleaned)
    if len(keywords) == 1:
        return keywords[0]
    return cleaned
