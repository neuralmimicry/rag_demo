from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Third-party libraries
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document
except ImportError:
    Document = None

try:
    from odf.opendocument import load
except ImportError:
    load = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from refiner.document_schema import DocumentElement, DocumentExtraction
from refiner.llm_providers import LLMProvider

logger = logging.getLogger(__name__)


class FileConverter:
    """Convert local files into text while preserving useful document structure.

    ``convert()`` remains intentionally string-based because large parts of the
    codebase still expect plain text. The richer ``extract()`` API sits under it
    and produces structured blocks with page and block locators for callers that
    can take advantage of them.
    """

    PDF_LOW_TEXT_CHARS = 140
    PDF_MAX_VISION_PAGES = 4
    PDF_MAX_IMAGES_PER_PAGE = 1
    PDF_MAX_IMAGE_BYTES = 2_500_000

    def __init__(self, llm: Optional[LLMProvider] = None, llm_params: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.llm_params = llm_params or {}

    def convert(self, file_path: str, mime_type: Optional[str] = None) -> str:
        """Convert one file into text while keeping the old return type stable."""
        extraction = self.extract(file_path, mime_type=mime_type)
        preview = (extraction.text or "")[:500]
        logger.debug("Converted content (first 500 chars): %s...", preview)
        return extraction.text

    def extract(self, file_path: str, mime_type: Optional[str] = None) -> DocumentExtraction:
        """Return structured extraction output for one file.

        The method is defensive by design:
        - unsupported dependencies return a readable error string instead of
          raising into unrelated workflows,
        - PDF extraction prefers cheap text extraction first and only uses the
          optional image-backed fallback on pages that look text-poor,
        - every successful extraction emits stable page/block locators.
        """
        if not os.path.exists(file_path):
            return self._error_extraction(file_path, mime_type, f"Error: File not found at {file_path}")

        resolved_mime = mime_type or mimetypes.guess_type(file_path)[0] or ""
        ext = os.path.splitext(file_path)[1].lower()
        logger.info("Converting file: %s (MIME: %s, Ext: %s)", file_path, resolved_mime, ext)

        try:
            if ext in (".txt", ".md"):
                extraction = self._extract_text_file(file_path, resolved_mime)
            elif ext == ".pdf":
                extraction = self._extract_pdf_file(file_path, resolved_mime or "application/pdf")
            elif ext == ".docx":
                extraction = self._extract_docx_file(file_path, resolved_mime)
            elif ext in (".odf", ".odt"):
                extraction = self._extract_odf_file(file_path, resolved_mime)
            elif ext == ".html" or ("html" in resolved_mime):
                extraction = self._extract_html_file(file_path, resolved_mime or "text/html")
            elif ext in (".jpg", ".jpeg", ".png", ".svg") or ("image" in resolved_mime):
                extraction = self._extract_image_file(file_path, resolved_mime or "image/jpeg")
            elif ext in (".mp3", ".mp4") or any(token in resolved_mime for token in ("audio", "video")):
                extraction = self._extract_audio_file(file_path, resolved_mime or "application/octet-stream")
            else:
                extraction = self._extract_text_file(file_path, resolved_mime or "text/plain")
        except Exception as exc:
            logger.error("Failed to convert %s: %s", file_path, exc)
            return self._error_extraction(file_path, resolved_mime, f"Error converting {file_path}: {str(exc)}")

        extraction.source = file_path
        extraction.mime_type = extraction.mime_type or resolved_mime
        extraction.metadata.setdefault("source_path", file_path)
        extraction.metadata.setdefault("source_name", os.path.basename(file_path) or file_path)
        if not (extraction.text or "").strip():
            extraction.text = extraction.render_text(include_page_markers=True)
        return extraction

    def _error_extraction(self, file_path: str, mime_type: Optional[str], message: str) -> DocumentExtraction:
        return DocumentExtraction(
            source=file_path,
            mime_type=mime_type or "",
            text=message,
            elements=[],
            metadata={"failed": True, "error": message, "page_count": 0, "element_count": 0},
        )

    def _read_text(self, file_path: str) -> str:
        return self._extract_text_file(file_path, mimetypes.guess_type(file_path)[0] or "text/plain").text

    def _read_pdf(self, file_path: str) -> str:
        return self._extract_pdf_file(file_path, "application/pdf").text

    def _read_docx(self, file_path: str) -> str:
        return self._extract_docx_file(file_path, mimetypes.guess_type(file_path)[0] or "").text

    def _read_odf(self, file_path: str) -> str:
        return self._extract_odf_file(file_path, mimetypes.guess_type(file_path)[0] or "").text

    def _read_html(self, file_path: str) -> str:
        return self._extract_html_file(file_path, mimetypes.guess_type(file_path)[0] or "text/html").text

    def _describe_image(self, file_path: str, mime_type: str) -> str:
        return self._extract_image_file(file_path, mime_type or "image/jpeg").text

    def _transcribe_audio(self, file_path: str) -> str:
        return self._extract_audio_file(file_path, mimetypes.guess_type(file_path)[0] or "").text

    def _extract_text_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read()
        elements, _ = self._elementize_blocks(content, page=1, confidence=0.99, source_kind="text")
        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            text=content.strip(),
            elements=elements,
            metadata={"page_count": 1, "extraction_mode": "text"},
        )
        if not extraction.text:
            extraction.text = extraction.render_text(include_page_markers=False)
        return extraction

    def _extract_pdf_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        if not PdfReader:
            return self._error_extraction(file_path, mime_type, "Error: pypdf not installed.")

        reader = PdfReader(file_path)
        elements: List[DocumentElement] = []
        heading_path: List[str] = []
        page_modes: List[str] = []
        image_pages = 0
        vision_pages = 0

        for page_number, page in enumerate(reader.pages, start=1):
            page_text = ""
            try:
                page_text = (page.extract_text() or "").strip()
            except Exception as exc:
                logger.debug("PDF text extraction failed for page %s: %s", page_number, exc)

            images = self._iter_pdf_page_images(page)
            if images:
                image_pages += 1
            low_text_density = self._looks_like_low_text_pdf_page(page_text)
            extraction_mode = "text"

            # Cheap text extraction is the default. Only fall back to image-backed
            # extraction on pages that look suspiciously empty or image-heavy.
            if low_text_density and images and vision_pages < self.PDF_MAX_VISION_PAGES:
                fallback_text = self._extract_text_from_pdf_page_images(images, page_number)
                if fallback_text:
                    page_text = fallback_text
                    extraction_mode = "vision_fallback"
                    vision_pages += 1

            page_elements, heading_path = self._elementize_blocks(
                page_text,
                page=page_number,
                heading_path=heading_path,
                confidence=0.72 if extraction_mode == "vision_fallback" else 0.96,
                source_kind=extraction_mode,
            )
            elements.extend(page_elements)

            # When the page still does not yield meaningful text, emit at least one
            # image element so callers know the page existed and why evidence may be thin.
            if images and (not page_elements or extraction_mode == "vision_fallback"):
                image_summary = self._summarize_pdf_page_images(images, page_number)
                if image_summary:
                    elements.append(
                        DocumentElement(
                            element_id=f"p{page_number:04d}-b{len(page_elements) + 1:04d}",
                            element_type="image",
                            text=image_summary,
                            page=page_number,
                            block_index=len(page_elements) + 1,
                            markdown=image_summary,
                            confidence=0.62,
                            heading_path=list(heading_path or []),
                            metadata={
                                "source_kind": "image_summary",
                                "image_count": len(images),
                            },
                        )
                    )
            page_modes.append(extraction_mode)

        extraction_mode = "mixed" if len(set(page_modes)) > 1 else (page_modes[0] if page_modes else "text")
        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            elements=elements,
            metadata={
                "page_count": len(reader.pages),
                "extraction_mode": extraction_mode,
                "image_page_count": image_pages,
                "vision_fallback_pages": vision_pages,
            },
        )
        extraction.text = extraction.render_text(include_page_markers=True, include_block_markers=False)
        return extraction

    def _extract_docx_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        if not Document:
            return self._error_extraction(file_path, mime_type, "Error: python-docx not installed.")

        doc = Document(file_path)
        elements: List[DocumentElement] = []
        heading_path: List[str] = []
        block_index = 1
        for para in getattr(doc, "paragraphs", []):
            text = (getattr(para, "text", "") or "").strip()
            if not text:
                continue
            style_name = str(getattr(getattr(para, "style", None), "name", "") or "")
            is_heading = style_name.lower().startswith("heading")
            inferred = "heading" if is_heading else self._classify_block(text)[0]
            level = self._heading_level_from_style(style_name) if is_heading else self._classify_block(text)[1]
            if inferred == "heading":
                heading_title = self._clean_heading_text(text)
                heading_path = self._update_heading_path(heading_path, heading_title, level or 2)
                markdown = f"{'#' * max(1, min(level or 2, 6))} {heading_title}"
            else:
                markdown = text
            elements.append(
                DocumentElement(
                    element_id=f"p0001-b{block_index:04d}",
                    element_type=inferred,
                    text=text,
                    page=1,
                    block_index=block_index,
                    markdown=markdown,
                    confidence=0.98,
                    heading_path=list(heading_path or []),
                    metadata={"source_kind": "text"},
                )
            )
            block_index += 1

        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            elements=elements,
            metadata={"page_count": 1, "extraction_mode": "text"},
        )
        extraction.text = extraction.render_text(include_page_markers=False)
        return extraction

    def _extract_odf_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        if not load:
            return self._error_extraction(file_path, mime_type, "Error: odfpy not installed.")
        textdoc = load(file_path)
        from odf import teletype

        content = teletype.extractText(textdoc.body)
        elements, _ = self._elementize_blocks(content, page=1, confidence=0.96, source_kind="text")
        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            text=content.strip(),
            elements=elements,
            metadata={"page_count": 1, "extraction_mode": "text"},
        )
        if not extraction.text:
            extraction.text = extraction.render_text(include_page_markers=False)
        return extraction

    def _extract_html_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        if not BeautifulSoup:
            return self._extract_text_file(file_path, mime_type or "text/html")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            soup = BeautifulSoup(handle, "html.parser")
        for script in soup(["script", "style"]):
            script.decompose()

        elements: List[DocumentElement] = []
        heading_path: List[str] = []
        block_index = 1
        nodes = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "img"])
        for node in nodes:
            text = ""
            markdown = ""
            element_type = "paragraph"
            level: Optional[int] = None
            if node.name and re.fullmatch(r"h[1-6]", node.name):
                level = int(node.name[1])
                text = node.get_text(" ", strip=True)
                text = self._clean_heading_text(text)
                element_type = "heading"
                heading_path = self._update_heading_path(heading_path, text, level)
                markdown = f"{'#' * level} {text}"
            elif node.name == "table":
                text = self._html_table_to_text(node)
                markdown = self._html_table_to_markdown(node)
                element_type = "table"
            elif node.name == "img":
                alt_text = (node.get("alt") or "").strip()
                if not alt_text:
                    continue
                text = alt_text
                markdown = alt_text
                element_type = "image"
            else:
                text = node.get_text(" ", strip=True)
                markdown = text
                element_type = "list" if node.name == "li" else "paragraph"
            if not text:
                continue
            elements.append(
                DocumentElement(
                    element_id=f"p0001-b{block_index:04d}",
                    element_type=element_type,
                    text=text,
                    page=1,
                    block_index=block_index,
                    markdown=markdown,
                    confidence=0.97,
                    heading_path=list(heading_path or []),
                    metadata={"source_kind": "html"},
                )
            )
            block_index += 1

        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            elements=elements,
            metadata={"page_count": 1, "extraction_mode": "html"},
        )
        extraction.text = extraction.render_text(include_page_markers=False)
        if not extraction.text:
            extraction.text = soup.get_text(separator="\n", strip=True)
        return extraction

    def _extract_image_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        content = self._describe_image_text(file_path, mime_type or "image/jpeg")
        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            text=content,
            elements=[
                DocumentElement(
                    element_id="p0001-b0001",
                    element_type="image",
                    text=content,
                    page=1,
                    block_index=1,
                    markdown=content,
                    confidence=0.65 if self.llm else 1.0,
                    metadata={"source_kind": "vision" if self.llm else "placeholder"},
                )
            ],
            metadata={"page_count": 1, "extraction_mode": "vision" if self.llm else "placeholder"},
        )
        return extraction

    def _extract_audio_file(self, file_path: str, mime_type: str) -> DocumentExtraction:
        content = self._transcribe_audio_text(file_path)
        extraction = DocumentExtraction(
            source=file_path,
            mime_type=mime_type,
            text=content,
            elements=[
                DocumentElement(
                    element_id="p0001-b0001",
                    element_type="transcript",
                    text=content,
                    page=1,
                    block_index=1,
                    markdown=content,
                    confidence=0.70 if self.llm else 1.0,
                    metadata={"source_kind": "audio"},
                )
            ],
            metadata={"page_count": 1, "extraction_mode": "audio"},
        )
        return extraction

    def _elementize_blocks(
        self,
        text: str,
        *,
        page: Optional[int] = None,
        starting_block: int = 1,
        heading_path: Optional[List[str]] = None,
        confidence: float = 0.95,
        source_kind: str = "text",
    ) -> Tuple[List[DocumentElement], List[str]]:
        """Split text into structural blocks and infer lightweight layout types."""
        normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return [], list(heading_path or [])

        blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
        elements: List[DocumentElement] = []
        current_heading_path = list(heading_path or [])
        block_index = starting_block
        for block in blocks:
            element_type, level, cleaned_text, markdown = self._classify_block(block)
            if not cleaned_text:
                continue
            if element_type == "heading":
                current_heading_path = self._update_heading_path(current_heading_path, self._clean_heading_text(cleaned_text), level or 2)
            elements.append(
                DocumentElement(
                    element_id=f"p{(page or 1):04d}-b{block_index:04d}",
                    element_type=element_type,
                    text=cleaned_text,
                    page=page,
                    block_index=block_index,
                    markdown=markdown,
                    confidence=confidence,
                    heading_path=list(current_heading_path or []),
                    metadata={"source_kind": source_kind},
                )
            )
            block_index += 1
        return elements, current_heading_path

    def _classify_block(self, block: str) -> Tuple[str, Optional[int], str, str]:
        """Infer a coarse structural type for one logical block of text."""
        raw = (block or "").strip()
        if not raw:
            return "paragraph", None, "", ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return "paragraph", None, "", ""

        first = lines[0]
        if first.startswith("#"):
            level = max(1, min(len(first) - len(first.lstrip("#")), 6))
            title = self._clean_heading_text(first[level:].strip())
            return "heading", level, title, f"{'#' * level} {title}"

        if self._looks_like_table(lines):
            markdown = self._table_lines_to_markdown(lines)
            return "table", None, "\n".join(lines), markdown

        if self._looks_like_list(lines):
            markdown = "\n".join(line if re.match(r"^[-*+]|\d+\.", line) else f"- {line}" for line in lines)
            return "list", None, "\n".join(lines), markdown

        if self._looks_like_heading(lines):
            level = self._infer_heading_level(first)
            title = self._clean_heading_text(first)
            return "heading", level, title, f"{'#' * max(1, min(level or 2, 6))} {title}"

        cleaned = "\n".join(lines)
        return "paragraph", None, cleaned, cleaned

    def _looks_like_heading(self, lines: List[str]) -> bool:
        if not lines:
            return False
        if len(lines) > 2:
            return False
        candidate = lines[0].strip()
        words = [word for word in re.split(r"\s+", candidate) if word]
        if not words or len(candidate) > 140 or len(words) > 14:
            return False
        if re.match(r"^\d+(?:\.\d+)*\s+\S+", candidate):
            return True
        alpha_chars = [char for char in candidate if char.isalpha()]
        if alpha_chars and candidate.upper() == candidate and len(alpha_chars) >= 4:
            return True
        if candidate.endswith(":") and len(words) <= 10 and "." not in candidate[:-1]:
            return True
        title_case_words = sum(1 for word in words if word[:1].isupper())
        return len(words) <= 8 and title_case_words >= max(2, len(words) - 1)

    def _infer_heading_level(self, text: str) -> int:
        match = re.match(r"^(\d+(?:\.\d+)*)\s+\S+", text.strip())
        if match:
            return min(match.group(1).count(".") + 1, 6)
        return 2

    def _heading_level_from_style(self, style_name: str) -> int:
        match = re.search(r"(\d+)", style_name or "")
        if match:
            return max(1, min(int(match.group(1)), 6))
        return 2

    def _clean_heading_text(self, text: str) -> str:
        cleaned = re.sub(r"^#+\s*", "", (text or "").strip())
        return cleaned.rstrip(":").strip()

    def _update_heading_path(self, current: List[str], title: str, level: int) -> List[str]:
        """Maintain a stable heading stack for downstream chunking."""
        clean_title = self._clean_heading_text(title)
        if not clean_title:
            return list(current or [])
        bounded_level = max(1, min(level or 2, 6))
        next_path = list(current[:bounded_level - 1])
        next_path.append(clean_title)
        return next_path

    def _looks_like_list(self, lines: List[str]) -> bool:
        if len(lines) < 2:
            return False
        matches = sum(1 for line in lines if re.match(r"^[-*+]|\d+\.", line))
        return matches >= max(2, len(lines) // 2)

    def _looks_like_table(self, lines: List[str]) -> bool:
        if len(lines) < 2:
            return False
        if sum(1 for line in lines if "|" in line) >= max(2, len(lines) - 1):
            return True
        column_counts = []
        for line in lines:
            cells = [cell.strip() for cell in re.split(r"\t+|\s{2,}", line.strip()) if cell.strip()]
            if len(cells) >= 2:
                column_counts.append(len(cells))
        return len(column_counts) >= 2 and len(set(column_counts)) <= 2

    def _table_lines_to_markdown(self, lines: List[str]) -> str:
        rows = [self._split_table_line(line) for line in lines]
        rows = [row for row in rows if len(row) >= 2]
        if len(rows) < 2:
            return "\n".join(lines)
        width = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (width - len(row)) for row in rows]
        header = "| " + " | ".join(normalized_rows[0]) + " |"
        separator = "| " + " | ".join(["---"] * width) + " |"
        body = ["| " + " | ".join(row) + " |" for row in normalized_rows[1:]]
        return "\n".join([header, separator] + body)

    def _split_table_line(self, line: str) -> List[str]:
        clean_line = line.strip().strip("|")
        if "|" in clean_line:
            return [cell.strip() for cell in clean_line.split("|")]
        return [cell.strip() for cell in re.split(r"\t+|\s{2,}", clean_line) if cell.strip()]

    def _html_table_to_text(self, table: Any) -> str:
        rows = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    def _html_table_to_markdown(self, table: Any) -> str:
        text_rows = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if cells:
                text_rows.append(cells)
        if len(text_rows) < 2:
            return "\n".join(" | ".join(row) for row in text_rows)
        width = max(len(row) for row in text_rows)
        normalized_rows = [row + [""] * (width - len(row)) for row in text_rows]
        header = "| " + " | ".join(normalized_rows[0]) + " |"
        separator = "| " + " | ".join(["---"] * width) + " |"
        body = ["| " + " | ".join(row) + " |" for row in normalized_rows[1:]]
        return "\n".join([header, separator] + body)

    def _looks_like_low_text_pdf_page(self, text: str) -> bool:
        stripped = (text or "").strip()
        if not stripped:
            return True
        alnum_count = sum(1 for char in stripped if char.isalnum())
        return alnum_count < self.PDF_LOW_TEXT_CHARS

    def _iter_pdf_page_images(self, page: Any) -> List[Dict[str, Any]]:
        """Extract lightweight image references from a PDF page when available."""
        raw_images = getattr(page, "images", None)
        if not raw_images:
            return []
        images: List[Dict[str, Any]] = []
        try:
            for idx, image in enumerate(raw_images):
                data = getattr(image, "data", None)
                if not isinstance(data, (bytes, bytearray)) or not data:
                    continue
                name = str(getattr(image, "name", "") or f"page-image-{idx + 1}.png")
                mime_type = mimetypes.guess_type(name)[0] or "image/png"
                images.append(
                    {
                        "name": name,
                        "data": bytes(data),
                        "mime_type": mime_type,
                    }
                )
        except Exception as exc:
            logger.debug("Failed to enumerate PDF page images: %s", exc)
            return []
        return images

    def _extract_text_from_pdf_page_images(self, images: List[Dict[str, Any]], page_number: int) -> str:
        """Use a vision-capable model to recover text from image-backed PDF pages."""
        if not self.llm or not images:
            return ""
        extracted_blocks: List[str] = []
        for image in images[: self.PDF_MAX_IMAGES_PER_PAGE]:
            data = image.get("data") or b""
            if not data or len(data) > self.PDF_MAX_IMAGE_BYTES:
                continue
            text = self._run_vision_prompt(
                data,
                image.get("mime_type") or "image/png",
                (
                    "This is an image extracted from a document page. Extract the readable text as "
                    "faithfully as possible. Preserve headings and simple tables in Markdown where "
                    "they are clear. Do not invent missing text."
                ),
                system=(
                    "You extract text from document images conservatively. Preserve structure when it "
                    "is obvious, admit uncertainty when text is unreadable, and avoid embellishment."
                ),
            )
            if text:
                extracted_blocks.append(text)
        if extracted_blocks:
            logger.info("Recovered text from image-backed PDF page %s using vision fallback", page_number)
        return "\n\n".join(block for block in extracted_blocks if block.strip()).strip()

    def _summarize_pdf_page_images(self, images: List[Dict[str, Any]], page_number: int) -> str:
        """Generate a short summary for charts/images on a PDF page when possible."""
        if not self.llm or not images:
            return ""
        descriptions = []
        for image in images[: self.PDF_MAX_IMAGES_PER_PAGE]:
            data = image.get("data") or b""
            if not data or len(data) > self.PDF_MAX_IMAGE_BYTES:
                continue
            description = self._run_vision_prompt(
                data,
                image.get("mime_type") or "image/png",
                (
                    "Describe this document image briefly for retrieval. Mention any visible chart, "
                    "diagram, table, or key annotation, and include visible text when relevant."
                ),
                system=(
                    "You describe document images conservatively for retrieval indexing. Prioritize "
                    "factual text, chart labels, table captions, and diagram meaning."
                ),
            )
            if description:
                descriptions.append(description)
        if descriptions:
            logger.debug("Generated image summary for PDF page %s", page_number)
        return "\n\n".join(desc for desc in descriptions if desc.strip()).strip()

    def _describe_image_text(self, file_path: str, mime_type: str) -> str:
        if not self.llm:
            return f"Image file: {os.path.basename(file_path)} (No LLM available for description)"
        with open(file_path, "rb") as handle:
            data = handle.read()
        description = self._run_vision_prompt(
            data,
            mime_type or "image/jpeg",
            (
                "Describe this image in detail, focusing on information relevant to technical research. "
                "Extract visible text, charts, diagrams, and other salient visual elements. "
                "Use British English."
            ),
            system=(
                "You are a conservative, reserved British professional technical writer and assistant. "
                "Describe images for research with strict factual accuracy."
            ),
        )
        if not description:
            return f"Image file: {os.path.basename(file_path)} (Description unavailable)"
        return f"--- Image Description: {os.path.basename(file_path)} ---\n{description}"

    def _transcribe_audio_text(self, file_path: str) -> str:
        if not self.llm:
            return f"Audio/Video file: {os.path.basename(file_path)} (No LLM available for transcription)"
        logger.info("Transcribing audio/video: %s", file_path)
        try:
            transcript = self.llm.transcribe(file_path, timeout=self.llm_params.get("timeout"))
            return f"--- Transcription: {os.path.basename(file_path)} ---\n{transcript}"
        except NotImplementedError:
            return f"Transcription not supported by current LLM provider ({self.llm.name})."
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
            return f"Error transcribing {os.path.basename(file_path)}: {str(exc)}"

    def _run_vision_prompt(self, image_bytes: bytes, mime_type: str, prompt: str, *, system: str) -> str:
        if not self.llm or not image_bytes:
            return ""
        try:
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}},
                    ],
                }
            ]
            response = self.llm.predict(messages, system=system, **self.llm_params)
            return (response.text or "").strip()
        except Exception as exc:
            logger.debug("Vision prompt failed: %s", exc)
            return ""
