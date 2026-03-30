"""Structured document extraction primitives used by ingestion and RAG.

The existing codebase mostly moved plain strings between the converter,
retrieval, and research layers. That was simple, but it discarded useful
layout signals such as page boundaries, table blocks, and heading context.

This module keeps the structure lightweight and JSON-friendly so it can be
carried through the rest of the system without introducing heavy dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def format_locator(
    *,
    page: Optional[int] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    block_index: Optional[int] = None,
    block_start: Optional[int] = None,
    block_end: Optional[int] = None,
) -> str:
    """Return a compact human-readable page/block locator string."""
    parts: List[str] = []
    start_page = page if page is not None else page_start
    end_page = page if page is not None else page_end
    if start_page is not None:
        if end_page is not None and end_page != start_page:
            parts.append(f"p.{start_page}-{end_page}")
        else:
            parts.append(f"p.{start_page}")
    start_block = block_index if block_index is not None else block_start
    end_block = block_index if block_index is not None else block_end
    if start_block is not None:
        if end_block is not None and end_block != start_block:
            parts.append(f"b.{start_block}-{end_block}")
        else:
            parts.append(f"b.{start_block}")
    return " ".join(parts)


def format_source_citation(source: str, locator: str = "") -> str:
    """Return a stable citation label that preserves source and locator."""
    clean_source = (source or "").strip() or "source"
    clean_locator = (locator or "").strip()
    if clean_locator:
        return f"{clean_source} [{clean_locator}]"
    return clean_source


@dataclass
class DocumentElement:
    """Structured fragment extracted from one document page or logical block."""

    element_id: str
    element_type: str
    text: str
    page: Optional[int] = None
    block_index: Optional[int] = None
    markdown: str = ""
    bbox: Optional[List[float]] = None
    caption: Optional[str] = None
    confidence: Optional[float] = None
    heading_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def normalized_text(self) -> str:
        """Return the best retrieval-friendly text for this element."""
        candidate = (self.markdown or "").strip()
        if candidate:
            return candidate
        return (self.text or "").strip()

    def locator(self) -> str:
        """Return the element locator in ``p.N b.M`` form when available."""
        return format_locator(page=self.page, block_index=self.block_index)

    def citation(self, source: str = "") -> str:
        """Return a full citation label that includes the source when known."""
        return format_source_citation(source, self.locator())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the element into a JSON-safe dictionary."""
        return {
            "element_id": self.element_id,
            "element_type": self.element_type,
            "text": self.text,
            "page": self.page,
            "block_index": self.block_index,
            "markdown": self.markdown,
            "bbox": list(self.bbox) if isinstance(self.bbox, list) else self.bbox,
            "caption": self.caption,
            "confidence": self.confidence,
            "heading_path": list(self.heading_path or []),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DocumentElement":
        """Rebuild an element from :meth:`to_dict` output."""
        return cls(
            element_id=str(payload.get("element_id") or ""),
            element_type=str(payload.get("element_type") or "paragraph"),
            text=str(payload.get("text") or ""),
            page=_safe_int(payload.get("page")),
            block_index=_safe_int(payload.get("block_index")),
            markdown=str(payload.get("markdown") or ""),
            bbox=list(payload.get("bbox")) if isinstance(payload.get("bbox"), list) else None,
            caption=str(payload.get("caption")) if payload.get("caption") is not None else None,
            confidence=_safe_float(payload.get("confidence")),
            heading_path=[str(item) for item in (payload.get("heading_path") or []) if str(item).strip()],
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass
class DocumentExtraction:
    """Structured output from one file conversion pass."""

    source: str
    mime_type: str = ""
    text: str = ""
    elements: List[DocumentElement] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def render_text(
        self,
        *,
        include_page_markers: bool = True,
        include_block_markers: bool = False,
    ) -> str:
        """Render structured elements back into readable plain text.

        ``convert()`` remains string-based for backward compatibility. This
        renderer keeps the output readable while optionally preserving locators
        for downstream citation-aware workflows.
        """
        if self.text.strip():
            return self.text.strip()
        lines: List[str] = []
        current_page: Optional[int] = None
        for element in self.elements:
            body = element.normalized_text()
            if not body:
                continue
            if include_page_markers and element.page is not None and element.page != current_page:
                current_page = element.page
                lines.append(f"[Page {current_page}]")
            if include_block_markers:
                locator = element.locator()
                marker_parts = [part for part in [locator, element.element_type] if part]
                if marker_parts:
                    lines.append(f"[{' | '.join(marker_parts)}]")
            lines.append(body)
            lines.append("")
        return "\n".join(lines).strip()

    def page_count(self) -> int:
        """Return the maximum known page number or ``0`` when none exist."""
        pages = [element.page for element in self.elements if element.page is not None]
        if pages:
            return max(pages)
        try:
            return int(self.metadata.get("page_count") or 0)
        except Exception:
            return 0

    def locator_summary(self) -> str:
        """Return a short locator hint useful for bibliographies or logs."""
        count = self.page_count()
        if count > 1:
            return f"pages 1-{count}"
        if count == 1:
            return "page 1"
        return ""

    def summary_metadata(self) -> Dict[str, Any]:
        """Return compact metadata safe to persist with the retrieval index."""
        summary = dict(self.metadata or {})
        summary.setdefault("mime_type", self.mime_type)
        summary.setdefault("page_count", self.page_count())
        summary.setdefault("element_count", len(self.elements))
        locator_summary = self.locator_summary()
        if locator_summary:
            summary.setdefault("locator_summary", locator_summary)
        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the extraction into a JSON-safe dictionary."""
        return {
            "source": self.source,
            "mime_type": self.mime_type,
            "text": self.text,
            "elements": [element.to_dict() for element in self.elements],
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DocumentExtraction":
        """Rebuild an extraction from :meth:`to_dict` output."""
        raw_elements = payload.get("elements") or []
        elements = []
        for entry in raw_elements:
            if isinstance(entry, dict):
                elements.append(DocumentElement.from_dict(entry))
        return cls(
            source=str(payload.get("source") or ""),
            mime_type=str(payload.get("mime_type") or ""),
            text=str(payload.get("text") or ""),
            elements=elements,
            metadata=dict(payload.get("metadata") or {}),
        )


def coerce_document_elements(raw: Iterable[Any]) -> List[DocumentElement]:
    """Coerce dictionaries or elements into a clean ``DocumentElement`` list."""
    elements: List[DocumentElement] = []
    for item in raw or []:
        if isinstance(item, DocumentElement):
            elements.append(item)
        elif isinstance(item, dict):
            try:
                elements.append(DocumentElement.from_dict(item))
            except Exception:
                continue
    return elements
