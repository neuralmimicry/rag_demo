"""Deterministic follow-up query rewriting for retrieval-oriented routes.

The assistant keeps this stage lexical and predictable so follow-up turns can
benefit from conversation context without requiring another model call before
retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Sequence

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_QUERY_CHARS = 480
_MAX_ANCHOR_CHARS = 240
_FOLLOW_UP_PREFIXES = (
    "and ",
    "also ",
    "then ",
    "so ",
    "but ",
    "what about",
    "how about",
    "what else",
    "who else",
    "where else",
    "when else",
    "why is that",
    "why does that",
    "how does that",
    "tell me more",
    "more about",
    "expand on",
    "dig into",
)
_CONTEXT_PRONOUNS = {
    "it",
    "its",
    "they",
    "them",
    "their",
    "that",
    "this",
    "these",
    "those",
    "he",
    "she",
    "him",
    "her",
    "there",
    "here",
    "same",
}
_LOW_SIGNAL_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "else",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "more",
    "of",
    "on",
    "or",
    "same",
    "should",
    "so",
    "tell",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
}


@dataclass(frozen=True)
class QueryRewrite:
    """Result of a deterministic retrieval-query rewrite attempt."""

    original_query: str
    retrieval_query: str
    rewritten: bool
    reason: str
    history_turns: int
    anchor_text: str = ""


def _clean_text(value: Any, *, max_chars: int = 0) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if not text:
        return ""
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text or "")]


def _informative_token_count(text: str) -> int:
    return len([token for token in _tokens(text) if token not in _LOW_SIGNAL_WORDS])


def _needs_context(query_text: str) -> bool:
    cleaned = _clean_text(query_text)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered.startswith(_FOLLOW_UP_PREFIXES):
        return True
    tokens = _tokens(cleaned)
    if not tokens:
        return False
    if tokens[0] in _CONTEXT_PRONOUNS:
        return True
    if any(token in _CONTEXT_PRONOUNS for token in tokens) and _informative_token_count(cleaned) <= 2:
        return True
    if len(tokens) <= 3 and _informative_token_count(cleaned) <= 1:
        return True
    return False


def _turn_candidates(turn: Dict[str, Any], *, role: str) -> Iterable[str]:
    if role == "user":
        for field in ("rewritten_query", "prompt_text", "requirements_text", "content"):
            yield _clean_text(turn.get(field), max_chars=_MAX_ANCHOR_CHARS)
    else:
        yield _clean_text(turn.get("content"), max_chars=_MAX_ANCHOR_CHARS)


def _find_anchor(query_text: str, turns: Sequence[Dict[str, Any]], *, max_turns: int = 8) -> str:
    cleaned_query = _clean_text(query_text).lower()
    assistant_fallback = ""
    window = [dict(turn) for turn in turns[-max_turns:] if isinstance(turn, dict)]
    for turn in reversed(window):
        role = str(turn.get("role") or "").strip().lower()
        if role == "assistant" and not assistant_fallback:
            for candidate in _turn_candidates(turn, role="assistant"):
                if candidate and candidate.lower() != cleaned_query:
                    assistant_fallback = candidate
                    break
        if role != "user":
            continue
        for candidate in _turn_candidates(turn, role="user"):
            if not candidate or candidate.lower() == cleaned_query:
                continue
            if _informative_token_count(candidate) <= 1 and turn.get("rewritten_query") != candidate:
                continue
            return candidate
    return assistant_fallback


def _merge_queries(anchor_text: str, query_text: str) -> str:
    anchor = _clean_text(anchor_text, max_chars=_MAX_ANCHOR_CHARS).rstrip(" .?!")
    query = _clean_text(query_text)
    if not anchor:
        return query
    combined = _clean_text(f"{anchor} {query}", max_chars=_MAX_QUERY_CHARS)
    return combined or query


def rewrite_query(query_text: str, turns: Sequence[Dict[str, Any]]) -> QueryRewrite:
    """Return a standalone retrieval query when recent turns imply a follow-up."""

    cleaned_query = _clean_text(query_text, max_chars=_MAX_QUERY_CHARS)
    history_turns = len([turn for turn in turns if isinstance(turn, dict)])
    if not cleaned_query:
        return QueryRewrite("", "", False, "empty_query", history_turns)
    if history_turns <= 0:
        return QueryRewrite(cleaned_query, cleaned_query, False, "no_history", 0)
    if not _needs_context(cleaned_query):
        return QueryRewrite(cleaned_query, cleaned_query, False, "query_already_standalone", history_turns)
    anchor_text = _find_anchor(cleaned_query, turns)
    if not anchor_text:
        return QueryRewrite(cleaned_query, cleaned_query, False, "no_anchor", history_turns)
    retrieval_query = _merge_queries(anchor_text, cleaned_query)
    if retrieval_query.lower() == cleaned_query.lower():
        return QueryRewrite(cleaned_query, cleaned_query, False, "rewrite_not_needed", history_turns, anchor_text)
    return QueryRewrite(cleaned_query, retrieval_query, True, "follow_up_rewritten", history_turns, anchor_text)
