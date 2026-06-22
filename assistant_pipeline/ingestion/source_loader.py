"""RAG source normalisation and document extraction helpers."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set

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


def _source_dedupe_key(source: Dict[str, Any]) -> str:
    if not isinstance(source, dict):
        return ""
    url = source.get("url")
    if isinstance(url, str) and url.strip():
        return f"url:{url.strip()}"
    path = source.get("path")
    if isinstance(path, str) and path.strip():
        try:
            return f"path:{os.path.abspath(path.strip())}"
        except Exception:
            return f"path:{path.strip()}"
    return ""


def _append_unique_source(sources: List[Dict[str, Any]], seen: Set[str], candidate: Dict[str, Any]) -> None:
    key = _source_dedupe_key(candidate)
    if key:
        if key in seen:
            return
        seen.add(key)
    sources.append(candidate)


def _normalise_documents_payload(raw_documents: Any) -> List[Dict[str, Any]]:
    normalised: List[Dict[str, Any]] = []
    if not isinstance(raw_documents, list):
        return normalised
    for item in raw_documents:
        if isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                normalised.append(
                    {
                        "id": item.get("id"),
                        "text": text,
                        "source": item.get("source") or item.get("title") or "inline_document",
                        "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                    }
                )
        elif isinstance(item, str) and item.strip():
            normalised.append({"text": item.strip(), "source": "inline_document"})
    return normalised


def _normalise_records_source(payload: Dict[str, Any], records: List[Any], *, source_label: str) -> Dict[str, Any]:
    source: Dict[str, Any] = {
        "records": records,
        "source": source_label,
    }
    id_field = payload.get("record_id_field")
    if isinstance(id_field, str) and id_field.strip():
        source["id_field"] = id_field.strip()
    text_fields = payload.get("record_text_fields")
    if isinstance(text_fields, list):
        cleaned_fields = [str(item).strip() for item in text_fields if str(item).strip()]
        if cleaned_fields:
            source["text_fields"] = cleaned_fields
    if isinstance(payload.get("metadata"), dict):
        source["metadata"] = dict(payload.get("metadata") or {})
    return source


def coerce_rag_sources(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the supported RAG source payload shapes into one list."""

    sources: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    raw_sources = payload.get("sources")
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if isinstance(item, dict):
                _append_unique_source(sources, seen, item)
            elif isinstance(item, str):
                cleaned = item.strip()
                if not cleaned:
                    continue
                if cleaned.startswith(("http://", "https://")):
                    _append_unique_source(sources, seen, {"url": cleaned})
                else:
                    _append_unique_source(sources, seen, {"path": cleaned})
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, list):
        for path in raw_paths:
            if isinstance(path, str):
                cleaned = path.strip()
                if cleaned:
                    _append_unique_source(sources, seen, {"path": cleaned})
    raw_urls = payload.get("urls")
    if isinstance(raw_urls, list):
        for url in raw_urls:
            if isinstance(url, str):
                cleaned = url.strip()
                if cleaned:
                    _append_unique_source(sources, seen, {"url": cleaned})
    for document_source in _normalise_documents_payload(payload.get("documents")):
        _append_unique_source(sources, seen, document_source)

    raw_records = payload.get("records")
    if isinstance(raw_records, list) and raw_records:
        sources.append(_normalise_records_source(payload, raw_records, source_label="records"))
    crm_export = payload.get("crm_export")
    if isinstance(crm_export, dict):
        crm_records = crm_export.get("records")
        if isinstance(crm_records, list) and crm_records:
            source = _normalise_records_source(crm_export, crm_records, source_label="crm_export")
            source.setdefault("source", "crm_export")
            sources.append(source)
    raw_crm_records = payload.get("crm_records")
    if isinstance(raw_crm_records, list) and raw_crm_records:
        sources.append(_normalise_records_source(payload, raw_crm_records, source_label="crm_records"))
    return sources


def _record_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except Exception:
        return str(value)


def _record_to_text(record: Dict[str, Any], *, text_fields: List[str]) -> str:
    lines: List[str] = []
    fields = text_fields or [str(key) for key in sorted(record.keys())]
    for field in fields:
        if field not in record:
            continue
        value_text = _record_value_text(record.get(field))
        if not value_text:
            continue
        lines.append(f"- {field}: {value_text}")
    if not lines:
        fallback = _record_value_text(record)
        if fallback:
            lines.append(f"- record: {fallback}")
    return "\n".join(lines)


def _record_document(
    source_index: int,
    record_index: int,
    record: Dict[str, Any],
    *,
    source_label: str,
    id_field: str,
    text_fields: List[str],
    base_doc_id: str,
    base_metadata: Dict[str, Any],
) -> Optional[RagDocument]:
    text = _record_to_text(record, text_fields=text_fields)
    if not text:
        return None
    record_identifier = _record_value_text(record.get(id_field)) if id_field else ""
    suffix = record_identifier or f"{record_index:04d}"
    doc_id = f"{base_doc_id}-{suffix}"
    metadata = dict(base_metadata)
    metadata.setdefault("source_type", "structured_records")
    metadata.setdefault("record_index", record_index)
    if record_identifier:
        metadata.setdefault("record_id", record_identifier)
    metadata.setdefault("source_index", source_index)
    return RagDocument(
        doc_id=doc_id,
        source=source_label,
        text=text,
        metadata=metadata,
    )


def _build_record_documents(
    entry: Dict[str, Any],
    *,
    source_index: int,
    remaining_docs: int,
) -> List[RagDocument]:
    records = entry.get("records")
    if not isinstance(records, list) or not records or remaining_docs <= 0:
        return []
    source_label = str(entry.get("source") or entry.get("title") or f"records-{source_index}").strip() or f"records-{source_index}"
    id_field = str(entry.get("id_field") or "id").strip()
    raw_text_fields = entry.get("text_fields")
    text_fields = [str(item).strip() for item in raw_text_fields if str(item).strip()] if isinstance(raw_text_fields, list) else []
    base_doc_id = str(entry.get("id") or f"records-{source_index:03d}").strip() or f"records-{source_index:03d}"
    base_metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    serialisable_records = [(idx, item) for idx, item in enumerate(records[:remaining_docs], start=1) if isinstance(item, dict)]
    if not serialisable_records:
        return []

    worker_count = min(8, max(1, len(serialisable_records)))
    if len(serialisable_records) == 1:
        single = _record_document(
            source_index,
            serialisable_records[0][0],
            serialisable_records[0][1],
            source_label=source_label,
            id_field=id_field,
            text_fields=text_fields,
            base_doc_id=base_doc_id,
            base_metadata=base_metadata,
        )
        return [single] if single is not None else []

    def _build(item: tuple[int, Dict[str, Any]]) -> Optional[RagDocument]:
        record_idx, record_value = item
        return _record_document(
            source_index,
            record_idx,
            record_value,
            source_label=source_label,
            id_field=id_field,
            text_fields=text_fields,
            base_doc_id=base_doc_id,
            base_metadata=base_metadata,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        built = list(executor.map(_build, serialisable_records))
    return [doc for doc in built if doc is not None][:remaining_docs]


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
        if len(docs) >= max_docs:
            break
        if not isinstance(entry, dict):
            continue
        records = entry.get("records")
        if isinstance(records, list):
            remaining_docs = max_docs - len(docs)
            docs.extend(
                _build_record_documents(
                    entry,
                    source_index=idx,
                    remaining_docs=remaining_docs,
                )
            )
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
