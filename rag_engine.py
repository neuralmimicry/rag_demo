"""Small, dependency-light RAG primitives for local document retrieval.

The index still defaults to simple BM25-style lexical ranking, but ingestion is
now capable of preserving document layout metadata so that chunks can align with
pages, headings, tables, and other logical blocks instead of arbitrary character
windows.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from assistant_pipeline.retrieval.dense_artifacts import (
    DenseIndexArtifact,
    attach_dense_artifact,
    dense_artifact_path_for_index,
    ensure_dense_artifact,
)
from document_schema import DocumentElement, coerce_document_elements, format_locator, format_source_citation


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/]{2,}")


def _tokenize(text: str) -> List[str]:
    """Tokenize text into normalized search terms and split sub-terms."""
    if not text:
        return []
    tokens = []
    for match in TOKEN_RE.findall(text):
        token = match.strip().lower()
        if len(token) < 3:
            continue
        tokens.append(token)
        for part in re.split(r"[-_/]", token):
            if len(part) >= 3:
                tokens.append(part)
    return tokens


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks suitable for retrieval indexing."""
    if not text:
        return []
    cleaned = text.strip()
    if not cleaned:
        return []
    if chunk_size <= 0:
        return [cleaned]
    overlap = max(0, min(overlap, chunk_size - 1)) if chunk_size > 1 else 0
    chunks: List[str] = []
    start = 0
    total = len(cleaned)
    while start < total:
        end = min(total, start + chunk_size)
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break
        start = end - overlap if overlap else end
    return chunks


@dataclass
class RagDocument:
    """Input document to be indexed for retrieval."""

    doc_id: str
    source: str
    text: str
    metadata: Dict[str, Any]
    elements: List[DocumentElement] = field(default_factory=list)


@dataclass
class RagChunk:
    """Indexed chunk with token stats used for ranking."""

    chunk_id: str
    doc_id: str
    source: str
    text: str
    tokens: Dict[str, int]
    length: int
    metadata: Dict[str, Any]
    citation: str = ""


@dataclass
class RagMatch:
    """Search result emitted by :meth:`RagIndex.search`."""

    chunk_id: str
    source: str
    score: float
    text: str
    metadata: Dict[str, Any]
    citation: str = ""


@dataclass
class _ChunkPayload:
    """Internal representation used while building a retrieval index."""

    text: str
    metadata: Dict[str, Any]
    citation: str


def _document_elements(doc: RagDocument) -> List[DocumentElement]:
    if doc.elements:
        return coerce_document_elements(doc.elements)
    raw = doc.metadata.get("document_elements") if isinstance(doc.metadata, dict) else None
    if isinstance(raw, list):
        return coerce_document_elements(raw)
    return []


def _split_large_element(element: DocumentElement, chunk_size: int, overlap: int) -> List[str]:
    text = element.normalized_text()
    if not text:
        return []
    if element.element_type == "table":
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) >= 3:
            rows: List[str] = []
            current = ""
            for line in lines:
                candidate = f"{current}\n{line}".strip() if current else line
                if current and len(candidate) > chunk_size:
                    rows.append(current.strip())
                    current = line
                else:
                    current = candidate
            if current.strip():
                rows.append(current.strip())
            if rows:
                return rows
    return chunk_text(text, chunk_size=chunk_size, overlap=overlap)


def _chunk_locator_metadata(elements: List[DocumentElement]) -> Dict[str, Any]:
    pages = [element.page for element in elements if element.page is not None]
    unique_types: List[str] = []
    for element in elements:
        if element.element_type and element.element_type not in unique_types:
            unique_types.append(element.element_type)
    metadata: Dict[str, Any] = {
        "element_types": unique_types,
        "element_ids": [element.element_id for element in elements if element.element_id],
    }
    if pages:
        metadata["page_start"] = min(pages)
        metadata["page_end"] = max(pages)
    single_page = bool(pages) and min(pages) == max(pages)
    if single_page:
        blocks = [element.block_index for element in elements if element.block_index is not None]
        if blocks:
            metadata["block_start"] = min(blocks)
            metadata["block_end"] = max(blocks)
    locators = [element.locator() for element in elements if element.locator()]
    if locators:
        metadata["locators"] = locators[:16]
    heading_paths = [element.heading_path for element in elements if element.heading_path]
    if heading_paths:
        metadata["heading_path"] = list(heading_paths[-1])
    return metadata


def _build_citation(source: str, metadata: Dict[str, Any]) -> str:
    locator = format_locator(
        page_start=metadata.get("page_start"),
        page_end=metadata.get("page_end"),
        block_start=metadata.get("block_start"),
        block_end=metadata.get("block_end"),
    )
    return format_source_citation(source, locator)


