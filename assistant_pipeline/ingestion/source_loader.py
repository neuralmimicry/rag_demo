"""RAG source normalisation and document extraction helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from refiner.document_schema import coerce_document_elements
from refiner.file_converter import FileConverter
from refiner.rag_engine import RagDocument
from refiner.web_research import fetch_url_content, fetch_youtube_transcript, is_youtube_url


def _normalise_allowed_roots(allowed_roots: Optional[List[str]]) -> List[str]:
    roots: List[str] = []
    for root in allowed_roots or []:
        if not root:
            continue
        try:
            roots.append(os.path.abspath(root))
        except Exception:
            continue
    return sorted(set(roots))


def _is_path_allowed(path: str, allowed_roots: Optional[List[str]] = None) -> bool:
    if not path:
        return False
    roots = _normalise_allowed_roots(allowed_roots)
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False
    for root in roots:
        if abs_path == root or abs_path.startswith(root + os.sep):
            return True
    return False


def _safe_source_label(path: str) -> str:
    return os.path.basename(path) or path


def coerce_rag_sources(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the supported RAG source payload shapes into one list."""

    sources: List[Dict[str, Any]] = []
    raw_sources = payload.get("sources")
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if isinstance(item, dict):
                sources.append(item)
            elif isinstance(item, str):
                cleaned = item.strip()
                if not cleaned:
                    continue
                if cleaned.startswith(("http://", "https://")):
                    sources.append({"url": cleaned})
                else:
                    sources.append({"path": cleaned})
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, list):
        for path in raw_paths:
            if isinstance(path, str):
                sources.append({"path": path})
    raw_urls = payload.get("urls")
    if isinstance(raw_urls, list):
        for url in raw_urls:
            if isinstance(url, str):
                cleaned = url.strip()
                if cleaned:
                    sources.append({"url": cleaned})
    return sources


def build_rag_documents(
    sources: List[Dict[str, Any]],
    *,
    max_docs: int,
    max_doc_bytes: int,
    allowed_roots: Optional[List[str]] = None,
) -> List[RagDocument]:
    """Build indexed RAG documents from local files, URLs, or inline payloads."""

    docs: List[RagDocument] = []
    converter = FileConverter(llm=None, llm_params=None)
    normalised_roots = _normalise_allowed_roots(allowed_roots)
    for idx, entry in enumerate(sources[:max_docs], start=1):
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        path = entry.get("path")
        url = entry.get("url")
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        source_label = entry.get("source") or entry.get("title")
        elements = coerce_document_elements(entry.get("elements") or [])
        if path and isinstance(path, str):
            path = path.strip()
            if not path:
                continue
            if normalised_roots and not _is_path_allowed(path, normalised_roots):
                continue
            if not os.path.exists(path):
                continue
            try:
                if os.path.getsize(path) > max_doc_bytes:
                    continue
            except Exception:
                continue
            source_label = source_label or _safe_source_label(path)
            extraction = converter.extract(path)
            text = extraction.text
            if isinstance(text, str) and text.startswith("Error:"):
                continue
            elements = extraction.elements
            extraction_meta = extraction.summary_metadata()
            merged_meta = dict(metadata)
            for key, value in extraction_meta.items():
                merged_meta.setdefault(key, value)
            merged_meta.setdefault("source_path", path)
            metadata = merged_meta
        elif url and isinstance(url, str):
            url = url.strip()
            if not url:
                continue
            merged_meta = dict(metadata)
            merged_meta.setdefault("source_url", url)
            if is_youtube_url(url):
                try:
                    text, transcript_meta = fetch_youtube_transcript(url, timeout=20)
                except Exception:
                    continue
                for key, value in transcript_meta.items():
                    merged_meta.setdefault(key, value)
                source_label = source_label or str(transcript_meta.get("title") or url).strip()
            else:
                text = fetch_url_content(
                    url,
                    timeout=20,
                    max_bytes=max_doc_bytes,
                    file_converter=converter,
                    raise_on_error=False,
                )
                if not text:
                    continue
                source_label = source_label or url
            try:
                if max_doc_bytes and len(text.encode("utf-8")) > max_doc_bytes:
                    continue
            except Exception:
                if max_doc_bytes and len(text) > max_doc_bytes:
                    continue
            metadata = merged_meta
        if not text or not isinstance(text, str):
            continue
        doc_id = entry.get("id") or f"doc-{idx:03d}"
        docs.append(
            RagDocument(
                doc_id=str(doc_id),
                source=str(source_label or doc_id),
                text=text,
                metadata=metadata,
                elements=elements,
            )
        )
    return docs
