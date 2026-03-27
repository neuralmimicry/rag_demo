"""Minimal local python-docx compatibility shim for test environments."""

from __future__ import annotations


class _Paragraph:
    def __init__(self, text: str = ""):
        self.text = text


class Document:
    def __init__(self, path: str | None = None):
        self.paragraphs = []
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
                for line in text.splitlines():
                    self.paragraphs.append(_Paragraph(line))
            except Exception:
                self.paragraphs = []

    def add_paragraph(self, text: str = "") -> _Paragraph:
        para = _Paragraph(text)
        self.paragraphs.append(para)
        return para

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(p.text for p in self.paragraphs))