def _payload_from_elements(doc: RagDocument, elements: List[DocumentElement]) -> _ChunkPayload:
    text = "\n\n".join(element.normalized_text() for element in elements if element.normalized_text()).strip()
    metadata = dict(doc.metadata or {})
    metadata.update(_chunk_locator_metadata(elements))
    citation = _build_citation(doc.source, metadata)
    metadata["citation"] = citation
    return _ChunkPayload(text=text, metadata=metadata, citation=citation)


def _structured_chunk_payloads(doc: RagDocument, chunk_size: int, overlap: int) -> List[_ChunkPayload]:
    elements = _document_elements(doc)
    if not elements:
        fallback_payloads = []
        for text in chunk_text(doc.text, chunk_size=chunk_size, overlap=overlap):
            metadata = dict(doc.metadata or {})
            locator_hint = str(metadata.get("locator_summary") or "").strip()
            citation = format_source_citation(doc.source, locator_hint)
            metadata["citation"] = citation
            fallback_payloads.append(_ChunkPayload(text=text, metadata=metadata, citation=citation))
        return fallback_payloads

    payloads: List[_ChunkPayload] = []
    buffer: List[DocumentElement] = []
    buffer_length = 0

    def flush() -> None:
        nonlocal buffer
        nonlocal buffer_length
        if not buffer:
            return
        payload = _payload_from_elements(doc, buffer)
        if payload.text:
            payloads.append(payload)
        buffer = []
        buffer_length = 0

    for element in elements:
        text = element.normalized_text()
        if not text:
            continue
        element_length = len(text)
        same_page_as_buffer = bool(buffer) and buffer[-1].page == element.page
        starts_new_section = element.element_type == "heading" and buffer_length >= max(160, chunk_size // 3)
        page_boundary = bool(buffer) and not same_page_as_buffer and buffer_length >= max(200, chunk_size // 2)
        exceeds_target = bool(buffer) and buffer_length + element_length > chunk_size

        # Flush on strong layout boundaries so headings stay with the section that
        # follows them and tables do not get merged into unrelated content.
        if starts_new_section or page_boundary or exceeds_target:
            flush()

        if element_length > chunk_size and element.element_type not in {"image"}:
            fragments = _split_large_element(element, chunk_size, overlap)
            for part_idx, fragment in enumerate(fragments, start=1):
                fragment_element = DocumentElement(
                    element_id=f"{element.element_id}-f{part_idx:02d}",
                    element_type=element.element_type,
                    text=fragment,
                    page=element.page,
                    block_index=element.block_index,
                    markdown=fragment,
                    bbox=list(element.bbox) if element.bbox else None,
                    caption=element.caption,
                    confidence=element.confidence,
                    heading_path=list(element.heading_path or []),
                    metadata=dict(element.metadata or {}),
                )
                payload = _payload_from_elements(doc, [fragment_element])
                payload.metadata["fragment_index"] = part_idx
                payload.metadata["fragment_count"] = len(fragments)
                payloads.append(payload)
            continue

        buffer.append(element)
        buffer_length += element_length
    flush()
    return payloads


class RagIndex:
    """In-memory BM25-like index built from :class:`RagDocument` entries."""

    def __init__(self, name: str, chunks: List[RagChunk], idf: Dict[str, float], avg_len: float):
        self.name = name
        self.chunks = chunks
        self.idf = idf
        self.avg_len = avg_len or 1.0
        self.dense_artifact: Optional[DenseIndexArtifact] = None
        self._dense_chunk_lookup: Dict[str, RagChunk] = {}

    @classmethod
    def build(
        cls,
        name: str,
        documents: Iterable[RagDocument],
        *,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
        max_chunks: Optional[int] = None,
    ) -> "RagIndex":
        """Create an index from documents with optional chunk limits."""
        chunks: List[RagChunk] = []
        for doc in documents:
            payloads = _structured_chunk_payloads(doc, chunk_size, chunk_overlap)
            for idx, payload in enumerate(payloads, start=1):
                tokens = Counter(_tokenize(payload.text))
                if not tokens:
                    continue
                chunk_id = f"{doc.doc_id}:{idx:04d}"
                chunks.append(
                    RagChunk(
                        chunk_id=chunk_id,
                        doc_id=doc.doc_id,
                        source=doc.source,
                        text=payload.text,
                        tokens=dict(tokens),
                        length=sum(tokens.values()),
                        metadata=dict(payload.metadata or {}),
                        citation=payload.citation,
                    )
                )
                if max_chunks and len(chunks) >= max_chunks:
                    break
            if max_chunks and len(chunks) >= max_chunks:
                break
        if not chunks:
            return cls(name=name, chunks=[], idf={}, avg_len=1.0)
        df: Dict[str, int] = {}
        for chunk in chunks:
            for term in chunk.tokens:
                df[term] = df.get(term, 0) + 1
        total_docs = len(chunks)
        idf = {
            term: math.log(1 + (total_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }
        avg_len = sum(chunk.length for chunk in chunks) / max(1, total_docs)
        return cls(name=name, chunks=chunks, idf=idf, avg_len=avg_len)

    def search(self, query: str, limit: int = 5, min_score: float = 0.0) -> List[RagMatch]:
        """Return the top-N BM25-ranked matches for the query."""
        tokens = _tokenize(query)
        if not tokens or not self.chunks:
            return []
        query_terms = Counter(tokens)
        k1 = 1.5
        b = 0.75
        results: List[RagMatch] = []
        for chunk in self.chunks:
            score = 0.0
            for term, qf in query_terms.items():
                tf = chunk.tokens.get(term)
                if not tf:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = tf + k1 * (1 - b + b * (chunk.length / self.avg_len))
                score += idf * ((tf * (k1 + 1)) / denom) * (1 + math.log(1 + qf))
            if score >= min_score:
                results.append(
                    RagMatch(
                        chunk_id=chunk.chunk_id,
                        source=chunk.source,
                        score=score,
                        text=chunk.text,
                        metadata=dict(chunk.metadata or {}),
                        citation=chunk.citation,
                    )
                )
        results.sort(key=lambda item: (-item.score, item.source, item.chunk_id))
        return results[:limit]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize index contents for JSON persistence."""
        return {
            "name": self.name,
            "avg_len": self.avg_len,
            "idf": self.idf,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "source": chunk.source,
                    "text": chunk.text,
                    "tokens": chunk.tokens,
                    "length": chunk.length,
                    "metadata": chunk.metadata,
                    "citation": chunk.citation,
                }
                for chunk in self.chunks
            ],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RagIndex":
        """Restore an index from :meth:`to_dict` output."""
        name = payload.get("name") or "rag"
        idf = payload.get("idf") or {}
        avg_len = float(payload.get("avg_len") or 1.0)
        chunks: List[RagChunk] = []
        for entry in payload.get("chunks") or []:
            if not isinstance(entry, dict):
                continue
            chunks.append(
                RagChunk(
                    chunk_id=str(entry.get("chunk_id")),
                    doc_id=str(entry.get("doc_id")),
                    source=str(entry.get("source")),
                    text=str(entry.get("text")),
                    tokens=entry.get("tokens") if isinstance(entry.get("tokens"), dict) else {},
                    length=int(entry.get("length") or 0),
                    metadata=entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {},
                    citation=str(entry.get("citation") or ""),
                )
            )
        return cls(name=name, chunks=chunks, idf=idf, avg_len=avg_len)


class RagStore:
    """Filesystem-backed storage for per-owner RAG indexes."""

    def __init__(self, root: str):
        """Initialize the index store root directory."""
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _index_path(self, owner: str, name: str) -> str:
        """Build a safe path for an owner's named index file."""
        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", owner or "default")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "index")
        return os.path.join(self.root, safe_owner, f"{safe_name}.json")

    def _write_json_atomic(self, path: str, payload: Dict[str, Any]) -> None:
        tmp = f"{path}.tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

    def list_indexes(self, owner: str) -> List[Dict[str, Any]]:
        """List persisted index names and chunk counts for one owner."""
        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", owner or "default")
        owner_dir = os.path.join(self.root, safe_owner)
        if not os.path.isdir(owner_dir):
            return []
        results = []
        for filename in os.listdir(owner_dir):
            if not filename.endswith(".json") or filename.endswith(".dense.json"):
                continue
            path = os.path.join(owner_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                results.append(
                    {
                        "name": payload.get("name") or filename[:-5],
                        "chunks": len(payload.get("chunks") or []),
                    }
                )
            except Exception:
                continue
        return sorted(results, key=lambda item: item.get("name") or "")

    def save_index(self, owner: str, index: RagIndex) -> str:
        """Persist an index and return the file path used."""
        path = self._index_path(owner, index.name)
        self._write_json_atomic(path, index.to_dict())
        dense_artifact = ensure_dense_artifact(index)
        dense_path = dense_artifact_path_for_index(path)
        if dense_path:
            self._write_json_atomic(dense_path, dense_artifact.to_dict())
        return path

    def load_index(self, owner: str, name: str) -> Optional[RagIndex]:
        """Load an index or return ``None`` when missing/corrupt."""
        path = self._index_path(owner, name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            index = RagIndex.from_dict(payload)
        except Exception:
            return None
        dense_path = dense_artifact_path_for_index(path)
        if dense_path and os.path.exists(dense_path):
            try:
                with open(dense_path, "r", encoding="utf-8") as handle:
                    dense_payload = json.load(handle)
                if isinstance(dense_payload, dict):
                    attach_dense_artifact(index, DenseIndexArtifact.from_dict(dense_payload))
            except Exception:
                pass
        return index

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete a persisted index, returning success state."""
        path = self._index_path(owner, name)
        dense_path = dense_artifact_path_for_index(path)
        deleted = False
        for target in (path, dense_path):
            if not target or not os.path.exists(target):
                continue
            try:
                os.remove(target)
                deleted = True
            except Exception:
                return False
        return deleted
