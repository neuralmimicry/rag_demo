"""Subset of odf.opendocument used by tests and file conversion."""

from __future__ import annotations

from .text import P


class _Container:
    def __init__(self):
        self.elements = []

    def addElement(self, element) -> None:
        self.elements.append(element)


class OpenDocumentText:
    def __init__(self):
        self.text = _Container()
        self.body = self.text

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(getattr(el, "text", str(el)) for el in self.text.elements))


class _LoadedDocument:
    def __init__(self, body: _Container):
        self.body = body


def load(path: str) -> _LoadedDocument:
    body = _Container()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except Exception:
        text = ""
    for line in text.splitlines():
        body.addElement(P(text=line))
    return _LoadedDocument(body)
