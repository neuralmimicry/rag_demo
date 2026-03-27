"""Small, dependency-free RAG primitives for local document retrieval.

The implementation uses:
- character-window chunking with overlap,
- lightweight tokenization, and
- BM25-style ranking over in-memory chunks.

Indexes are persisted as JSON so they can be managed per user/workspace by the
web API without requiring an external vector database.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from collections import Counter


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


@dataclass
class RagMatch:
    """Search result emitted by :meth:`RagIndex.search`."""

    chunk_id: str
    source: str
    score: float
    text: str
    metadata: Dict[str, Any]


class RagIndex:
    """In-memory BM25-like index built from :class:`RagDocument` entries."""

    def __init__(self, name: str, chunks: List[RagChunk], idf: Dict[str, float], avg_len: float):
        self.name = name
        self.chunks = chunks
        self.idf = idf
        self.avg_len = avg_len or 1.0

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
            for idx, chunk_text_item in enumerate(chunk_text(doc.text, chunk_size, chunk_overlap), start=1):
                tokens = Counter(_tokenize(chunk_text_item))
                if not tokens:
                    continue
                chunk_id = f"{doc.doc_id}:{idx:04d}"
                chunks.append(
                    RagChunk(
                        chunk_id=chunk_id,
                        doc_id=doc.doc_id,
                        source=doc.source,
                        text=chunk_text_item,
                        tokens=dict(tokens),
                        length=sum(tokens.values()),
                        metadata=dict(doc.metadata or {}),
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

    def list_indexes(self, owner: str) -> List[Dict[str, Any]]:
        """List persisted index names and chunk counts for one owner."""
        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", owner or "default")
        owner_dir = os.path.join(self.root, safe_owner)
        if not os.path.isdir(owner_dir):
            return []
        results = []
        for filename in os.listdir(owner_dir):
            if not filename.endswith(".json"):
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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(index.to_dict(), handle)
        return path

    def load_index(self, owner: str, name: str) -> Optional[RagIndex]:
        """Load an index or return ``None`` when missing/corrupt."""
        path = self._index_path(owner, name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return RagIndex.from_dict(payload)
        except Exception:
            return None

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete a persisted index, returning success state."""
        path = self._index_path(owner, name)
        if not os.path.exists(path):
            return False
        try:
            os.remove(path)
            return True
        except Exception:
            return False
